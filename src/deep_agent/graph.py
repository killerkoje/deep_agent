"""Graph wiring.

Two nodes. That is the point.

A stage-per-node graph (spec node -> impl node -> qa node) would be a
workflow: the developer fixes the order. Here the next action is Main's
tool choice, which is what makes it an agent (docs/concepts.md 1).

    START -> main_agent -> tools? -- yes --> tools -> main_agent
                              |
                              no
                              v
                             END
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from .checkpoint import get_checkpointer
from .llm import get_main_model
from .nodes.main_agent import make_main_agent_node
from .state import AgentState
from .tools.control import fail_run, finish
from .tools.loop import apply_diagnosis, record_e2e
from .tools.spawn import spawn
from .tools.todos import write_todos
from .tools.verify import check_human_gate, run_verify

# Main's complete tool set.
#
# `spawn` is a TOOL, never a node - see src/deep_agent/tools/spawn.py.
#
# Deliberately absent, permanently: write_file, apply_patch, git_commit,
# bash. Main delegates work; it does not do it. Enforced by tool absence
# rather than prompt wording (SPEC 8.3).
#
# Note what Main also cannot do: set verify_passed, or answer a
# permission question. run_verify and check_human_gate let it ASK for a
# verdict, not issue one.
MAIN_TOOLS = [
    write_todos,
    spawn,
    run_verify,
    check_human_gate,
    record_e2e,
    apply_diagnosis,
    finish,
    fail_run,
]


def build_graph(tools=None, model_factory=get_main_model, checkpointer=None):
    tools = MAIN_TOOLS if tools is None else tools

    builder = StateGraph(AgentState)
    builder.add_node("main_agent", make_main_agent_node(tools, model_factory))
    builder.add_node("tools", ToolNode(tools))

    builder.add_edge(START, "main_agent")
    builder.add_conditional_edges(
        "main_agent",
        tools_condition,
        {"tools": "tools", END: END},
    )
    builder.add_edge("tools", "main_agent")

    # In-memory locally, Postgres once DATABASE_URL is set. One call,
    # because we did not hand-roll persistence (SPEC 12.2).
    return builder.compile(checkpointer=checkpointer or get_checkpointer())
