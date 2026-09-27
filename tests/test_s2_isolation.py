"""S2 - sub-agent isolation.

The load-bearing test is test_spawn_adds_exactly_one_message. If that
breaks, the harness still works and still produces output - it just
quietly stops being a Deep Agent, and the context bill is the first
symptom. So it is pinned before anything else builds on spawn.

test_contrast_subagent_as_node_pollutes_parent is the deliberate
counter-experiment from PLAN Phase A: build it the wrong way once, watch
the parent get polluted, so the failure mode stays recognizable.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from deep_agent import skills_loader
from deep_agent.graph import build_graph
from deep_agent.state import AgentState, initial_state
from deep_agent.subagent import SubAgentState, run_subagent
from deep_agent.tools import spawn as spawn_mod
from tests.fakes import ScriptedChatModel, tool_call

SPEC_BODY = "# spec\n[AI 결정] 신규 고객 = 첫 구매\n"
SUB_FINAL = "spec.md written: 12 ACs, 3 TBD"

# A sub-agent that actually works: lists, reads, writes, then reports.
# Each tool call costs it an AI message plus a ToolMessage, so it burns
# 8 messages internally. Main must still gain exactly 1.
SUB_SCRIPT = [
    tool_call("ls", {}, "s1"),
    tool_call("read_file", {"path": "sources/prd.md"}, "s2"),
    tool_call("write_file", {"path": "spec.md", "content": SPEC_BODY}, "s3"),
]


def sub_factory(_model_id=None):
    return ScriptedChatModel(SUB_SCRIPT, final_text=SUB_FINAL)


def run_main(script, sub=sub_factory, **kw):
    spawn_mod.set_model_factory(sub)
    try:
        main = ScriptedChatModel(script, final_text="done")
        graph = build_graph(model_factory=lambda: main)
        state = initial_state(thread_id="th_s2", feature_id="demo", **kw)
        out = graph.invoke(
            {**state, "messages": [HumanMessage("start")]},
            {"configurable": {"thread_id": "th_s2"}},
        )
        return out, main
    finally:
        spawn_mod.set_model_factory(None)


def spawn_call(skill="spec-write", model="gpt-6-sol", brief="draft it", cid="c1"):
    return tool_call("spawn", {"skill": skill, "model": model, "brief": brief}, cid)


# --- THE test --------------------------------------------------------


def test_spawn_adds_exactly_one_message():
    """The sub-agent burns 8 messages internally. Main gains ONE."""
    out, _ = run_main([spawn_call()], sources={"prd.md": "# PRD"})

    tool_msgs = [m for m in out["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1, "spawn must add exactly one message to Main"

    blob = "\n".join(str(m.content) for m in out["messages"])
    assert "wrote spec.md" not in blob  # its write_file ToolMessage
    assert "sources/prd.md  (" not in blob  # its ls output
    assert SPEC_BODY not in blob  # the document body itself
    assert SUB_FINAL in blob  # only the summary came back

    # it really did the work - the output landed in shared state
    assert out["files"]["spec.md"] == SPEC_BODY


def test_subagent_really_burned_many_turns():
    """Guards the test above: if the sub-agent only ever took one turn,
    'exactly one parent message' would prove nothing."""
    from deep_agent.tools import fs

    skill = skills_loader.load("spec-write")
    out = run_subagent(
        system_prompt=skill.prompt,
        brief="do it",
        model_id="gpt-6-sol",
        tools=fs.resolve(skill.tool_names),
        files={"sources/prd.md": "# PRD"},
        model=sub_factory(),
    )
    assert len(out["transcript"]) >= 8
    assert out["summary"] == SUB_FINAL


def test_contrast_subagent_as_node_pollutes_parent():
    """The wrong way, built on purpose.

    Same sub-agent, wired as a NODE on the parent graph. It shares
    AgentState, so add_messages appends every message it produces to the
    parent. Everything runs; the isolation is simply gone.
    """
    chatty = [
        AIMessage("listing files"),
        AIMessage("reading sources/prd.md"),
        AIMessage("writing spec.md"),
        AIMessage(SUB_FINAL),
    ]

    def polluting_node(state):
        return {"messages": chatty}

    b = StateGraph(AgentState)
    b.add_node("sub", polluting_node)
    b.add_edge(START, "sub")
    b.add_edge("sub", END)
    bad = b.compile()

    out = bad.invoke(
        {**initial_state(thread_id="th_bad", feature_id="demo"), "messages": []}
    )

    assert len(out["messages"]) == 4  # vs 1 through the tool
    assert "reading sources/prd.md" in "\n".join(
        str(m.content) for m in out["messages"]
    )


# --- what the sub-agent can and cannot see ---------------------------


def test_subagent_never_receives_parent_messages():
    seen: list[list] = []

    class Spy(ScriptedChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kw):
            seen.append(list(messages))
            return super()._generate(messages, stop, run_manager, **kw)

    run_main(
        [spawn_call(brief="draft it")],
        sub=lambda _m=None: Spy([], final_text="ok"),
        sources={"prd.md": "# PRD"},
    )

    first = seen[0]
    assert isinstance(first[0], SystemMessage)  # the skill prompt
    assert first[1].content == "draft it"  # the brief
    assert len(first) == 2  # nothing else. no parent history.


def test_subagent_only_gets_declared_input_files():
    """qa has no business reading the implementer's notes."""
    files = {
        "sources/prd.md": "prd",
        "spec.md": "spec",
        "qa-report.md": "secret",
        "decisions.md": "decisions",
    }
    picked = skills_loader.select_inputs(
        files, skills_loader.load("openspec").inputs
    )
    assert set(picked) == {"spec.md"}


def test_subagent_has_its_own_state_class():
    """Isolation is structural: a different State class, therefore a
    different `messages` channel."""
    assert AgentState is not SubAgentState
    assert "todos" not in SubAgentState.__annotations__  # planning is Main's
    assert "sdd" not in SubAgentState.__annotations__  # run status is Main's


def test_undeclared_output_is_not_merged_back():
    """A sub-agent cannot write wherever it likes in shared state."""
    sneaky = [tool_call("write_file", {"path": "qa-report.md", "content": "PASS"}, "s1")]
    out, _ = run_main(
        [spawn_call()],
        sub=lambda _m=None: ScriptedChatModel(sneaky, final_text="done"),
        sources={"prd.md": "# PRD"},
    )
    assert "qa-report.md" not in out["files"]  # not in spec-write's outputs


# --- gates come back as messages, not exceptions ---------------------


def test_unknown_model_is_rejected_and_main_replans():
    out, _ = run_main(
        [spawn_call(model="gpt-9-imaginary", cid="c1"), spawn_call(cid="c2")],
        sources={"prd.md": "# PRD"},
    )

    msgs = [m.content for m in out["messages"] if isinstance(m, ToolMessage)]
    assert "GateReject: G_MODEL_KNOWN" in msgs[0]
    assert SUB_FINAL in msgs[1]  # recovered on the retry

    # the rejection is in Main's context so it does not hit the same wall
    assert out["sdd"]["gate_rejects"][0]["code"] == "G_MODEL_KNOWN"


def test_implement_blocked_until_verify_passes():
    out, _ = run_main([spawn_call(skill="implement", model="gpt-6-astra", brief="build")])
    msg = [m for m in out["messages"] if isinstance(m, ToolMessage)][0]
    assert "G_NO_IMPL_WITHOUT_VERIFY" in msg.content
    assert out["sdd"]["spawn_history"] == []  # it never ran


def test_unknown_skill_is_rejected():
    out, _ = run_main([spawn_call(skill="make-it-good")])
    assert "G_SKILL_KNOWN" in [
        m for m in out["messages"] if isinstance(m, ToolMessage)
    ][0].content


def test_embedding_model_is_not_spawnable():
    out, _ = run_main([spawn_call(model="text-embedding-3-small")], sources={"prd.md": "#"})
    assert "G_MODEL_KNOWN" in [
        m for m in out["messages"] if isinstance(m, ToolMessage)
    ][0].content


# --- accounting ------------------------------------------------------


def test_spawn_is_recorded_with_skill_model_and_cost():
    out, _ = run_main([spawn_call()], sources={"prd.md": "# PRD"})
    entry = out["sdd"]["spawn_history"][0]

    assert entry["skill"] == "spec-write"
    assert entry["model"] == "gpt-6-sol"
    assert entry["brief"] == "draft it"
    assert entry["event"] == "spec.draft.ready"
    assert {"tokens_in", "tokens_out", "cost_usd"} <= set(entry)
    assert entry["spawn_id"].startswith("sp_")
