"""spawn - the Subagents pillar.

A TOOL, not a node. If this were a node on Main's graph it would share
`AgentState`, and `add_messages` would append the sub-agent's whole
transcript to Main's `messages`. The isolation that is the entire reason
for sub-agents would be gone - and the graph would still run, so nobody
would notice until the context bill arrived.

What crosses back into Main:
  - one ToolMessage holding the summary
  - the declared output files
  - a spawn_history entry (skill, model, brief, tokens, cost)

What does not cross:
  - the sub-agent's messages. Not once, not filtered, not summarized-
    into-the-transcript. One ToolMessage, that is all.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated, Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from .. import skills_loader
from . import fs
from ..llm import _price
from ..models_catalog import spawnable_ids
from ..subagent import run_subagent

# Injected by tests so the loop can run without an API key.
_MODEL_FACTORY = None


def set_model_factory(factory) -> None:
    global _MODEL_FACTORY
    _MODEL_FACTORY = factory


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _reject(code: str, message: str, tool_call_id: str, sdd: dict, skill: str | None):
    """Gate rejections come back as a ToolMessage, not an exception.

    Raising would kill the graph and teach Main nothing. As a message,
    Main reads why it was blocked and replans - fail-closed and
    autonomous at the same time (docs/gates.md 1.2).
    """
    rejects = list(sdd.get("gate_rejects") or [])
    rejects.append({"code": code, "message": message, "skill": skill, "at": _now()})
    return Command(
        update={
            "messages": [
                ToolMessage(f"GateReject: {code} — {message}", tool_call_id=tool_call_id)
            ],
            "sdd": {**sdd, "gate_rejects": rejects[-10:]},
        }
    )


@tool
def spawn(
    skill: str,
    model: str,
    brief: str,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Run one skill in an isolated sub-agent session and return its summary.

    `model` is required and chosen by you per call - there is no default
    per skill. `brief` is everything the sub-agent will know: it cannot
    see this conversation.
    """
    sdd: dict[str, Any] = dict(state.get("sdd") or {})

    # --- gates (pure functions; S3 moves them into gates.py) ---
    try:
        sk = skills_loader.load(skill)
    except skills_loader.UnknownSkill as exc:
        return _reject("G_SKILL_KNOWN", str(exc), tool_call_id, sdd, skill)

    if model not in spawnable_ids():
        return _reject(
            "G_MODEL_KNOWN",
            f"{model!r} is not in the catalog",
            tool_call_id,
            sdd,
            skill,
        )

    if skill in {"implement", "qa"} and not sdd.get("verify_passed"):
        return _reject(
            "G_NO_IMPL_WITHOUT_VERIFY",
            "verify_passed=false",
            tool_call_id,
            sdd,
            skill,
        )

    # --- isolated run ---
    spawn_id = f"sp_{uuid.uuid4().hex[:12]}"
    result = run_subagent(
        system_prompt=sk.prompt,
        brief=brief,
        model_id=model,
        tools=fs.resolve(sk.tool_names),
        files=skills_loader.select_inputs(state.get("files") or {}, sk.inputs),
        workspace=sdd.get("target_repo_path") if sk.needs_workspace else None,
        model=_MODEL_FACTORY(model) if _MODEL_FACTORY else None,
    )

    # --- record what it cost and what it did ---
    cost = _price(model, result["tokens_in"], result["tokens_out"])
    budget = dict(sdd.get("budget") or {})
    budget["spent_usd"] = round(float(budget.get("spent_usd") or 0.0) + cost, 6)

    history = list(sdd.get("spawn_history") or [])
    history.append(
        {
            "spawn_id": spawn_id,
            "skill": skill,
            "model": model,
            "brief": brief,
            "at": _now(),
            "result": result["summary"][:500],
            "event": sk.event,
            "tokens_in": result["tokens_in"],
            "tokens_out": result["tokens_out"],
            "cost_usd": round(cost, 6),
        }
    )

    sdd.update(
        {"spawn_history": history, "budget": budget, "last_event": sk.event}
    )

    # Only declared outputs are merged back; a sub-agent cannot write
    # wherever it likes in the shared workspace.
    produced = {
        path: content
        for path, content in (result["files"] or {}).items()
        if path in sk.outputs or any(path.startswith(o.rstrip("*")) for o in sk.outputs)
    }

    return Command(
        update={
            "messages": [
                ToolMessage(
                    f"[{skill} @ {model}] {result['summary']}",
                    tool_call_id=tool_call_id,
                )
            ],
            "files": {**(state.get("files") or {}), **produced},
            "sdd": sdd,
        }
    )
