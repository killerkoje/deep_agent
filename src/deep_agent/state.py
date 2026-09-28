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


# --- reducers for the channels two spawns can write in the same turn --
#
# openspec and spec-kit are meant to run in parallel (they must not see
# each other's findings - overlap between independent readings is the
# signal). With the default last-value channel that is an
# InvalidUpdateError, so these two channels merge instead.


def merge_files(left: dict | None, right: dict | None) -> dict:
    """Per-path merge. Two sub-agents writing different files both land."""
    return {**(left or {}), **(right or {})}


def _append_unique(left: list | None, right: list | None, key) -> list:
    out = list(left or [])
    seen = {key(x) for x in out}
    for item in right or []:
        if key(item) not in seen:
            seen.add(key(item))
            out.append(item)
    return out


def merge_sdd(left: dict | None, right: dict | None) -> dict:
    """Scalars take the newer value; the two logs accumulate.

    Parallel branches each start from the same base, so their histories
    overlap - dedupe by spawn_id rather than concatenating blindly.
    """
    out = {**(left or {}), **(right or {})}
    out["spawn_history"] = _append_unique(
        (left or {}).get("spawn_history"),
        (right or {}).get("spawn_history"),
        key=lambda s: s.get("spawn_id"),
    )
    out["gate_rejects"] = _append_unique(
        (left or {}).get("gate_rejects"),
        (right or {}).get("gate_rejects"),
        key=lambda r: (r.get("code"), r.get("skill"), r.get("at")),
    )
    return out


class AgentState(TypedDict, total=False):
    # --- conversation ---
    messages: Annotated[list, add_messages]
    thread_id: str
    user_id: str

    # --- deep agent core ---
    todos: list[TodoItem]  # Planning  - re-injected every turn
    files: Annotated[dict[str, str], merge_files]  # INDEX: path -> sha256

    # --- execution control ---
    status: RunStatus
    next_action: str | None

    # --- SDD metadata (schemas/run-state.schema.json) ---
    sdd: Annotated[dict[str, Any], merge_sdd]

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
    """A fresh run. `sources` are the human-supplied documents (stage 0).

    They are written to disk immediately; state keeps only the index,
    so a checkpoint never carries document bytes.
    """
    from .workspace import Workspace

    ws = Workspace.for_thread(thread_id)
    for name, content in (sources or {}).items():
        ws.write(f"sources/{name}", content)
    files = ws.index()

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
