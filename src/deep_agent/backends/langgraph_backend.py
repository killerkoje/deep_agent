"""Sub-agents as an in-process LangGraph subgraph, on an API key.

The original path, now behind the same interface as the CLI backend.
Isolation here comes from SubAgentState being a different State class
with its own `messages` channel; in the CLI backend it comes from a
separate OS process. Same guarantee, different mechanism.
"""

from __future__ import annotations

from typing import Any

from ..llm import _price, get_chat_model
from .base import Backend, Credentials, SubagentResult


class LangGraphBackend(Backend):
    name = "langgraph"

    def __init__(self, model_factory=None):
        # Tests inject scripted models here.
        self.model_factory = model_factory

    def available(self, creds: Credentials | None = None) -> str | None:
        if self.model_factory is not None:
            return None  # tests drive scripted models

        if creds is not None:
            # A caller who supplies credentials must be usable on their
            # own. Falling back to the server's key here would run their
            # work on the operator's account and bill the operator for
            # it - and nothing in the response would say so.
            if creds.api_key or creds.oauth_token:
                return None
            return (
                f"provider {creds.provider!r} needs an api_key or oauth_token; "
                "the server will not run your work on its own credentials"
            )

        from ..config import settings

        if settings.openai_api_key:
            return None
        return "no API key - set OPENAI_API_KEY or pass one per request"

    def models(self) -> list[dict[str, Any]]:
        from ..models_catalog import spawnable_models

        return spawnable_models()

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
        from ..subagent import run_subagent
        from ..tools import fs

        chat = None
        if self.model_factory is not None:
            chat = self.model_factory(model)
        elif creds and creds.api_key:
            chat = get_chat_model(model, api_key=creds.api_key)

        out = run_subagent(
            system_prompt=system_prompt,
            brief=brief,
            model_id=model,
            tools=fs.resolve(tool_names),
            workdir=workdir,
            repo=repo,
            model=chat,
        )
        return SubagentResult(
            summary=out["summary"],
            tokens_in=out["tokens_in"],
            tokens_out=out["tokens_out"],
            cost_usd=_price(model, out["tokens_in"], out["tokens_out"]),
            model=model,
            turns=out["turns"],
        )
