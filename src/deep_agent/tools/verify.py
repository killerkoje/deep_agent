"""Main-side tools that turn gate verdicts into state changes.

`verify_spec` in gates.py only judges. These tools are the callers that
act on the judgement - writing the token, raising the human gate,
stopping the run. Keeping the two apart is what stops a gate from
quietly repairing the thing it is supposed to be judging.
"""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command, interrupt

from .. import gates
from ..workspace import Workspace


@tool
def run_verify(
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Run the six verify checks and set verify_passed accordingly.

    Call this after ready-audit. You cannot set verify_passed yourself -
    only this gate does, and it will refuse if any of the six fails.
    """
    sdd: dict[str, Any] = dict(state.get("sdd") or {})
    ws = Workspace.for_thread(state.get("thread_id") or sdd.get("run_id") or "default")

    # Gates stay content-based and pure, so the caller reads what they
    # need off disk and hands it over.
    content = ws.read_many(
        [
            "spec.md", "decisions.md", "questions.openspec.md",
            "questions.speckit.md", "meta/crosscheck.json",
            "meta/spec.sha256", "meta/verify-token",
        ]
    )
    result = gates.verify_spec(sdd, content, sdd.get("decisions") or [])

    if not result.passed:
        sdd["verify_passed"] = False
        ws.resolve("meta/verify-token").unlink(missing_ok=True)
        reasons = "\n".join(f"  - {r}" for r in result.reasons)
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        f"spec.verify.failed ({len(result.reasons)} reason(s)):\n{reasons}",
                        tool_call_id=tool_call_id,
                    )
                ],
                "sdd": {**sdd, "last_event": "spec.verify.failed"},
                "files": ws.index(),
            }
        )

    # Acting on the verdict is the caller's job, and this is the caller.
    ws.write("meta/spec.sha256", result.spec_hash)
    ws.write(
        "meta/verify-token", gates.verify_token(sdd.get("run_id", ""), result.spec_hash)
    )
    sdd.update(
        {
            "verify_passed": True,
            "spec_hash": result.spec_hash,
            "last_event": "spec.verify.passed",
        }
    )
    return Command(
        update={
            "messages": [
                ToolMessage(
                    f"spec.verify.passed (hash {result.spec_hash[:12]})",
                    tool_call_id=tool_call_id,
                )
            ],
            "sdd": sdd,
            "files": ws.index(),
        }
    )


@tool
def check_human_gate(
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Stop the run if any permission/security or money/billing decision
    is still unanswered, and collect the answers in one pass.

    You cannot skip this by classifying a decision as 일반 - the gate
    rescans independently and takes the union.
    """
    sdd: dict[str, Any] = dict(state.get("sdd") or {})
    decisions = sdd.get("decisions")   # None = unreadable, not empty
    terms = gates.load_sensitive_terms()

    if (blocked := gates.check_human_gate(decisions, terms)) is not None and decisions is None:
        return Command(
            update={
                "messages": [
                    ToolMessage(f"GateReject: {blocked}", tool_call_id=tool_call_id)
                ]
            }
        )

    raised = gates.human_gate_items(decisions or [], terms)
    pending = [d for d in raised if not (d.get("answer") or "").strip()]

    if not pending:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        f"human gate clear ({len(raised)} sensitive decision(s), all answered)",
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )

    # One stop per run, at the latest point, with everything batched.
    # interrupt() checkpoints and returns control - the process may die
    # here and the thread resumes on the same thread_id.
    answers = interrupt(
        {
            "kind": "human_gate",
            "reason": "permission/security and billing decisions need a person",
            "items": [
                {
                    "id": d.get("id"),
                    "title": d.get("title"),
                    "category": d.get("category"),
                    "text": d.get("text"),
                    "options": d.get("alternatives"),
                }
                for d in pending
            ],
        }
    )

    verdict = gates.check_answers_human(
        answers or [], actor="human", pending_ids=[d["id"] for d in pending]
    )
    if verdict is not None:
        return Command(
            update={
                "messages": [
                    ToolMessage(f"GateReject: {verdict}", tool_call_id=tool_call_id)
                ]
            }
        )

    by_id = {str(a["item_id"]): a for a in answers}
    decisions = list(decisions or [])
    for d in decisions:
        if (a := by_id.get(str(d.get("id")))) is not None:
            d["answer"] = a.get("choice")
            d["tag"] = "[답변]"  # a person decided it; authority changes

    sdd["decisions"] = decisions
    sdd["last_event"] = "human.decided"
    sdd["waiting_for"] = None
    return Command(
        update={
            "messages": [
                ToolMessage(
                    f"human.decided: {len(by_id)} answer(s) recorded",
                    tool_call_id=tool_call_id,
                )
            ],
            "sdd": sdd,
        }
    )
