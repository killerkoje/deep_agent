"""HTTP API.

Thin on purpose: every route loads state, calls the graph, and returns.
No business logic here.

The route that justifies the whole module is POST /human-gate. Until it
existed there was no way for a person to answer a permission or billing
question outside a test - the gate could stop the run but nothing could
un-stop it.

It is also the second caller of gates.py, and the reason those checks
had to come out of the spawn tool: there is no graph context here, no
tool_call_id, nowhere to return a Command. The same verdict just gets
translated into a 400 instead.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Header
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from pydantic import BaseModel, Field

from . import backends, gates, skills_loader
from .policy import ModelPolicy, set_policy, validate_policy, validate_skills
from .checkpoint import get_checkpointer, is_durable
from .config import settings
from .graph import build_graph
from .state import initial_state
from .workspace import Workspace

app = FastAPI(title="Deep Agent Orchestrator", version="0.5.0")

_GRAPH = None


def graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph(checkpointer=get_checkpointer())
    return _GRAPH


def cfg(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def require_model_credentials() -> None:
    """A missing key is a predictable, user-fixable condition - it should
    read as one, not as a 500 with a provider stack trace."""
    if not settings.openai_api_key:
        raise HTTPException(
            503,
            "OPENAI_API_KEY is not set. Copy .env.example to .env and fill it in, "
            "or export the variable before starting the server.",
        )


def auth(authorization: str = Header(default="")) -> None:
    if authorization != f"Bearer {settings.orch_api_token}":
        raise HTTPException(401, "bad or missing bearer token")


def snapshot(thread_id: str):
    snap = graph().get_state(cfg(thread_id))
    if not snap.values:
        raise HTTPException(404, f"no such thread: {thread_id}")
    return snap


# --- models ----------------------------------------------------------


class Auth(BaseModel):
    """What the caller brought. Never stored beyond process memory."""

    provider: str = Field(
        ..., description="claude-cli | openai | anthropic"
    )
    api_key: str | None = None
    oauth_token: str | None = None


class CreateThread(BaseModel):
    feature_id: str
    sources: dict[str, str] = Field(
        ..., description="stage 0 documents: PRD, mails, prior specs"
    )
    target_repo_path: str | None = None
    max_iterations: int = 3

    # Step 3 of the flow: required, the user picks it.
    main_model: str | None = None
    # Step 4: optional - sub-agents inherit main_model when omitted.
    subagent_model: str | None = None
    # A few light roles an operator wants pinned cheaper. Everything
    # not named here inherits. Nothing may exceed main_model's tier.
    skill_models: dict[str, str] = Field(default_factory=dict)
    # Cost escape hatch: every spawn on one model, Main's choice ignored.
    force_subagent_model: bool = False

    auth: Auth | None = None


class GateAnswer(BaseModel):
    item_id: str
    choice: str
    note: str | None = None


class GateAnswers(BaseModel):
    answers: list[GateAnswer]


# --- routes ----------------------------------------------------------


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "main_model": settings.main_model,
        "model_credentials": bool(settings.openai_api_key),
        "durable": is_durable(),  # false means a restart loses running threads
    }


@app.post("/api/v1/auth")
def check_auth(body: Auth, _: None = Depends(auth)) -> dict:
    """Step 1-2 of the flow: validate what the caller brought.

    Nothing is stored here - credentials attach to a thread when one is
    created. This only answers "will this work, and what can it run".
    """
    creds = backends.Credentials(
        provider=body.provider, api_key=body.api_key, oauth_token=body.oauth_token
    )
    backend = backends.backend_for(creds)
    why = backend.available(creds)
    if why is not None:
        raise HTTPException(400, why)

    return {
        "ok": True,
        "backend": backend.name,
        "identity": creds.redacted(),
        "models": backend.models(),
    }


@app.get("/api/v1/models")
def models(provider: str | None = None, _: None = Depends(auth)) -> dict:
    """Step 3-4: what this caller can pick from.

    The list depends on the backend - a Claude login sees fable/opus/
    sonnet/haiku, an OpenAI key sees the gpt-6 tiers. It is also the
    exact list the gate checks, so a choice made here cannot be
    rejected later as unknown.
    """
    creds = backends.Credentials(provider=provider) if provider else None
    backend = backends.backend_for(creds)
    return {"backend": backend.name, "models": backend.models()}


@app.get("/api/v1/backends")
def list_backends(_: None = Depends(auth)) -> dict:
    return {"backends": backends.available_backends()}


@app.post("/api/v1/threads", status_code=201)
def create_thread(body: CreateThread, _: None = Depends(auth)) -> dict:
    require_model_credentials()
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    thread_id = f"run_{stamp}_{body.feature_id}"

    state = initial_state(
        thread_id=thread_id,
        feature_id=body.feature_id,
        user_id=settings.user_id,
        sources=body.sources,
        target_repo_path=body.target_repo_path or settings.target_repo_path,
        max_iterations=body.max_iterations,
    )
    state["sdd"]["last_event"] = "sources.ready"

    # Credentials live in process memory keyed by thread, never in
    # AgentState - state is checkpointed to disk and later Postgres, and
    # a checkpoint carrying an API key is a leak sitting in a backup.
    creds = None
    if body.auth:
        creds = backends.Credentials(
            provider=body.auth.provider,
            api_key=body.auth.api_key,
            oauth_token=body.auth.oauth_token,
        )
        if (why := backends.backend_for(creds).available(creds)) is not None:
            raise HTTPException(400, why)
        backends.set_credentials(thread_id, creds)

    backend = backends.backend_for(creds)
    if body.main_model:
        policy = ModelPolicy(
            main_model=body.main_model,
            subagent_model=body.subagent_model,
            skill_models=body.skill_models,
            force_subagent_model=body.force_subagent_model,
        )
        # A pin naming a skill that does not exist would never apply,
        # and the operator would believe they had set it.
        if (bad := validate_skills(policy, skills_loader.SKILLS)) is not None:
            backends.clear_credentials(thread_id)
            raise HTTPException(400, str(bad))
        # Refused up front, before a token is spent - and refused rather
        # than clamped, so nobody discovers afterwards that their run was
        # quietly done on a cheaper model than they asked for.
        if (bad := validate_policy(policy, backend.models())) is not None:
            backends.clear_credentials(thread_id)
            raise HTTPException(400, str(bad))
        set_policy(thread_id, policy)

    out = graph().invoke(
        {**state, "messages": [HumanMessage(f"Feature: {body.feature_id}. Begin.")]},
        cfg(thread_id),
    )
    return {"thread_id": thread_id, **_summary(thread_id, out)}


@app.get("/api/v1/threads/{thread_id}")
def get_thread(thread_id: str, _: None = Depends(auth)) -> dict:
    snap = snapshot(thread_id)
    return {"thread_id": thread_id, **_summary(thread_id, snap.values, snap)}


@app.get("/api/v1/threads/{thread_id}/files/{name:path}")
def get_file(thread_id: str, name: str, _: None = Depends(auth)) -> dict:
    index = snapshot(thread_id).values.get("files") or {}
    if name not in index:
        raise HTTPException(404, f"no such file: {name}")
    body = Workspace.for_thread(thread_id).read(name)
    if body is None:
        raise HTTPException(410, f"{name} is indexed but missing on disk")
    return {"path": name, "content": body, "sha256": index[name]}


@app.get("/api/v1/threads/{thread_id}/human-gate")
def read_human_gate(thread_id: str, _: None = Depends(auth)) -> dict:
    """What a person still has to decide. Empty when the run is not parked."""
    snap = snapshot(thread_id)
    payloads = [i.value for i in (snap.interrupts or [])]
    items = [
        item
        for p in payloads
        if isinstance(p, dict) and p.get("kind") == "human_gate"
        for item in p.get("items", [])
    ]
    return {"waiting": bool(items), "items": items}


@app.post("/api/v1/threads/{thread_id}/human-gate")
def answer_human_gate(
    thread_id: str, body: GateAnswers, _: None = Depends(auth)
) -> dict:
    """Answer every parked permission/billing question and resume.

    Partial answers are refused. The same gates.check_answers_human that
    the graph uses runs here - only the translation differs: a 400
    instead of a ToolMessage.
    """
    snap = snapshot(thread_id)
    pending = [
        item["id"]
        for i in (snap.interrupts or [])
        if isinstance(i.value, dict) and i.value.get("kind") == "human_gate"
        for item in i.value.get("items", [])
    ]
    if not pending:
        raise HTTPException(409, "this thread is not waiting on a human")

    require_model_credentials()  # resuming runs the graph again

    answers = [a.model_dump() for a in body.answers]
    verdict = gates.check_answers_human(answers, actor="human", pending_ids=pending)
    if verdict is not None:
        raise HTTPException(400, str(verdict))

    out = graph().invoke(Command(resume=answers), cfg(thread_id))
    return {"thread_id": thread_id, **_summary(thread_id, out)}


@app.get("/api/v1/threads/{thread_id}/report")
def get_report(thread_id: str, _: None = Depends(auth)) -> dict:
    snapshot(thread_id)  # 404s on an unknown thread
    report = Workspace.for_thread(thread_id).read("report.md")
    if not report:
        raise HTTPException(404, "no report yet")
    return {"report": report}


@app.get("/api/v1/threads/{thread_id}/history")
def get_history(thread_id: str, limit: int = 20, _: None = Depends(auth)) -> dict:
    """Checkpoint list - for debugging what the graph actually did."""
    out = []
    for snap in graph().get_state_history(cfg(thread_id)):
        out.append(
            {
                "checkpoint_id": snap.config["configurable"].get("checkpoint_id"),
                "next": list(snap.next),
                "stage": (snap.values.get("sdd") or {}).get("stage"),
            }
        )
        if len(out) >= limit:
            break
    return {"checkpoints": out}


# --- shared shape -----------------------------------------------------


def _summary(thread_id: str, values: dict[str, Any], snap=None) -> dict:
    sdd = values.get("sdd") or {}
    if snap is None:
        snap = graph().get_state(cfg(thread_id))

    waiting = any(
        isinstance(i.value, dict) and i.value.get("kind") == "human_gate"
        for i in (snap.interrupts or [])
    )
    return {
        "status": "waiting_human" if waiting else values.get("status"),
        "stage": sdd.get("stage"),
        "verify_passed": sdd.get("verify_passed"),
        "ready_open_count": sdd.get("ready_open_count"),
        "waiting_on_human": waiting,
        "spent_usd": (sdd.get("budget") or {}).get("spent_usd", 0.0),
        "spawns": len(sdd.get("spawn_history") or []),
        "todos": values.get("todos") or [],
        "files": sorted(values.get("files") or {}),
    }
