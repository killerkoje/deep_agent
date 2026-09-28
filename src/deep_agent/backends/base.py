"""What a sub-agent backend has to provide.

A backend runs one isolated session in a directory and returns a
summary. It does not know about gates, skills, or the pipeline - those
sit above it, which is what makes the backend swappable.

Two exist:

  langgraph   an API key, a model id, our own file tools
  claude-cli  the Claude Code subscription, its own built-in tools

The second is why this abstraction exists at all. The deployed version
is bring-your-own-credentials: a user arrives with a Claude login or an
OpenAI key, and the same pipeline has to run on whichever they brought.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class SubagentResult:
    """Everything that crosses back to Main. Summary, not transcript."""

    summary: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    model: str | None = None  # what actually ran, as the backend reports it
    turns: int = 0
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)  # audit log only


@dataclass(frozen=True)
class Credentials:
    """Whatever this caller brought.

    Never stored in AgentState. State is checkpointed to disk and later
    to Postgres, and a checkpoint that carries an API key is a leak
    waiting for someone to read a backup.
    """

    provider: str  # "openai" | "anthropic" | "claude-cli"
    api_key: str | None = None
    oauth_token: str | None = None

    def redacted(self) -> str:
        if self.api_key:
            return f"{self.provider}:key:…{self.api_key[-4:]}"
        if self.oauth_token:
            return f"{self.provider}:oauth:…{self.oauth_token[-4:]}"
        return f"{self.provider}:ambient"


class Backend(Protocol):
    """One isolated session, one summary back."""

    name: str

    def available(self, creds: Credentials | None) -> str | None:
        """None if usable, otherwise why not - shown to the caller."""

    def models(self) -> list[dict[str, Any]]:
        """What this backend can be asked for. Capability notes only:
        never a skill-to-model mapping (README 0.2)."""

    def run(
        self,
        *,
        system_prompt: str,
        brief: str,
        model: str,
        workdir: str,
        tool_names: tuple[str, ...],
        creds: Credentials | None = None,
        repo: str | None = None,
    ) -> SubagentResult: ...
