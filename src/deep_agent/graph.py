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

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from .llm import get_main_model
from .nodes.main_agent import make_main_agent_node
from .state import AgentState
from .tools.spawn import spawn
from .tools.todos import write_todos

# S2 tool set. `wait_human` lands in S5.
#
# `spawn` is a TOOL, never a node - see src/deep_agent/tools/spawn.py.
#
# Deliberately absent, permanently: write_file, apply_patch, git_commit,
# bash. Main taking over the work is prevented by not handing it the
# tools, not by asking it nicely (SPEC 8.3).
MAIN_TOOLS = [write_todos, spawn]


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

    # S1 uses InMemorySaver. Swapping in PostgresSaver at Phase C is a
    # one-line change here - that is the payoff for not hand-rolling
    # persistence (SPEC 12.2).
    return builder.compile(checkpointer=checkpointer or InMemorySaver())
