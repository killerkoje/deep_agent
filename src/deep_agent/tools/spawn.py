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

from .. import gates, sdd_cli, skills_loader
from . import fs
from ..llm import _price
from ..models_catalog import spawnable_ids
from ..state import STAGE_OF_EVENT
from ..subagent import run_subagent

# Injected by tests so the loop can run without an API key.
_MODEL_FACTORY = None


def set_model_factory(factory) -> None:
    global _MODEL_FACTORY
    _MODEL_FACTORY = factory


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_reject(sdd: dict, verdict: gates.GateReject) -> dict:
    rejects = list(sdd.get("gate_rejects") or [])
    rejects.append(
        {
            "code": verdict.code,
            "message": verdict.message,
            "skill": verdict.skill,
            "at": _now(),
        }
    )
    return {**sdd, "gate_rejects": rejects[-10:]}


def _reject(verdict: gates.GateReject, tool_call_id: str, sdd: dict):
    """Translate a gate verdict into this world's currency: a ToolMessage.

    Raising would kill the graph and teach Main nothing. As a message,
    Main reads why it was blocked and replans - fail-closed and
    autonomous at the same time (docs/gates.md 1.2).

    The rejection is also kept in `sdd.gate_rejects`, which main_agent
    re-injects next turn so Main does not walk into the same wall twice.
    """
    return Command(
        update={
            "messages": [
                ToolMessage(f"GateReject: {verdict}", tool_call_id=tool_call_id)
            ],
            "sdd": _record_reject(sdd, verdict),
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
    files: dict[str, str] = state.get("files") or {}

    # --- gates ---------------------------------------------------
    # The verdict comes from gates.py, which knows nothing about
    # LangGraph. All this does is translate it into a ToolMessage. The
    # HTTP handler translates the same verdict into a 400; CI into an
    # exit code. That is why the check does not live here.

    # A spec edited after verification stops being verified. Checked
    # here rather than inside allow_spawn because it needs the files.
    if gates.spec_drifted(sdd, files):
        sdd["verify_passed"] = False

    verdict = gates.allow_spawn(
        sdd,
        skill,
        model,
        catalog=spawnable_ids(),
        known_skills=skills_loader.SKILLS,
    )
    if verdict is not None:
        return _reject(verdict, tool_call_id, sdd)

    sk = skills_loader.load(skill)

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

    sdd.update({"spawn_history": history, "budget": budget, "last_event": sk.event})
    if stage := STAGE_OF_EVENT.get(sk.event or ""):
        sdd["stage"] = stage

    # Only declared outputs are merged back; a sub-agent cannot write
    # wherever it likes in the shared workspace.
    produced = {
        path: content
        for path, content in (result["files"] or {}).items()
        if path in sk.outputs or any(path.startswith(o.rstrip("*")) for o in sk.outputs)
    }
    merged_files = {**files, **produced}

    # decisions.md is the artifact people read; sdd.decisions is what
    # the gates read. Re-parsed after any skill that touches it, so the
    # two cannot drift apart.
    if "decisions.md" in produced:
        sdd["decisions"] = gates.parse_decisions_md(produced["decisions.md"])

    # Same idea for ready.md: G_READY needs a number, not prose. Left
    # as None when the audit has not run or its output is unparseable -
    # "no audit" must not read as "audit found nothing".
    if "ready.md" in produced:
        sdd["ready_open_count"] = gates.count_ready_open(produced["ready.md"])

    # --- post-run gates ------------------------------------------
    # The work is kept either way - a rejection here means "this is not
    # good enough yet", not "throw it away". Main sees why and respawns,
    # usually with a different model or a sharper brief.
    after = _check_outputs(skill, sk, merged_files, sdd)
    if after is not None:
        sdd = _record_reject(sdd, after)
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        f"[{skill} @ {model}] {result['summary']}\n"
                        f"GateReject: {after}",
                        tool_call_id=tool_call_id,
                    )
                ],
                "files": merged_files,
                "sdd": sdd,
            }
        )

    return Command(
        update={
            "messages": [
                ToolMessage(
                    f"[{skill} @ {model}] {result['summary']}",
                    tool_call_id=tool_call_id,
                )
            ],
            "files": merged_files,
            "sdd": sdd,
        }
    )


def _check_outputs(
    skill: str, sk, files: dict[str, str], sdd: dict[str, Any]
) -> gates.GateReject | None:
    """Gates that can only run once the sub-agent has produced something."""
    missing = [p for p in sk.outputs if p not in files]
    if missing:
        return gates.GateReject(
            "G_OUTPUT_MISSING", f"skill did not produce {', '.join(missing)}", skill
        )

    if skill == "openspec":
        workspace = sdd.get("target_repo_path")
        if not workspace or not sdd_cli.available("openspec"):
            # Not installed locally is a known state, not a silent pass.
            return gates.check_openspec_valid(0, None, skipped=True)
        res = sdd_cli.openspec_validate(workspace)
        return gates.check_openspec_valid(res.exit_code, res.json())

    if skill in {"decide", "spec-rereview", "crosscheck"}:
        return gates.check_decisions_logged(sdd.get("decisions") or [])

    return None
