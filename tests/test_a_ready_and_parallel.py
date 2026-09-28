"""A - the ready_open_count gap, and parallel spawn.

G_READY read `ready_open_count` and nothing ever wrote it, so implement
was permanently blocked on "ready-audit has not run yet". The happy-path
test never caught it because that path never spawns implement.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deep_agent import gates
from tests.test_s7_pipeline import HAPPY, run_pipeline, sp

READY_OPEN = """\
# ready — 권한 검수

## 1. 남은 문제 — 여기만 논의한다

**2건.**

| # | 무엇이 | 종류 | 상태 |
|---|---|---|---|
| [R1](#r1) | 지점 관리자 조회 범위 | **결정 필요** | 미결 |
| [R2](#r2) | 취소 건 차감 시점 | 반영 대기 | 답은 나옴 |

## 2. 닫힌 것 — 대장

| # | 무엇이 | 어떻게 닫혔나 |
|---|---|---|
| C1 | 신규 고객 정의 | 채택 |
"""

READY_CLOSED = READY_OPEN.replace("| 미결 |", "| 닫힘 |").replace(
    "| 답은 나옴 |", "| 닫힘 |"
)

READY_EMPTY = "# ready\n\n## 1. 남은 문제\n\n**0건.** 전부 닫혔다.\n"

# What a skill is told to emit: the count is declared, not narrated.
READY_DECLARED = "---\nready: false\nready_open_count: 24\n---\n# ready\n\n산문만 있다\n"
READY_DECLARED_ZERO = "---\nready_open_count: 0\n---\n# ready\n"

# The exact document shape that slipped through on the first live run:
# the header says 24 are open, and the body happens to contain 없음
# inside an unrelated sentence.
READY_LIVE_TRAP = """\
# readiness audit

## 1. 논의·승인 대상 (열린 항목 24개)

| ID | 분류 | 내용 |
|---|---|---|
| G03 | 과금 | 매출 0/없음/음수 지점의 랭킹 포함 규칙 |
"""


# --- counting --------------------------------------------------------


def test_counts_only_open_rows_in_section_one():
    assert gates.count_ready_open(READY_OPEN) == 2
    assert gates.count_ready_open(READY_CLOSED) == 0


def test_closed_items_keep_their_row():
    """SPEC 10.4: an item closed in place is not deleted, its status
    flips. Counting rows blindly would never reach zero."""
    assert "R1" in READY_CLOSED  # still there
    assert gates.count_ready_open(READY_CLOSED) == 0


def test_section_two_rows_are_not_counted():
    """Only section 1 is open for discussion."""
    assert gates.count_ready_open(READY_OPEN) == 2  # not 3, C1 is in section 2


def test_declared_count_wins():
    """Skills declare the number in frontmatter; that is the contract."""
    assert gates.count_ready_open(READY_DECLARED) == 24
    assert gates.count_ready_open(READY_DECLARED_ZERO) == 0


def test_prose_is_never_read_as_clean():
    """The live-run bug. count_ready_open used to search the body for
    '없음' and matched it inside '매출 0/없음/음수 지점' - reporting a
    clean audit for a document whose own header said 24 were open.

    A heuristic that can silently return a PASSING value is worse than
    no gate. Prose now yields None, and None refuses."""
    assert gates.count_ready_open(READY_EMPTY) is None  # was 0
    assert gates.count_ready_open(READY_LIVE_TRAP) == 1  # the one table row


def test_unknown_stays_unknown():
    """None and 0 are different: 'never audited' must not read as
    'audited and clean'."""
    assert gates.count_ready_open(None) is None
    assert gates.count_ready_open("") is None
    assert gates.count_ready_open("# ready\n\n## 2. 닫힌 것\n") is None
    assert gates.count_ready_open("## 1. 남은 문제\n\n검토 중입니다\n") is None


# --- the gap this closes ---------------------------------------------


def test_implement_was_permanently_blocked_before_this():
    sdd = {"verify_passed": True, "ready_open_count": None, "budget": {}}
    rej = gates.allow_spawn(sdd, "implement", "gpt-6-astra", {"gpt-6-astra"}, ["implement"])
    assert rej.code == "G_READY" and "has not run" in rej.message


def test_ready_audit_now_sets_the_count():
    out, _, _ = run_pipeline(HAPPY[:7], sources={"prd.md": "# PRD"})
    assert out["sdd"]["ready_open_count"] == 0  # the fixture's ready.md says 0건


def test_open_items_block_implement_end_to_end():
    import tests.test_s7_pipeline as P

    original = P.route_sub

    def with_open_ready(decisions_md=P.DECISIONS_OK):
        factory, _ = original(decisions_md)
        inner = P.writer("ready.md", READY_OPEN, "2 still open")

        def routed(_model_id=None):
            skill = P._CURRENT_SKILL.get()
            return inner() if skill == "ready-audit" else factory(_model_id)

        return routed, None

    P.route_sub = with_open_ready
    try:
        # through run_verify, so the verify gate is green and G_READY is
        # the one that has to stop it
        out, _, _ = run_pipeline(
            [*HAPPY[:8], sp("implement", model="gpt-6-astra", cid="cX")],
            sources={"prd.md": "# PRD"},
        )
    finally:
        P.route_sub = original

    assert out["sdd"]["verify_passed"] is True
    assert out["sdd"]["ready_open_count"] == 2
    msgs = "\n".join(m.content for m in out["messages"] if isinstance(m, ToolMessage))
    assert "G_READY" in msgs and "still has 2 open" in msgs


# --- parallel spawn --------------------------------------------------


def test_both_hole_finders_can_run_from_one_turn():
    """openspec and spec-kit are independent, so Main may issue both in
    a single turn. Overlap between them is the signal we want; sharing
    a session would destroy it."""
    both = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "spawn",
                "args": {"skill": "openspec", "model": "gpt-6-astra", "brief": "holes"},
                "id": "p1",
                "type": "tool_call",
            },
            {
                "name": "spawn",
                "args": {"skill": "spec-kit", "model": "gpt-6-sol", "brief": "holes"},
                "id": "p2",
                "type": "tool_call",
            },
        ],
    )

    out, _, _ = run_pipeline([sp("spec-write", cid="c1"), both], sources={"prd.md": "# PRD"})

    assert "questions.openspec.md" in out["files"]
    assert "questions.speckit.md" in out["files"]

    skills = [h["skill"] for h in out["sdd"]["spawn_history"]]
    assert skills == ["spec-write", "openspec", "spec-kit"]

    # different models, chosen per call - no role-to-model table
    models = {h["skill"]: h["model"] for h in out["sdd"]["spawn_history"]}
    assert models["openspec"] == "gpt-6-astra"
    assert models["spec-kit"] == "gpt-6-sol"
