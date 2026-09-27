"""AgentState - what the graph carries between nodes.

Contract: docs/SPEC.md 5.

Three rules that are easy to break later:

1. `messages` uses the built-in `add_messages` reducer. Without it a node
   returning {"messages": [...]} would OVERWRITE the history instead of
   appending. We never hand-write reducers - see docs/concepts.md 3.1.

2. `todos` lives here, not in the prompt. That is what lets it survive
   compaction and be re-injected every turn (recitation, SPEC 8.2).

3. `files` holds DOCUMENTS ONLY. Code goes to real disk under
   `sdd.target_repo_path` - a checkpoint is written every step, and
   source trees or Playwright traces in state will kill it (SPEC 5.3).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph.message import add_messages

RunStatus = Literal["running", "waiting_human", "done", "error"]
TodoStatus = Literal["pending", "doing", "done"]

# Tags carry authority. `[AI 결정]` is a guess made visible as a guess;
# G_DECISION_LOGGED rejects it without rationale/alternatives/confidence.
DecisionTag = Literal["[코드]", "[AI 결정]", "[답변]", "[정지 후보]", "[CROSS]"]

# Only these two stop the run: everything else can be fixed with a later
# code change, these cannot (SPEC 11.2).
DecisionCategory = Literal["일반", "권한", "과금"]


class TodoItem(TypedDict):
    id: str
    text: str
    status: TodoStatus


class AgentState(TypedDict, total=False):
    # --- conversation ---
    messages: Annotated[list, add_messages]
    thread_id: str
    user_id: str

    # --- deep agent core ---
    todos: list[TodoItem]  # Planning  - re-injected every turn
    files: dict[str, str]  # Filesystem - documents only

    # --- execution control ---
    status: RunStatus
    next_action: str | None

    # --- SDD metadata (schemas/run-state.schema.json) ---
    sdd: dict[str, Any]

    # --- implement / verify loop (SPEC 5.5) ---
    implementation: dict[str, Any]
    e2e: dict[str, Any]
    analysis: dict[str, Any] | None
    iteration: int  # counts impl_bug retries only
    max_iterations: int


# Which pipeline stage an event puts us in. A hint for Main and for the
# report - gates never trust it (schemas/run-state.schema.json).
STAGE_OF_EVENT = {
    "sources.ready": "0-sources",
    "spec.draft.ready": "1-spec-draft",
    "questions.ready": "2-questions",
    "decisions.ready": "3-decide",
    "spec.updated": "4-rereview",
    "crosscheck.ready": "5-crosscheck",
    "ready.audit.ready": "6-ready",
    "human.gate.raised": "6b-human-gate",
    "human.decided": "6b-human-gate",
    "spec.verify.passed": "6-ready",
    "impl.ready": "7-implement",
    "qa.passed": "8-qa",
    "qa.failed": "8-qa",
    "triage.diagnosed": "8b-triage",
    "run.report.ready": "9-report",
}


def initial_state(
    *,
    thread_id: str,
    feature_id: str,
    user_id: str = "default",
    sources: dict[str, str] | None = None,
    target_repo_path: str | None = None,
    max_iterations: int = 3,
) -> AgentState:
    """A fresh run. `sources` are the human-supplied documents (stage 0)."""
    files: dict[str, str] = {}
    for name, content in (sources or {}).items():
        files[f"sources/{name}"] = content

    return AgentState(
        messages=[],
        thread_id=thread_id,
        user_id=user_id,
        todos=[],
        files=files,
        status="running",
        next_action=None,
        sdd={
            "run_id": thread_id,
            "thread_id": thread_id,
            "feature_id": feature_id,
            "stage": "0-sources",
            "spec_hash": None,
            "verify_passed": False,
            "ready_open_count": None,
            "last_event": None,
            "waiting_for": None,
            "human_gate": None,
            "decisions": [],
            "target_repo_path": target_repo_path,
            "budget": {"max_usd": None, "spent_usd": 0.0},
            "spawn_history": [],
            "gate_rejects": [],
            "error": None,
        },
        implementation={},
        e2e={"status": "not_run", "tests": [], "passed_count": 0},
        analysis=None,
        iteration=0,
        max_iterations=max_iterations,
    )
