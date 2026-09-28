"""Regressions from the first run against real models.

114 tests passed before this run and every one of these bugs was live.
Scripted models cannot find them: they return the shapes the test author
already imagined. These fixtures are trimmed from what gpt-6 actually
wrote, so they keep the real shapes in the suite.

The common fault is not that parsing failed. It is that parsing failed
*into a passing value* - a gate that cannot read its input must refuse,
because an unreadable record is exactly where a problem hides.
"""

from __future__ import annotations

from deep_agent import gates

# --- 1. prose that reads as "clean" ----------------------------------

# ready-audit declared 24 open items in frontmatter and then, in an
# unrelated sentence, used the word 없음. The old parser ignored the
# declaration, searched the body, matched 없음, and returned 0.
LIVE_READY = """\
---
ready: false
ready_open_count: 24
ready_open_ids: [G01, G02, G03]
---
# CRM 지표 대시보드 — readiness audit

## 1. 논의·승인 대상 (열린 항목 24개)

| ID | 분류 | 미결 계약 |
|---|---|---|
| G01 | 과금 | D-009: 매출의 원천·상태·금액 필드·인식일. 정산 소유자 승인. |
| G03 | 과금 | 매출 0/없음/음수 지점의 표·랭킹 대상 포함 규칙. |
"""


def test_declared_count_beats_prose():
    assert gates.count_ready_open(LIVE_READY) == 24


def test_a_gate_cannot_be_talked_into_passing_by_a_word():
    """The exact failure: 없음 appearing in a sentence about revenue."""
    assert "없음" in LIVE_READY
    assert gates.count_ready_open(LIVE_READY) != 0


def test_no_declaration_and_no_table_refuses():
    assert gates.count_ready_open("# audit\n\n## 1. 남은 문제\n\n없음\n") is None


# --- 2. the decision record the gate could not see -------------------

# decide correctly marked fifteen items [정지 후보] - and wrote them as
# prose bullets, not as the `## D-001` headings the parser expected.
# parse_decisions_md returned [], the human gate read that as "nothing
# sensitive", and a run full of open permission questions went through.
LIVE_DECISIONS = """\
# CRM 지표 대시보드 — 통합 결정 및 인간 게이트

## 기술 결정

- **O01 [AI 결정] 신규 고객:** 최초 등록월에 고객 ID를 한 번만 센다.
  **근거:** `spec.md` §2.1. **기각:** 첫 유효 주문월. **확신도: 하**

## 미결 — 사람 승인 필요

- **O07/P02 [정지 후보] 지점 관리자 랭킹 노출(권한):** A 본사에만 전 지점 랭킹 /
  B 지점별 허용 범위로 계산. **권고:** A. 근거: `spec.md` §§2.2-2.3.
"""


def test_unreadable_decisions_return_none_not_empty():
    """[] would mean 'no decisions'. None means 'cannot tell'."""
    assert gates.parse_decisions(LIVE_DECISIONS) is None


def test_the_human_gate_refuses_what_it_cannot_read():
    """This is the bug that mattered. The old code cleared the gate."""
    terms = gates.load_sensitive_terms()
    verdict = gates.check_human_gate(None, terms)
    assert verdict is not None
    assert verdict.code == "G_HUMAN_GATE"
    assert "could not be read" in verdict.message


def test_empty_decision_list_still_clears():
    """A genuinely empty record is different from an unreadable one."""
    assert gates.check_human_gate([], gates.load_sensitive_terms()) is None


# --- 3. the shape skills are now told to emit ------------------------

DECLARED = """\
# decisions

```json
[
  {"id": "D-001", "tag": "[AI 결정]", "text": "신규 고객 = 첫 구매",
   "rationale": "sources/prd.md#L12 에서 유도", "alternatives": "가입 기준",
   "confidence": "중", "category": "일반"},
  {"id": "D-013", "tag": "[정지 후보]", "text": "지점 관리자 랭킹 노출",
   "category": "권한"}
]
```

사람이 읽는 본문은 아래에 이어진다.
"""


def test_declared_json_block_is_read():
    parsed = gates.parse_decisions(DECLARED)
    assert [d["id"] for d in parsed] == ["D-001", "D-013"]
    assert parsed[1]["category"] == "권한"


def test_a_stop_candidate_still_stops_the_run():
    parsed = gates.parse_decisions(DECLARED)
    verdict = gates.check_human_gate(parsed, gates.load_sensitive_terms())
    assert verdict.code == "G_HUMAN_GATE"
    assert "D-013" in verdict.message


def test_stop_candidate_left_unclassified_is_still_raised():
    """A [정지 후보] with no category must not fall through as 일반."""
    parsed = gates.parse_decisions(
        '```json\n[{"id":"D-9","tag":"[정지 후보]","text":"x"}]\n```'
    )
    assert parsed[0]["category"] == "권한"


# --- 4. reasoning models return block content ------------------------


def test_block_content_does_not_crash_the_run():
    """'list object has no attribute strip' took down the first live
    run at the first spawn. Scripted models always return strings."""
    from langchain_core.messages import AIMessage

    from deep_agent.llm import text_of

    msg = AIMessage(
        content=[
            {"type": "reasoning", "reasoning": "weighing the options"},
            {"type": "text", "text": "spec.md written"},
        ]
    )
    assert text_of(msg) == "spec.md written"


# --- 5. a gate that no retry can satisfy ------------------------------


def test_missing_tool_does_not_block():
    """The third live run died here, and correctly.

    openspec is not installed, so the gate rejected the skill's output
    for a reason that had nothing to do with the output. Main escalated
    the model, got the identical rejection, and called fail_run - which
    is exactly what it should do when a wall does not move. The bug was
    the wall: a missing binary is an environment fact, and a gate that
    rejects what no retry can change just burns the loop.
    """
    assert gates.check_openspec_valid(0, None, skipped=True) is None


def test_a_real_validation_failure_still_blocks():
    rej = gates.check_openspec_valid(
        1, {"findings": [{"message": "AC-003 has no acceptance rule"}]}
    )
    assert rej.code == "G_OPENSPEC_VALID"
    assert "AC-003" in rej.message


def test_the_report_says_which_checks_did_not_run():
    """Not blocking must not mean pretending it passed."""
    from deep_agent.report import build_report

    report = build_report(
        {
            "sdd": {
                "feature_id": "crm",
                "skipped_validations": ["openspec binary not installed"],
            }
        }
    )
    assert "Checks that did not run" in report
    assert "openspec binary not installed" in report
    assert "unverified" in report
