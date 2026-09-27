"""A scripted chat model.

Lets us drive the graph without an API key, and - more importantly -
assert exactly what lands in `messages`. The isolation test in S2 depends
on being able to count parent messages precisely.
"""

from __future__ import annotations

from typing import Any, Iterator, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class ScriptedChatModel(BaseChatModel):
    """Returns pre-built AIMessages in order, then a plain final answer.

    `seen` records the context it was handed on each call, so tests can
    check what Main actually saw (e.g. that todos were re-injected).
    """

    script: list[AIMessage] = []
    final_text: str = "done"

    _cursor: Iterator[AIMessage] = None  # type: ignore[assignment]
    seen: list[list[BaseMessage]] = []

    def __init__(self, script: Sequence[AIMessage], final_text: str = "done", **kw: Any):
        super().__init__(**kw)
        object.__setattr__(self, "script", list(script))
        object.__setattr__(self, "final_text", final_text)
        object.__setattr__(self, "_cursor", iter(list(script)))
        object.__setattr__(self, "seen", [])

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(self, messages, stop=None, run_manager=None, **kw) -> ChatResult:
        self.seen.append(list(messages))
        try:
            msg = next(self._cursor)
        except StopIteration:
            msg = AIMessage(content=self.final_text)
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(self, tools, **kw):  # noqa: ANN001
        return self


def tool_call(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )
