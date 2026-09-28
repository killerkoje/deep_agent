"""Sub-agents run as `claude -p` processes.

Uses the Claude Code login already on the machine, so there is no API
key to supply and no per-token bill. It also solves the thing that
blocked `implement`: Claude Code ships Read/Write/Edit/Bash/Glob/Grep,
so a sub-agent can touch a real repo without us building those tools.

Isolation gets stronger, not weaker. Each spawn is a separate OS
process with its own context, started in the staged directory that
holds only the skill's declared inputs. Nothing leaks between spawns
because there is nothing shared to leak through.

Three things to know before relying on it:

- A seat subscription is licensed for interactive use. Driving it from
  a server is the operator's call to make, and it is theirs to make.
- Subscription rate limits are per-window, not per-request. A run with
  a dozen spawns can exhaust one and stall for hours; an API 429 you
  can just retry.
- Every invocation re-sends Claude Code's own system prompt and tool
  definitions. A four-character reply measured $0.0996 of reported
  cost, nearly all of it cache. Under a subscription nothing is
  charged, but the number is what an API-billed run would have cost.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any

from .base import Backend, Credentials, SubagentResult

DEFAULT_TIMEOUT = 900  # a real implement pass is minutes, not seconds

# Our skill contracts name tools in our vocabulary; Claude Code has its
# own. A skill that only reads and writes documents must not be handed
# Bash - the narrower the surface, the less there is to go wrong.
_TOOL_MAP = {
    "ls": ("Glob",),
    "read_file": ("Read",),
    "write_file": ("Write", "Edit"),
}
_REPO_SKILL_TOOLS = ("Read", "Write", "Edit", "Glob", "Grep", "Bash")

# Aliases the CLI accepts, plus full ids. Prices are first-party API
# rates - under a subscription nothing is charged, but they are what
# the same work would have cost, which is the number worth comparing.
_MODELS = [
    {
        "id": "fable",
        "provider": "claude-cli",
        "display_name": "Claude Fable 5",
        "input_per_1m": 10.0,
        "output_per_1m": 50.0,
        "notes": (
            "Most capable. Thinking cannot be turned off, so every call pays "
            "reasoning tokens - wasteful on high-frequency work. Keep it for a "
            "judgment no cheaper model reached."
        ),
    },
    {
        "id": "opus",
        "provider": "claude-cli",
        "display_name": "Claude Opus 5",
        "input_per_1m": 5.0,
        "output_per_1m": 25.0,
        "notes": (
            "Strong. Worth it where a miss is silent rather than loud: reading "
            "adversarially across distant sections, spotting an assumption that "
            "reads as true, holding one codebase consistent."
        ),
    },
    {
        "id": "sonnet",
        "provider": "claude-cli",
        "display_name": "Claude Sonnet 5",
        "input_per_1m": 3.0,
        "output_per_1m": 15.0,
        "notes": "Balanced. Good default for formulaic but long work.",
    },
    {
        "id": "haiku",
        "provider": "claude-cli",
        "display_name": "Claude Haiku 4.5",
        "input_per_1m": 1.0,
        "output_per_1m": 5.0,
        "notes": "Cheapest and fastest. Constrained decisions over summarized state.",
    },
]


class ClaudeCliBackend(Backend):
    name = "claude-cli"

    def __init__(self, binary: str = "claude", timeout: int = DEFAULT_TIMEOUT):
        self.binary = binary
        self.timeout = timeout

    # --- capability ---------------------------------------------------

    def available(self, creds: Credentials | None = None) -> str | None:
        if shutil.which(self.binary) is None:
            return f"{self.binary} is not on PATH - install Claude Code"
        if creds and creds.oauth_token:
            return None
        if os.getenv("CLAUDE_CODE_OAUTH_TOKEN"):
            return None
        home = os.path.expanduser("~/.claude.json")
        if not os.path.isfile(home):
            return "no Claude Code login found - run `claude` and sign in"
        return None

    def models(self) -> list[dict[str, Any]]:
        return [dict(m) for m in _MODELS]

    # --- running --------------------------------------------------------

    def run(
        self,
        *,
        system_prompt: str,
        brief: str,
        model: str,
        workdir: str,
        tool_names: tuple[str, ...] = (),
        creds: Credentials | None = None,
        repo: str | None = None,
    ) -> SubagentResult:
        argv = [
            self.binary,
            "-p",
            "--model", model,
            "--output-format", "json",
            "--permission-mode", "acceptEdits",  # it must write without asking
            "--append-system-prompt", system_prompt,
            "--allowedTools", *self._tools_for(tool_names, repo),
        ]
        if repo:
            argv += ["--add-dir", repo]

        env = dict(os.environ)
        if creds and creds.oauth_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = creds.oauth_token

        try:
            proc = subprocess.run(  # noqa: S603 - argv form, shell=False
                argv,
                cwd=workdir,
                input=brief,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                shell=False,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return SubagentResult(
                summary=f"(no summary - timed out after {self.timeout}s)",
                error="timeout",
            )
        except FileNotFoundError:
            return SubagentResult(
                summary="(no summary - claude not found)", error="binary missing"
            )

        return self._parse(proc.stdout, proc.stderr, proc.returncode)

    # --- helpers ---------------------------------------------------------

    def _tools_for(self, tool_names: tuple[str, ...], repo: str | None) -> list[str]:
        if repo:
            return list(_REPO_SKILL_TOOLS)
        allowed: list[str] = []
        for name in tool_names:
            allowed += [t for t in _TOOL_MAP.get(name, ()) if t not in allowed]
        return allowed or ["Read"]

    def _parse(self, stdout: str, stderr: str, code: int) -> SubagentResult:
        try:
            data = json.loads(stdout)
        except (json.JSONDecodeError, ValueError):
            tail = (stderr or stdout or "").strip()[-400:]
            return SubagentResult(
                summary=f"(no summary - unparseable CLI output) {tail}",
                error=f"exit {code}",
            )

        usage = data.get("usage") or {}
        model_usage = data.get("modelUsage") or {}
        canonical = next(iter(model_usage), None)

        # Cache reads are input tokens the API would have billed at a
        # discount; folding them in keeps the comparison with the API
        # backend honest rather than flattering.
        tokens_in = (
            int(usage.get("input_tokens", 0))
            + int(usage.get("cache_read_input_tokens", 0))
            + int(usage.get("cache_creation_input_tokens", 0))
        )

        summary = (data.get("result") or "").strip()
        if not summary:
            summary = "(no summary returned)"

        return SubagentResult(
            summary=summary,
            tokens_in=tokens_in,
            tokens_out=int(usage.get("output_tokens", 0)),
            cost_usd=float(data.get("total_cost_usd") or 0.0),
            model=canonical,
            turns=int(data.get("num_turns") or 0),
            error=None if not data.get("is_error") else data.get("stop_reason"),
            raw={
                "session_id": data.get("session_id"),
                "permission_denials": data.get("permission_denials"),
                "subagent_stats": data.get("subagent_stats"),
            },
        )
