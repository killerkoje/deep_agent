"""Subprocess wrapper for the SDD CLIs.

What these CLIs actually are matters for the design: `openspec` and
`specify` do NOT call an LLM. They scaffold, they emit instructions for
an agent to execute, and they validate. The hole-finding stays with our
sub-agent (SPEC 10.2.1).

The payoff is `openspec validate --strict --json`: a deterministic
verdict with an exit code, not a model saying it looks fine. That makes
it usable as a gate.

Everything runs through an allowlist. An LLM-authored string never
reaches a shell: no shell=True, arguments are validated against a
per-command whitelist, and anything else is refused.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TIMEOUT = 120

# Only these binaries, only these subcommands. Extending this list is a
# deliberate act, not something a prompt can talk us into.
ALLOWLIST: dict[str, set[str]] = {
    "openspec": {"init", "instructions", "validate", "status", "list", "show"},
    "specify": {"init", "check"},
    "npx": {"playwright"},  # qa only; further constrained below
    "git": {"status", "diff", "add", "commit", "checkout", "branch", "rev-parse"},
}

# Flags are matched, not free text. `--json` yes; `--output=;rm -rf /` no.
_SAFE_ARG = re.compile(r"^[A-Za-z0-9._:/@=-]+$")


class ShellRefused(RuntimeError):
    pass


@dataclass
class CliResult:
    ok: bool
    exit_code: int
    stdout: str
    stderr: str

    def json(self) -> dict:
        try:
            return json.loads(self.stdout)
        except (json.JSONDecodeError, ValueError):
            return {}


def available(binary: str) -> bool:
    return shutil.which(binary) is not None


def _check(argv: list[str]) -> None:
    if not argv:
        raise ShellRefused("empty command")

    binary, *rest = argv
    if binary not in ALLOWLIST:
        raise ShellRefused(f"{binary!r} is not allowlisted")
    if not rest or rest[0] not in ALLOWLIST[binary]:
        sub = rest[0] if rest else "(none)"
        raise ShellRefused(f"{binary} {sub!r} is not allowlisted")

    for arg in rest:
        if not _SAFE_ARG.match(arg):
            raise ShellRefused(f"unsafe argument: {arg!r}")


def run(argv: list[str], cwd: str | Path | None = None, timeout: int = DEFAULT_TIMEOUT) -> CliResult:
    """Run an allowlisted command. Never uses a shell."""
    _check(argv)
    try:
        proc = subprocess.run(  # noqa: S603 - argv form, shell=False
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except FileNotFoundError:
        return CliResult(False, 127, "", f"{argv[0]} not installed")
    except subprocess.TimeoutExpired:
        return CliResult(False, 124, "", f"timed out after {timeout}s")

    return CliResult(proc.returncode == 0, proc.returncode, proc.stdout, proc.stderr)


# --- openspec ---------------------------------------------------------


def openspec_init(cwd: str | Path) -> CliResult:
    return run(["openspec", "init", "."], cwd=cwd)


def openspec_instructions(artifact: str, cwd: str | Path) -> CliResult:
    """Pull the prompt the CLI wants an agent to execute."""
    return run(["openspec", "instructions", artifact, "--json"], cwd=cwd)


def openspec_validate(cwd: str | Path, strict: bool = True) -> CliResult:
    argv = ["openspec", "validate", "--all", "--json"]
    if strict:
        argv.append("--strict")
    return run(argv, cwd=cwd)


def openspec_status(cwd: str | Path) -> CliResult:
    return run(["openspec", "status", "--json"], cwd=cwd)


# --- spec-kit ---------------------------------------------------------


def specify_check() -> CliResult:
    return run(["specify", "check"])
