"""write_todos - the Planning pillar.

The plan lives in state, not in the prompt. Two consequences:

- it survives compaction, because compaction only trims `messages`
- it can be re-injected into recent context every turn (recitation),
  which is what stops goal drift on long runs (docs/concepts.md 5.1)

Main is the only writer. Sub-agents get a `brief`, never the todo list.
"""

from __future__ import annotations

from typing import Annotated, Literal

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.types import Command

_VALID_STATUS = {"pending", "doing", "done"}


@tool
def write_todos(
    items: list[dict],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Replace the todo list with `items`.

    Each item: {"id": str, "text": str, "status": "pending"|"doing"|"done"}.
    Send the WHOLE list every time - this replaces, it does not merge.
    """
    cleaned: list[dict] = []
    for i, raw in enumerate(items):
        status = raw.get("status", "pending")
        if status not in _VALID_STATUS:
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            f"write_todos rejected: item {i} has status={status!r}, "
                            f"expected one of {sorted(_VALID_STATUS)}",
                            tool_call_id=tool_call_id,
                        )
                    ]
                }
            )
        cleaned.append(
            {
                "id": str(raw.get("id") or f"T-{i + 1:03d}"),
                "text": str(raw.get("text", "")).strip(),
                "status": status,
            }
        )

    done = sum(1 for t in cleaned if t["status"] == "done")
    return Command(
        update={
            "todos": cleaned,
            "messages": [
                ToolMessage(
                    f"todos updated: {done}/{len(cleaned)} done",
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )


def recite(todos: list[dict]) -> str:
    """Render the plan for re-injection into recent context.

    Called every turn by the main_agent node. Putting this in the system
    prompt once is NOT equivalent - as the conversation grows the plan
    drifts out of attention, which is the usual cause of goal drift.
    """
    if not todos:
        return "TODO: (empty - call write_todos to plan before acting)"

    mark: dict[str, str] = {"done": "x", "doing": ">", "pending": " "}
    lines = [f"[{mark.get(t['status'], ' ')}] {t['id']} {t['text']}" for t in todos]
    return "TODO (current plan):\n" + "\n".join(lines)
