"""The E2E failure loop - recording results and applying a diagnosis.

The routing decision itself is not here. Main reads the diagnosis and
picks the next tool; these tools only record what happened and enforce
what must not happen. An edge that hard-coded
`root_cause -> next node` would freeze the policy and could not carry
"which model to retry with" anyway.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from .. import gates


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@tool
def record_e2e(
    status: str,
    tests: list[dict],
    report_path: str | None = None,
    traces: list[str] | None = None,
    screenshots: list[str] | None = None,
    *,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Record an E2E run.

    `traces` and `screenshots` are PATHS. A Playwright trace.zip is tens
    of megabytes and a checkpoint is written every step (SPEC 5.3).

    Each test carries `ac_ref`: a test not tied to an acceptance clause
    is not evidence of anything.
    """
    prev = state.get("e2e") or {}
    passed = sum(1 for t in tests if t.get("status") == "passed")
    failed = [t for t in tests if t.get("status") == "failed"]

    unmapped = [t.get("id") for t in tests if not t.get("ac_ref")]
    note = f" ({len(unmapped)} test(s) not mapped to an AC)" if unmapped else ""

    e2e = {
        "status": status,
        "tests": tests,
        "passed_count": passed,
        "prev_passed_count": prev.get("passed_count"),
        "report_path": report_path,
        "traces": list(traces or []),
        "screenshots": list(screenshots or []),
        "ran_at": _now(),
    }
    sdd = {**(state.get("sdd") or {}), "last_event": f"qa.{status}"}

    return Command(
        update={
            "messages": [
                ToolMessage(
                    f"qa.{status}: {passed} passed, {len(failed)} failed{note}",
                    tool_call_id=tool_call_id,
                )
            ],
            "e2e": e2e,
            "sdd": sdd,
        }
    )


@tool
def apply_diagnosis(
    root_cause: str,
    rationale: str,
    affected_acs: list[str] | None = None,
    confidence: str = "중",
    *,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Record an e2e-triage verdict and apply what follows from it.

    root_cause is one of impl_bug | spec_gap | test_defect | environment.

    Only impl_bug spends loop budget. spec_gap leaves the loop: it drops
    verify_passed so the run goes back through the spec, because
    retrying the build instead means the gap gets filled by a guess and
    the guess passes the test.
    """
    if root_cause not in gates.ROUTE_OF_CAUSE:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        f"unknown root_cause {root_cause!r}; expected one of "
                        f"{', '.join(gates.ROUTE_OF_CAUSE)}",
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )

    e2e: dict[str, Any] = state.get("e2e") or {}
    sdd: dict[str, Any] = dict(state.get("sdd") or {})
    route = gates.ROUTE_OF_CAUSE[root_cause]

    analysis = {
        "root_cause": root_cause,
        "route": route,
        "rationale": rationale,
        "confidence": confidence,
        "affected_acs": list(affected_acs or []),
        "ran_after": e2e.get("ran_at"),
        "at": _now(),
    }

    history = list(sdd.get("triage_history") or [])
    history.append(
        {
            "root_cause": root_cause,
            "failed_ids": [
                t.get("id") for t in (e2e.get("tests") or []) if t.get("status") == "failed"
            ],
            "at": analysis["at"],
        }
    )
    sdd["triage_history"] = history[-10:]
    sdd["last_event"] = "triage.diagnosed"
    sdd["stage"] = "8b-triage"

    update: dict[str, Any] = {"analysis": analysis, "sdd": sdd}
    lines = [f"root_cause={root_cause} -> route={route} (confidence {confidence})"]

    if root_cause == "spec_gap":
        # Out of the loop, back to the spec. The iteration counter is
        # NOT reset - after fixing the spec you still need to know how
        # much budget is left.
        sdd["verify_passed"] = False
        lines.append(
            "verify_passed dropped to false: the spec is missing a rule, so "
            "this goes back through decide/rereview, not another build."
        )
    elif root_cause == "impl_bug":
        guard = gates.check_loop(
            state.get("iteration", 0),
            state.get("max_iterations", 3),
            sdd,
            state.get("implementation") or {},
            e2e,
            sdd.get("triage_history"),
        )
        if guard is not None:
            update["status"] = "waiting_human"
            sdd["waiting_for"] = "loop_exhausted"
            lines.append(f"GateReject: {guard}")
        else:
            update["iteration"] = state.get("iteration", 0) + 1
            lines.append(
                f"iteration {state.get('iteration', 0) + 1}/"
                f"{state.get('max_iterations', 3)} - respawn implement"
            )
    elif root_cause == "test_defect":
        lines.append("respawn qa with route=fix_test; it may edit tests only there.")
    else:
        lines.append("environment: retry once, then escalate.")

    update["messages"] = [ToolMessage("\n".join(lines), tool_call_id=tool_call_id)]
    return Command(update=update)
