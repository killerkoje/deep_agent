"""Model access + token/cost accounting.

Accounting is wired in from S1 on purpose. A subagent harness spends
tokens at a multiple of a single-agent loop, and a cost you never
measured is a cost you cannot argue about later (README 12).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel

from .config import settings
from .models_catalog import all_models


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    calls: int = 0
    by_model: dict[str, dict[str, float]] = field(default_factory=dict)

    def add(self, model: str, tokens_in: int, tokens_out: int) -> None:
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.calls += 1
        cost = _price(model, tokens_in, tokens_out)
        self.cost_usd += cost
        slot = self.by_model.setdefault(
            model, {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "calls": 0}
        )
        slot["tokens_in"] += tokens_in
        slot["tokens_out"] += tokens_out
        slot["cost_usd"] += cost
        slot["calls"] += 1


def _price(model: str, tokens_in: int, tokens_out: int) -> float:
    for m in all_models():
        if m["id"] == model:
            pin = m.get("input_per_1m") or 0.0
            pout = m.get("output_per_1m") or 0.0
            return (tokens_in * pin + tokens_out * pout) / 1_000_000
    return 0.0


def usage_from_message(msg: Any) -> tuple[int, int]:
    meta = getattr(msg, "usage_metadata", None) or {}
    return int(meta.get("input_tokens", 0)), int(meta.get("output_tokens", 0))


def get_chat_model(model: str, reasoning_effort: str | None = None) -> BaseChatModel:
    """Build a chat model by id. Main passes its env-fixed model; spawn
    passes whatever Main chose for that sub-agent."""
    from langchain_openai import ChatOpenAI

    kwargs: dict[str, Any] = {"model": model, "api_key": settings.openai_api_key}
    if reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort
    return ChatOpenAI(**kwargs)


def get_main_model() -> BaseChatModel:
    """Main's model is env-fixed (SPEC Q1) - it runs on every loop turn and
    carries the largest accumulated context, so it is the biggest cost
    multiplier in the system. Sub-agent models stay Main's choice."""
    return get_chat_model(settings.main_model, settings.main_reasoning_effort)
