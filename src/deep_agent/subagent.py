"""Sub-agent graphs.

A sub-agent is its own compiled graph with its own State class. That is
the whole isolation mechanism - not a flag, not a filter. `SubAgentState`
and `AgentState` both have a field called `messages`, but they are
different channels on different graphs, so nothing a sub-agent says can
land in Main's history.

If instead a sub-agent were a node on Main's graph, it would share
`AgentState`, and `add_messages` would append its entire transcript to
Main's `messages`. It would still run, so the leak goes unnoticed until
the context bill shows up. See docs/concepts.md 5.2.

No checkpointer here. A spawn is one unit of work: if it dies, Main
spawns a fresh one rather than resuming. Resuming would carry the wrong
assumption that caused the failure back into the retry.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from .llm import get_chat_model, text_of, usage_from_message

# How many agent<->tools turns a sub-agent gets before we stop it.
MAX_SUBAGENT_TURNS = 24


class SubAgentState(TypedDict, total=False):
    """Deliberately NOT AgentState.

    No `todos` - planning belongs to Main; a sub-agent gets a brief.
    No `sdd`  - a sub-agent does not read run status or pick models.
    """

    messages: Annotated[list, add_messages]
    workdir: str | None    # staged dir holding only the declared inputs
    repo: str | None       # target repo, for skills that touch code
    turns: int
    tokens_in: int
    tokens_out: int


def _agent_node(model: BaseChatModel, tools: list):
    bound = model.bind_tools(tools) if tools else model

    def agent(state: SubAgentState) -> dict[str, Any]:
        reply = bound.invoke(state["messages"])
        tin, tout = usage_from_message(reply)
        return {
            "messages": [reply],
            "turns": state.get("turns", 0) + 1,
            "tokens_in": state.get("tokens_in", 0) + tin,
            "tokens_out": state.get("tokens_out", 0) + tout,
        }

    return agent


def _route(state: SubAgentState):
    if state.get("turns", 0) >= MAX_SUBAGENT_TURNS:
        return END
    return tools_condition(state)


def build_subagent_graph(model: BaseChatModel, tools: list | None = None):
    tools = tools or []

    builder = StateGraph(SubAgentState)
    builder.add_node("agent", _agent_node(model, tools))
    builder.add_edge(START, "agent")

    if tools:
        builder.add_node("tools", ToolNode(tools))
        builder.add_conditional_edges("agent", _route, {"tools": "tools", END: END})
        builder.add_edge("tools", "agent")
    else:
        builder.add_edge("agent", END)

    return builder.compile()  # no checkpointer - see module docstring


def run_subagent(
    *,
    system_prompt: str,
    brief: str,
    model_id: str,
    tools: list | None = None,
    workdir: str | None = None,
    repo: str | None = None,
    model: BaseChatModel | None = None,
) -> dict[str, Any]:
    """Run one isolated session and return only what Main is allowed to see.

    The initial `messages` are built from scratch here. Main's history is
    never passed in - the brief is the entire inheritance.
    """
    chat = model or get_chat_model(model_id)
    graph = build_subagent_graph(chat, tools)

    result = graph.invoke(
        {
            "messages": [SystemMessage(system_prompt), HumanMessage(brief)],
            "workdir": workdir,
            "repo": repo,
            "turns": 0,
            "tokens_in": 0,
            "tokens_out": 0,
        }
    )

    last = result["messages"][-1]
    return {
        "summary": text_of(last) or "(no summary returned)",
        "turns": result.get("turns", 0),
        "tokens_in": result.get("tokens_in", 0),
        "tokens_out": result.get("tokens_out", 0),
        # Kept for the audit log on disk. Never fed back into any model.
        "transcript": result["messages"],
    }
