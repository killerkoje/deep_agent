"""C - the E2E failure loop.

The test that matters is test_spec_gap_leaves_the_loop. Reading a spec
gap as an implementation bug and rebuilding is how the agent ends up
filling the gap by guessing - and once the guess passes the test, the
guess has quietly become the spec. Every SDD gate in this repo exists
to stop that, and this is the one path that routes around all of them.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage, ToolMessage

from deep_agent import gates
from deep_agent.graph import build_graph
from deep_agent.state import initial_state
from tests.fakes import ScriptedChatModel, tool_call


def run(script, **state_kw):
    main = ScriptedChatModel(script, final_text="done")
    graph = build_graph(model_factory=lambda: main)
    state = initial_state(thread_id="th_loop", feature_id="crm", **state_kw)
    state["sdd"].update({"verify_passed": True, "spec_hash": "abc", "ready_open_count": 0})
    out = graph.invoke(
        {**state, "messages": [HumanMessage("go")]},
        {"configurable": {"thread_id": "th_loop"}},
    )
    return out


def failing_e2e(cid="e1"):
    return tool_call(
        "record_e2e",
        {
            "status": "failed",
            "tests": [
                {"id": "t1", "ac_ref": "AC-001", "status": "passed"},
                {"id": "t2", "ac_ref": "AC-002", "status": "failed"},
            ],
            "traces": ["workspace/trace.zip"],
        },
        cid,
    )


def diagnose(cause, cid="d1", rationale="…", confidence="중"):
    return tool_call(
        "apply_diagnosis",
        {"root_cause": cause, "rationale": rationale, "confidence": confidence},
        cid,
    )


def texts(out):
    return "\n".join(m.content for m in out["messages"] if isinstance(m, ToolMessage))


# --- THE test --------------------------------------------------------


def test_spec_gap_leaves_the_loop():
    """It must go back to the spec, not around again.

    Not counted as an iteration either: this is not a failed attempt at
    the build, it is the build never having been specified.
    """
    out = run([failing_e2e(), diagnose("spec_gap", rationale="no rule for 지점 귀속")])

    assert out["sdd"]["verify_passed"] is False  # back through the spec
    assert out["iteration"] == 0  # not a build attempt
    assert out["analysis"]["route"] == "respec"
    assert "goes back through decide/rereview" in texts(out)


def test_spec_gap_then_implement_is_blocked():
    """And the way back is actually shut."""
    out = run(
        [
            failing_e2e(),
            diagnose("spec_gap"),
            tool_call("spawn", {"skill": "implement", "brief": "fix"}, "s1"),
        ]
    )
    assert "G_NO_IMPL_WITHOUT_VERIFY" in texts(out)


def test_impl_bug_stays_in_the_loop():
    out = run([failing_e2e(), diagnose("impl_bug", rationale="spec §1.2 says X, code does Y")])

    assert out["iteration"] == 1
    assert out["sdd"]["verify_passed"] is True  # the spec was fine
    assert out["analysis"]["route"] == "reimplement"


def test_rebuilding_without_a_diagnosis_is_refused():
    out = run(
        [
            failing_e2e(),
            tool_call("spawn", {"skill": "implement", "brief": "fix"}, "s1"),
        ]
    )
    assert "G_TRIAGE_FIRST" in texts(out)


def test_a_stale_diagnosis_does_not_count():
    """A verdict from the previous failure is not a verdict on this one."""
    out = run(
        [
            failing_e2e("e1"),
            diagnose("impl_bug", "d1"),
            failing_e2e("e2"),  # a new failure; the old analysis is stale
            tool_call("spawn", {"skill": "implement", "brief": "x"}, "s1"),
        ]
    )
    assert "predates this failure" in texts(out)


# --- the five guards -------------------------------------------------


def failing_e2e_for(test_id, cid):
    return tool_call(
        "record_e2e",
        {
            "status": "failed",
            "tests": [
                {"id": "t1", "ac_ref": "AC-001", "status": "passed"},
                {"id": test_id, "ac_ref": "AC-002", "status": "failed"},
            ],
        },
        cid,
    )


def test_guard_two_fires_before_the_budget_does():
    """max_iterations is the ceiling, not the usual stop.

    Reaching 3 requires a different failure each round; the same test
    failing the same way twice is caught first.
    """
    # fresh objects per round - add_messages dedupes by message id, so
    # reusing one AIMessage would silently collapse the rounds
    script = []
    for i in range(4):
        script += [failing_e2e(f"e{i}"), diagnose("impl_bug", f"d{i}")]

    out = run(script, max_iterations=3)

    assert out["iteration"] == 1  # stopped on round two, not round three
    assert out["status"] == "waiting_human"
    assert "same diagnosis twice" in texts(out)


def test_budget_exhausts_when_each_round_fails_differently():
    script = []
    for i in range(4):
        script += [failing_e2e_for(f"t{i + 2}", f"e{i}"), diagnose("impl_bug", f"d{i}")]

    out = run(script, max_iterations=3)

    assert out["iteration"] == 3
    assert out["status"] == "waiting_human"
    assert "loop budget spent 3/3" in texts(out)


def test_same_diagnosis_twice_means_the_diagnosis_is_wrong():
    """Two rounds blaming the same test for the same reason is not a
    fix that failed - it is a wrong diagnosis. A third repair is waste."""
    history = [
        {"root_cause": "impl_bug", "failed_ids": ["t2"]},
        {"root_cause": "impl_bug", "failed_ids": ["t2"]},
    ]
    rej = gates.check_loop(1, 3, {"budget": {}}, {}, {}, history)
    assert rej.code == "G_LOOP" and "same diagnosis twice" in rej.message


def test_a_different_diagnosis_is_allowed_to_continue():
    history = [
        {"root_cause": "test_defect", "failed_ids": ["t2"]},
        {"root_cause": "impl_bug", "failed_ids": ["t2"]},
    ]
    assert gates.check_loop(1, 3, {"budget": {}}, {}, {}, history) is None


def test_regression_stops_the_loop():
    e2e = {"prev_passed_count": 12, "passed_count": 9}
    rej = gates.check_loop(1, 3, {"budget": {}}, {}, e2e)
    assert "regression: 12 passing -> 9" in rej.message


def test_spec_changing_mid_loop_discards_it():
    rej = gates.check_loop(
        1, 3, {"spec_hash": "new", "budget": {}}, {"spec_hash_at_impl": "old"}, {}
    )
    assert "spec changed mid-loop" in rej.message


# --- G_TEST_INTEGRITY ------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_sales.py",
        "e2e/checkout.spec.ts",
        "src/__tests__/util.test.js",
        "apps/web/e2e/login.spec.tsx",
    ],
)
def test_implement_may_not_touch_tests(path):
    rej = gates.check_test_integrity("implement", [path])
    assert rej.code == "G_TEST_INTEGRITY"


def test_implement_may_touch_source():
    assert gates.check_test_integrity("implement", ["src/api/sales.py"]) is None


def test_qa_may_only_edit_tests_when_routed_there():
    assert gates.check_test_integrity("qa", ["e2e/a.spec.ts"]).code == "G_TEST_INTEGRITY"
    assert gates.check_test_integrity("qa", ["e2e/a.spec.ts"], route="fix_test") is None


@pytest.mark.parametrize(
    "before,after,fragment",
    [
        ({"tests": 20}, {"tests": 17}, "test count"),
        ({"assertions": 55}, {"assertions": 40}, "assertion count"),
        ({"mapped_acs": 12}, {"mapped_acs": 11}, "AC mappings"),
    ],
)
def test_weakening_the_suite_is_refused(before, after, fragment):
    """Pass rate alone makes this look like success, so the SHAPE of the
    output is what gets checked."""
    rej = gates.check_test_integrity(
        "qa", ["e2e/a.spec.ts"], route="fix_test", before=before, after=after
    )
    assert rej.code == "G_TEST_INTEGRITY" and fragment in rej.message


def test_a_genuine_test_fix_passes():
    assert (
        gates.check_test_integrity(
            "qa",
            ["e2e/a.spec.ts"],
            route="fix_test",
            before={"tests": 20, "assertions": 55, "mapped_acs": 12},
            after={"tests": 20, "assertions": 56, "mapped_acs": 12},
        )
        is None
    )


# --- recording -------------------------------------------------------


def test_traces_are_recorded_as_paths():
    out = run([failing_e2e()])
    assert out["e2e"]["traces"] == ["workspace/trace.zip"]
    assert out["e2e"]["passed_count"] == 1
    assert out["sdd"]["last_event"] == "qa.failed"


def test_unmapped_tests_are_called_out():
    out = run(
        [
            tool_call(
                "record_e2e",
                {"status": "passed", "tests": [{"id": "t1", "status": "passed"}]},
                "e1",
            )
        ]
    )
    assert "not mapped to an AC" in texts(out)


def test_unknown_root_cause_is_rejected():
    out = run([failing_e2e(), diagnose("vibes")])
    assert "unknown root_cause" in texts(out)
    assert out["analysis"] is None
