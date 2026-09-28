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
from ..workspace import Workspace
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
    ws = Workspace.for_thread(state.get("thread_id") or sdd.get("run_id") or "default")
    files: dict[str, str] = state.get("files") or {}   # path -> sha256

    # --- gates ---------------------------------------------------
    # The verdict comes from gates.py, which knows nothing about
    # LangGraph. All this does is translate it into a ToolMessage. The
    # HTTP handler translates the same verdict into a 400; CI into an
    # exit code. That is why the check does not live here.

    # A spec edited after verification stops being verified. Checked
    # here rather than inside allow_spawn because it needs the files.
    if gates.spec_drifted(sdd, ws.read_many(["spec.md", "meta/spec.sha256"])):
        sdd["verify_passed"] = False

    verdict = gates.allow_spawn(
        sdd,
        skill,
        model,
        catalog=spawnable_ids(),
        known_skills=skills_loader.SKILLS,
    )

    # After a failed E2E run, rebuilding without a diagnosis is how a
    # spec gap gets filled by a guess (SPEC 5.5.2).
    if verdict is None and skill == "implement":
        verdict = gates.check_triage_first(
            sdd, state.get("analysis"), state.get("e2e") or {}
        )

    if verdict is not None:
        return _reject(verdict, tool_call_id, sdd)

    sk = skills_loader.load(skill)

    # --- isolated run ---
    # A directory holding only the declared inputs. On disk the
    # contract is enforced by absence: qa-report.md is not there to be
    # read, rather than guarded by a check something could route round.
    spawn_id = f"sp_{uuid.uuid4().hex[:12]}"
    workdir = ws.stage_spawn(spawn_id, sk.inputs)

    result = run_subagent(
        system_prompt=sk.prompt + skills_loader.contract_block(sk),
        brief=brief,
        model_id=model,
        tools=fs.resolve(sk.tool_names),
        workdir=str(workdir),
        repo=sdd.get("target_repo_path") if sk.needs_workspace else None,
        model=_MODEL_FACTORY(model) if _MODEL_FACTORY else None,
    )

    produced_paths = ws.collect(workdir, sk.outputs)

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

    # State carries the index, not the bytes.
    merged_files = ws.index()
    produced = set(produced_paths)

    # decisions.md is the artifact people read; sdd.decisions is what
    # the gates read. Re-parsed after any skill that touches it, so the
    # two cannot drift apart.
    if "decisions.md" in produced:
        # None means "unreadable", which must not become an empty list -
        # an empty list reads as "no decisions to check" and clears the
        # human gate. Kept as None so the gates refuse instead.
        sdd["decisions"] = gates.parse_decisions(ws.read("decisions.md"))

    # Same idea for ready.md: G_READY needs a number, not prose. Left
    # as None when the audit has not run or its output is unparseable -
    # "no audit" must not read as "audit found nothing".
    if "ready.md" in produced:
        sdd["ready_open_count"] = gates.count_ready_open(ws.read("ready.md"))

    # --- post-run gates ------------------------------------------
    # The work is kept either way - a rejection here means "this is not
    # good enough yet", not "throw it away". Main sees why and respawns,
    # usually with a different model or a sharper brief.
    after = _check_outputs(skill, sk, merged_files, sdd, ws)
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
    skill: str, sk, files: dict[str, str], sdd: dict[str, Any], ws: Workspace
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
            # Environment, not output quality. Does not block - no retry
            # could fix it - but it is recorded so the report cannot
            # claim a validation that never ran.
            skipped = list(sdd.get("skipped_validations") or [])
            reason = (
                "openspec binary not installed"
                if not sdd_cli.available("openspec")
                else "no target_repo_path to validate against"
            )
            if reason not in skipped:
                skipped.append(reason)
            sdd["skipped_validations"] = skipped
            return None
        res = sdd_cli.openspec_validate(workspace)
        return gates.check_openspec_valid(res.exit_code, res.json())

    if skill in {"decide", "spec-rereview", "crosscheck"}:
        decisions = sdd.get("decisions")
        if decisions is None and "decisions.md" in files:
            return gates.GateReject(
                "G_DECISION_FORMAT",
                "decisions.md carries no readable decision record. Emit a "
                "```json block: [{id, tag, text, rationale, alternatives, "
                "confidence, category}]",
                skill,
            )
        return gates.check_decisions_logged(decisions or [])

    if skill == "ready-audit":
        if sdd.get("ready_open_count") is None:
            return gates.GateReject(
                "G_READY_FORMAT",
                "ready.md does not declare ready_open_count. Put it in "
                "frontmatter: ---\nready_open_count: N\n---",
                skill,
            )

    return None


def _check_touched(skill: str, touched: list[str], state: dict) -> gates.GateReject | None:
    """Did the sub-agent edit files it had no business editing?

    Separate from _check_outputs because it looks at the real workspace,
    not at state documents.
    """
    route = (state.get("analysis") or {}).get("route")
    return gates.check_test_integrity(skill, touched, route=route)
