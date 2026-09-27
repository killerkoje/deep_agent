"""Run control - how Main ends a run.

`finish` refuses while the run is not actually finished. Without that,
the cheapest way out of a blocked pipeline is to declare victory, and a
harness that can do that will eventually do it.
"""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from ..report import build_report


@tool
def finish(
    summary: str,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Write the total report and end the run.

    Only valid once QA has passed. If it has not, say why via fail_run
    or keep working - do not finish around a blocked gate.
    """
    sdd: dict[str, Any] = dict(state.get("sdd") or {})
    e2e = state.get("e2e") or {}

    blockers = []
    if not sdd.get("verify_passed"):
        blockers.append("verify_passed is false")
    if e2e.get("status") not in {"passed", "not_run"}:
        blockers.append(f"e2e status is {e2e.get('status')}")
    if e2e.get("status") == "not_run" and sdd.get("target_repo_path"):
        blockers.append("qa has not run")

    if blockers:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        "GateReject: G_FINISH — cannot finish: "
                        + "; ".join(blockers),
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )

    sdd.update({"stage": "9-report", "last_event": "run.report.ready"})
    report = build_report({**state, "sdd": sdd, "status": "done"}, summary)
    return Command(
        update={
            "messages": [
                ToolMessage(
                    f"run.report.ready — report.md written ({len(report)} chars)",
                    tool_call_id=tool_call_id,
                )
            ],
            "files": {**(state.get("files") or {}), "report.md": report},
            "sdd": sdd,
            "status": "done",
        }
    )


@tool
def fail_run(
    reason: str,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Stop the run and say why. A report is still written - a failed run
    that leaves no trace of why is the worst outcome."""
    sdd: dict[str, Any] = dict(state.get("sdd") or {})
    sdd.update({"error": reason, "last_event": "run.failed"})
    return Command(
        update={
            "messages": [ToolMessage(f"run failed: {reason}", tool_call_id=tool_call_id)],
            "files": {
                **(state.get("files") or {}),
                "report.md": build_report(state, f"FAILED: {reason}"),
            },
            "sdd": sdd,
            "status": "error",
        }
    )
