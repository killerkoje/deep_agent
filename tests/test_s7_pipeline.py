"""S4-S7 - the pipeline end to end.

Drives sources -> spec -> holes (in parallel) -> decide -> rereview ->
crosscheck -> ready -> verify -> human gate -> report, with scripted
models throughout. No API key.

The interesting assertions are not "it completed" but the places it
refuses to: an ungrounded decision, a permission question the agent
tried to wave through, finishing while a gate is still red.
"""

from __future__ import annotations

import contextvars

from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.types import Command as ResumeCommand

from deep_agent import gates
from deep_agent.graph import build_graph
from deep_agent.state import initial_state
from deep_agent.tools import spawn as spawn_mod
from tests.fakes import ScriptedChatModel, tool_call

# --- artifacts the scripted sub-agents produce -----------------------

SPEC_MD = """\
# CRM 지표

### 1.1 신규 고객 수
처리: 이번 달 첫 구매한 고객을 센다  [AI 결정] (D-001)
성공: 대시보드 수치가 표본과 일치한다
출처: sources/prd.md#L12

### 1.2 지점별 매출
처리: 조건: 지점 코드로 그룹화한다
성공: 합계가 전체 매출과 일치한다
출처: sources/prd.md#L30
"""

DECISIONS_OK = """\
## D-001  신규 고객의 정의
- **결정:** 이번 달 첫 구매한 고객  [AI 결정]
- **근거:** sources/prd.md#L12 의 구매 이력 기준에서 유도
- **대안:** 가입 기준 - 1.2절 집계와 어긋나 미채택
- **확신도:** 중
- **분류:** 일반
"""

# The agent filed a permission decision as ordinary. The gate should not
# care what it filed it as.
DECISIONS_SNEAKY = DECISIONS_OK + """
## D-013  지점 관리자 조회 범위
- **결정:** 지점 관리자도 전체 지점 매출 랭킹을 조회할 수 있다  [AI 결정]
- **근거:** sources/prd.md#L30 랭킹 요구에서 유도
- **대안:** 지점명 마스킹
- **확신도:** 하
- **분류:** 일반
"""

DECISIONS_UNGROUNDED = """\
## D-002  정렬 기준
- **결정:** 최신순으로 정렬한다  [AI 결정]
- **근거:** 일반적으로 그렇게 한다
- **대안:** 없음
- **확신도:** 중
- **분류:** 일반
"""


def writer(path: str, body: str, summary: str, extra: dict | None = None):
    """A sub-agent that writes its declared outputs and reports."""
    script = [tool_call("write_file", {"path": path, "content": body}, "w1")]
    for i, (p, b) in enumerate((extra or {}).items()):
        script.append(tool_call("write_file", {"path": p, "content": b}, f"w{i + 2}"))
    return lambda _m=None: ScriptedChatModel(script, final_text=summary)


# Which skill is spawning right now. A ContextVar rather than a plain
# dict because ToolNode runs parallel tool calls in separate contexts:
# with a shared variable, two concurrent spawns both read whichever
# skill was written last and run the same sub-agent script.
_CURRENT_SKILL: contextvars.ContextVar[str] = contextvars.ContextVar("skill")


def route_sub(decisions_md=DECISIONS_OK):
    """Pick the scripted sub-agent by the skill currently spawning.

    `decide` and `crosscheck` both write decisions.md, so both get the
    same body - otherwise the later one silently reverts the earlier.
    """
    subs = {
        "spec-write": writer("spec.md", SPEC_MD, "spec.md: 2 ACs"),
        "openspec": writer("questions.openspec.md", "Q1 신규 고객 정의?", "3 holes"),
        "spec-kit": writer("questions.speckit.md", "Q2 조회 권한?", "2 holes"),
        "decide": writer("decisions.md", decisions_md, "answered 5"),
        "spec-rereview": writer("spec.md", SPEC_MD, "decisions folded in"),
        "crosscheck": writer(
            "decisions.md", decisions_md,
            "no contradictions",
            {"meta/crosscheck.json": '{"found": 0}'},
        ),
        # Frontmatter, not prose - a gate reads a declared number.
        "ready-audit": writer(
            "ready.md",
            "---\nready_open_count: 0\n---\n# ready\n\n## 1. 남은 문제\n\n없음\n",
            "section 1 empty",
        ),
    }

    def factory(_model_id=None):
        return subs[_CURRENT_SKILL.get()]()

    return factory, None


def run_pipeline(script, decisions_md=DECISIONS_OK, resume=None, **kw):
    factory, _ = route_sub(decisions_md)

    # remember which skill each spawn is for, so the right script runs
    real_spawn = spawn_mod.spawn.func

    def patched(skill, brief, state, tool_call_id):
        _CURRENT_SKILL.set(skill)
        return real_spawn(skill, brief, state, tool_call_id)

    spawn_mod.spawn.func = patched
    spawn_mod.set_model_factory(factory)
    try:
        main = ScriptedChatModel(script, final_text="done")
        graph = build_graph(model_factory=lambda: main)
        cfg = {"configurable": {"thread_id": "th_pipe"}}
        out = graph.invoke(
            {
                **initial_state(thread_id="th_pipe", feature_id="crm", **kw),
                "messages": [HumanMessage("start")],
            },
            cfg,
        )
        if resume is not None:
            out = graph.invoke(ResumeCommand(resume=resume), cfg)
        return out, graph, cfg
    finally:
        spawn_mod.spawn.func = real_spawn
        spawn_mod.set_model_factory(None)


def sp(skill, model=None, brief="go", cid="c"):
    """`model` is accepted and ignored - the operator picks it now, and
    several tests still pass one to document what they used to assert."""
    return tool_call("spawn", {"skill": skill, "brief": brief}, cid)


HAPPY = [
    sp("spec-write", cid="c1"),
    sp("openspec", cid="c2"),
    sp("spec-kit", cid="c3"),
    sp("decide", cid="c4"),
    sp("spec-rereview", cid="c5"),
    sp("crosscheck", cid="c6"),
    sp("ready-audit", cid="c7"),
    tool_call("run_verify", {}, "c8"),
    tool_call("check_human_gate", {}, "c9"),
    tool_call("finish", {"summary": "CRM 지표 스펙 완료"}, "c10"),
]


# --- the happy path --------------------------------------------------


def test_full_pipeline_reaches_a_report():
    out, _, _ = run_pipeline(HAPPY, sources={"prd.md": "# PRD"})

    assert out["sdd"]["verify_passed"] is True
    assert out["status"] == "done"
    assert "report.md" in out["files"]

    produced = set(out["files"])
    assert {"spec.md", "decisions.md", "ready.md", "meta/verify-token"} <= produced


def test_report_records_skill_and_model_per_spawn():
    from deep_agent.workspace import Workspace

    out, _, _ = run_pipeline(HAPPY, sources={"prd.md": "# PRD"})
    report = Workspace.for_thread("th_pipe").read("report.md")

    assert "spec-write" in report and "gpt-6-luna" in report
    assert "Total:" in report
    # no role-to-model table anywhere - just what was chosen per call
    assert "default_for_skill" not in report


def test_hole_finders_run_independently():
    """Both must produce their own question file; neither sees the other."""
    out, _, _ = run_pipeline(HAPPY, sources={"prd.md": "# PRD"})
    assert "questions.openspec.md" in out["files"]
    assert "questions.speckit.md" in out["files"]
    assert out["files"]["questions.openspec.md"] != out["files"]["questions.speckit.md"]


# --- where it refuses ------------------------------------------------


def test_ungrounded_decision_is_rejected_at_the_source():
    """'일반적으로 그렇게 한다' is not grounds."""
    out, _, _ = run_pipeline(
        [sp("spec-write", cid="c1"), sp("decide", cid="c2")],
        decisions_md=DECISIONS_UNGROUNDED,
        sources={"prd.md": "# PRD"},
    )
    msgs = "\n".join(m.content for m in out["messages"] if isinstance(m, ToolMessage))
    assert "G_DECISION_LOGGED" in msgs
    assert "not a citation" in msgs


def test_verify_blocks_when_crosscheck_never_ran():
    out, _, _ = run_pipeline(
        [sp("spec-write", cid="c1"), tool_call("run_verify", {}, "c2")],
        sources={"prd.md": "# PRD"},
    )
    assert out["sdd"]["verify_passed"] is False
    msgs = "\n".join(m.content for m in out["messages"] if isinstance(m, ToolMessage))
    assert "crosscheck has not run" in msgs


def test_implement_refused_before_ready_audit():
    out, _, _ = run_pipeline(
        [sp("spec-write", cid="c1"), sp("implement", model="gpt-6-astra", cid="c2")],
        sources={"prd.md": "# PRD"},
    )
    msgs = "\n".join(m.content for m in out["messages"] if isinstance(m, ToolMessage))
    assert "G_NO_IMPL_WITHOUT_VERIFY" in msgs


def test_finish_refuses_while_verify_is_red():
    """The cheapest way out of a blocked pipeline must not be to declare
    victory."""
    out, _, _ = run_pipeline(
        [sp("spec-write", cid="c1"), tool_call("finish", {"summary": "다 됐음"}, "c2")],
        sources={"prd.md": "# PRD"},
    )
    msgs = "\n".join(m.content for m in out["messages"] if isinstance(m, ToolMessage))
    assert "G_FINISH" in msgs
    assert out["status"] != "done"


# --- the human gate --------------------------------------------------


def test_permission_decision_stops_the_run_despite_being_filed_as_일반():
    out, graph, cfg = run_pipeline(
        HAPPY[:-1], decisions_md=DECISIONS_SNEAKY, sources={"prd.md": "# PRD"}
    )

    snap = graph.get_state(cfg)
    assert snap.interrupts, "the run should be parked at the human gate"

    payload = snap.interrupts[0].value
    assert payload["kind"] == "human_gate"
    assert [i["id"] for i in payload["items"]] == ["D-013"]


def test_resuming_with_an_answer_records_it_as_human_authority():
    _, graph, cfg = run_pipeline(
        HAPPY[:-1], decisions_md=DECISIONS_SNEAKY, sources={"prd.md": "# PRD"}
    )
    out = graph.invoke(
        ResumeCommand(resume=[{"item_id": "D-013", "choice": "① 지점명 마스킹"}]), cfg
    )

    d13 = next(d for d in out["sdd"]["decisions"] if d["id"] == "D-013")
    assert d13["answer"] == "① 지점명 마스킹"
    assert d13["tag"] == "[답변]"  # authority changed hands


def test_the_stop_survives_the_process():
    """interrupt checkpoints; nothing needs to stay running."""
    _, graph, cfg = run_pipeline(
        HAPPY[:-1], decisions_md=DECISIONS_SNEAKY, sources={"prd.md": "# PRD"}
    )
    # a fresh graph object, same checkpointer + thread_id
    snap = graph.get_state(cfg)
    assert snap.values["sdd"]["decisions"]
    assert snap.next  # still has work pending


def test_ordinary_run_does_not_stop():
    out, graph, cfg = run_pipeline(HAPPY[:-1], sources={"prd.md": "# PRD"})
    assert not graph.get_state(cfg).interrupts
    msgs = "\n".join(m.content for m in out["messages"] if isinstance(m, ToolMessage))
    assert "human gate clear" in msgs


# --- the parser the gates depend on ----------------------------------


def test_decisions_md_parses_into_what_gates_read():
    parsed = gates.parse_decisions_md(DECISIONS_SNEAKY)
    assert [d["id"] for d in parsed] == ["D-001", "D-013"]

    d13 = parsed[1]
    assert d13["tag"] == "[AI 결정]"
    assert d13["confidence"] == "하"
    assert "마스킹" in d13["alternatives"]
