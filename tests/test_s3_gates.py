"""S3 - gates as pure functions.

Every test here calls gates.py directly: no graph, no model, no API key,
no fake chat model to set up. That is the payoff for extracting them.
When one of these fails you know the rule is wrong, not the wiring.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from deep_agent import gates
from deep_agent.skills_loader import SKILLS

CATALOG = {"gpt-6-astra", "gpt-6-sol", "gpt-6-luna"}


def sdd(**kw):
    base = {
        "run_id": "run_x",
        "verify_passed": False,
        "ready_open_count": None,
        "budget": {"max_usd": None, "spent_usd": 0.0},
        "decisions": [],
    }
    base.update(kw)
    return base


def allow(s, skill="spec-write", model="gpt-6-sol"):
    return gates.allow_spawn(s, skill, model, CATALOG, SKILLS)


# --- rule 1: the module must not know about the engine ---------------


def test_gates_does_not_import_langgraph():
    """SPEC 13.1 #10. Also what lets the HTTP handler and CI call these."""
    src = Path("src/deep_agent/gates.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])

    assert "langgraph" not in imported
    assert "langchain_core" not in imported
    assert imported <= {"__future__", "hashlib", "json", "re", "dataclasses", "pathlib", "typing"}


def test_gates_makes_no_model_calls():
    """A gate that asks an LLM is not a gate - it can be talked round,
    and it answers differently on the same input twice."""
    src = Path("src/deep_agent/gates.py").read_text(encoding="utf-8")
    for forbidden in ("invoke(", "ChatOpenAI", "get_chat_model", "openai"):
        assert forbidden not in src


# --- spawn-time gates ------------------------------------------------


def test_unknown_skill_and_model():
    assert allow(sdd(), skill="make-it-nice").code == "G_SKILL_KNOWN"
    assert allow(sdd(), model="gpt-9").code == "G_MODEL_KNOWN"


def test_implement_needs_verify():
    assert allow(sdd(), "implement").code == "G_NO_IMPL_WITHOUT_VERIFY"
    assert allow(sdd(), "qa").code == "G_NO_IMPL_WITHOUT_VERIFY"


def test_implement_needs_ready_closed():
    verified = sdd(verify_passed=True)
    assert allow(verified, "implement").code == "G_READY"  # audit never ran

    assert allow(sdd(verify_passed=True, ready_open_count=2), "implement").code == "G_READY"
    assert allow(sdd(verify_passed=True, ready_open_count=0), "implement") is None


def test_qa_does_not_need_ready():
    """ready gates the build, not the check of it."""
    assert allow(sdd(verify_passed=True), "qa") is None


def test_budget_cap():
    s = sdd(budget={"max_usd": 5.0, "spent_usd": 5.0})
    assert allow(s).code == "G_BUDGET"
    s["budget"]["spent_usd"] = 4.99
    assert allow(s) is None


def test_editing_the_spec_un_verifies_it():
    files = {"spec.md": "v2", "meta/spec.sha256": gates.spec_hash("v1")}
    assert gates.spec_drifted(sdd(verify_passed=True), files) is True

    files["meta/spec.sha256"] = gates.spec_hash("v2")
    assert gates.spec_drifted(sdd(verify_passed=True), files) is False


# --- G_DECISION_LOGGED ------------------------------------------------


def good_decision(**kw):
    d = {
        "id": "D-001",
        "tag": "[AI 결정]",
        "text": "신규 고객 = 첫 구매",
        "rationale": "sources/prd.md#L88 의 구매 이력 기준에서 유도",
        "alternatives": "가입 기준 - 7쪽 집계와 불일치하여 미채택",
        "confidence": "중",
        "category": "일반",
    }
    d.update(kw)
    return d


def test_a_grounded_decision_passes():
    assert gates.check_decisions_logged([good_decision()]) is None


@pytest.mark.parametrize(
    "field,value,fragment",
    [
        ("rationale", "", "no rationale"),
        ("rationale", "합리적이므로 이렇게 정함", "not a citation"),
        ("rationale", "일반적으로 그렇게 함", "not a citation"),
        ("alternatives", "", "no alternative"),
        ("confidence", "높음", "상/중/하"),
        ("confidence", None, "상/중/하"),
    ],
)
def test_ungrounded_decisions_are_rejected(field, value, fragment):
    rej = gates.check_decisions_logged([good_decision(**{field: value})])
    assert rej is not None and rej.code == "G_DECISION_LOGGED"
    assert fragment in rej.message


def test_other_tags_carry_their_own_authority():
    """[코드] and [답변] are not AI guesses, so the rule does not apply."""
    for tag in ("[코드]", "[답변]", "[CROSS]"):
        d = {"id": "D-9", "tag": tag, "text": "x"}
        assert gates.check_decisions_logged([d]) is None


# --- G_HUMAN_GATE: the union is the point -----------------------------


TERMS = gates.load_sensitive_terms()


def test_gate_catches_what_the_agent_called_일반():
    """The agent must not be able to clear its own stop."""
    sneaky = good_decision(
        id="D-013",
        text="지점 관리자도 전체 매출 랭킹을 조회할 수 있다",
        category="일반",  # the agent says it is ordinary
    )
    raised = gates.human_gate_items([sneaky], TERMS)
    assert [d["id"] for d in raised] == ["D-013"]
    assert gates.check_human_gate([sneaky], TERMS).code == "G_HUMAN_GATE"


def test_agent_flag_alone_is_enough_too():
    """Union, not intersection - either side raising it stops the run."""
    flagged = good_decision(id="D-020", text="배치 주기를 6시간으로", category="과금")
    assert [d["id"] for d in gates.human_gate_items([flagged], TERMS)] == ["D-020"]


def test_ordinary_decision_does_not_stop_the_run():
    plain = good_decision(id="D-002", text="목록 정렬은 최신순", category="일반")
    assert gates.human_gate_items([plain], TERMS) == []
    assert gates.check_human_gate([plain], TERMS) is None


def test_answered_permission_decision_clears_the_gate():
    answered = good_decision(
        id="D-013", text="조회 권한 범위", category="권한", answer="① 지점명 마스킹"
    )
    assert gates.check_human_gate([answered], TERMS) is None


# --- G_ANSWERS_HUMAN --------------------------------------------------


def test_only_a_human_may_answer():
    rej = gates.check_answers_human([{"item_id": "D-1", "choice": "①"}], "decide", ["D-1"])
    assert rej.code == "G_ANSWERS_HUMAN" and "only a human" in rej.message


def test_partial_answers_are_rejected():
    rej = gates.check_answers_human(
        [{"item_id": "D-1", "choice": "①"}], "human", ["D-1", "D-2"]
    )
    assert "still open: D-2" in rej.message


def test_ai_authored_answer_is_rejected():
    rej = gates.check_answers_human(
        [{"item_id": "D-1", "choice": "① (추정)"}], "human", ["D-1"]
    )
    assert "AI-authored" in rej.message


def test_complete_human_answer_passes():
    assert (
        gates.check_answers_human(
            [{"item_id": "D-1", "choice": "① 마스킹"}], "human", ["D-1"]
        )
        is None
    )


# --- verify_spec ------------------------------------------------------

GOOD_SPEC = """\
# feature

### 1.1 신규 고객 수
처리: 이번 달 첫 구매 고객을 센다
성공: 대시보드 수치가 표본과 일치
출처: sources/prd.md#L88

### 1.2 이탈률
처리: 조건: 최근 30일 미구매이면 이탈로 본다
성공: 계산이 표본과 일치
출처: sources/prd.md#L120
"""


def good_files(spec=GOOD_SPEC, **extra):
    files = {"spec.md": spec, "meta/crosscheck.json": '{"found": 0}'}
    files.update(extra)
    return files


def test_verify_passes_on_a_complete_spec():
    r = gates.verify_spec(sdd(), good_files(), [good_decision()])
    assert r.passed, r.reasons
    assert r.spec_hash == gates.spec_hash(GOOD_SPEC)


def test_item1_acceptance_criteria_need_their_rules():
    thin = "# f\n\n### 1.1 매출\n표시한다\n"
    r = gates.verify_spec(sdd(), good_files(spec=thin))
    assert any(x.startswith("1:") for x in r.reasons)


def test_item2_branchy_wording_needs_a_condition():
    branchy = GOOD_SPEC.replace(
        "처리: 이번 달 첫 구매 고객을 센다",
        "처리: 첫 구매 또는 재구매 고객을 센다",
    )
    r = gates.verify_spec(sdd(), good_files(spec=branchy))
    assert any(x.startswith("2:") for x in r.reasons)


def test_item3_is_an_artifact_check_not_a_promise():
    """Q2: 'a human says they looked' was a formality, not a gate."""
    files = good_files()
    del files["meta/crosscheck.json"]
    r = gates.verify_spec(sdd(), files)
    assert any("crosscheck has not run" in x for x in r.reasons)


def test_item3_open_cross_item_blocks():
    unresolved = {"id": "D-050", "tag": "[CROSS]", "text": ""}
    r = gates.verify_spec(sdd(), good_files(), [unresolved])
    assert any("unresolved [CROSS]" in x for x in r.reasons)


def test_item4_ungrounded_decision_blocks():
    r = gates.verify_spec(sdd(), good_files(), [good_decision(rationale="느낌상")])
    assert any(x.startswith("4:") for x in r.reasons)


def test_item5_open_blocking_question_blocks():
    files = good_files(**{"questions.openspec.md": "[BLOCKING] 권한 범위?\n답:\n"})
    r = gates.verify_spec(sdd(), files)
    assert any(x.startswith("5:") for x in r.reasons)


def test_item6_edited_spec_invalidates_the_hash():
    files = good_files(**{"meta/spec.sha256": gates.spec_hash("older")})
    r = gates.verify_spec(sdd(), files, [good_decision()])
    assert any("changed since the hash" in x for x in r.reasons)


def test_item6_token_must_match_this_spec():
    files = good_files(**{"meta/verify-token": "VERIFY_OK:run_x:deadbeef"})
    r = gates.verify_spec(sdd(), files, [good_decision()])
    assert any("does not match" in x for x in r.reasons)


def test_verify_is_verdict_only():
    """It decides and computes; writing the token is the caller's job."""
    files = good_files()
    before = dict(files)
    gates.verify_spec(sdd(), files, [good_decision()])
    assert files == before
