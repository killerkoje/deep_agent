"""S1 - the tool loop turns, and the plan lives outside the prompt."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deep_agent.graph import MAIN_TOOLS, build_graph
from deep_agent.state import initial_state
from deep_agent.tools.todos import recite
from tests.fakes import ScriptedChatModel, tool_call


def run(script, final_text="finished", **state_kw):
    model = ScriptedChatModel(script, final_text=final_text)
    graph = build_graph(model_factory=lambda: model)
    state = initial_state(thread_id="th_test", feature_id="demo", **state_kw)
    cfg = {"configurable": {"thread_id": "th_test"}}
    out = graph.invoke({**state, "messages": [HumanMessage("start")]}, cfg)
    return out, model, graph, cfg


# --- the loop itself -------------------------------------------------


def test_loop_runs_tool_then_ends():
    out, _, _, _ = run(
        [tool_call("write_todos", {"items": [{"id": "T-1", "text": "spec", "status": "pending"}]}, "c1")]
    )

    assert [t["text"] for t in out["todos"]] == ["spec"]
    assert any(isinstance(m, ToolMessage) for m in out["messages"])
    assert out["messages"][-1].content == "finished"


def test_no_tool_call_ends_immediately():
    out, model, _, _ = run([], final_text="nothing to do")

    assert out["messages"][-1].content == "nothing to do"
    assert len(model.seen) == 1  # main_agent ran exactly once


def test_loop_turns_multiple_times():
    out, model, _, _ = run(
        [
            tool_call("write_todos", {"items": [{"id": "T-1", "text": "a", "status": "pending"}]}, "c1"),
            tool_call("write_todos", {"items": [{"id": "T-1", "text": "a", "status": "done"}]}, "c2"),
        ]
    )

    assert out["todos"][0]["status"] == "done"
    assert len(model.seen) == 3  # two tool turns + the closing turn


# --- Planning pillar -------------------------------------------------


def test_todos_are_reinjected_every_turn():
    """Recitation: the plan must appear in RECENT context on each turn,
    not only in the system prompt. This is what stops goal drift."""
    out, model, _, _ = run(
        [tool_call("write_todos", {"items": [{"id": "T-9", "text": "write spec", "status": "doing"}]}, "c1")]
    )

    first_turn, last_turn = model.seen[0], model.seen[-1]
    assert "TODO" in first_turn[-1].content
    assert "T-9 write spec" in last_turn[-1].content

    # It must be the LAST message, i.e. nearest the model's attention -
    # not buried in the system prompt at position 0.
    assert "T-9" not in last_turn[0].content
    assert last_turn[-1].content.index("TODO") > 0


def test_todos_survive_message_trimming():
    """Because todos are a state field, trimming `messages` cannot lose
    the plan - the reason Planning is not kept in the prompt."""
    out, _, _, _ = run(
        [tool_call("write_todos", {"items": [{"id": "T-1", "text": "keep me", "status": "pending"}]}, "c1")]
    )
    trimmed = {**out, "messages": []}
    assert "keep me" in recite(trimmed["todos"])


def test_write_todos_rejects_bad_status():
    out, _, _, _ = run(
        [tool_call("write_todos", {"items": [{"id": "T-1", "text": "x", "status": "maybe"}]}, "c1")]
    )
    tool_msgs = [m for m in out["messages"] if isinstance(m, ToolMessage)]
    assert "rejected" in tool_msgs[0].content
    assert out["todos"] == []  # state untouched


# --- structure guarantees --------------------------------------------


def test_graph_has_exactly_two_nodes():
    """A stage-per-node graph would be a workflow, not an agent."""
    graph = build_graph(model_factory=lambda: ScriptedChatModel([]))
    nodes = set(graph.get_graph().nodes) - {"__start__", "__end__"}
    assert nodes == {"main_agent", "tools"}


def test_main_has_no_write_tools():
    """Main is kept out of the work by tool absence, not by prompt wording."""
    names = {t.name for t in MAIN_TOOLS}
    assert names.isdisjoint({"write_file", "apply_patch", "git_commit", "bash"})


def test_checkpoint_persists_across_invocations():
    """Same thread_id resumes; this is what interrupt/resume will ride on."""
    model = ScriptedChatModel(
        [tool_call("write_todos", {"items": [{"id": "T-1", "text": "a", "status": "pending"}]}, "c1")]
    )
    graph = build_graph(model_factory=lambda: model)
    cfg = {"configurable": {"thread_id": "th_resume"}}

    graph.invoke(
        {**initial_state(thread_id="th_resume", feature_id="demo"), "messages": [HumanMessage("go")]},
        cfg,
    )
    later = graph.get_state(cfg)

    assert later.values["todos"][0]["text"] == "a"
    assert later.values["sdd"]["feature_id"] == "demo"
