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

from . import gates
from .checkpoint import get_checkpointer, is_durable
from .config import settings
from .graph import build_graph
from .models_catalog import spawnable_models
from .state import initial_state

app = FastAPI(title="Deep Agent Orchestrator", version="0.5.0")

_GRAPH = None


def graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph(checkpointer=get_checkpointer())
    return _GRAPH


def cfg(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def auth(authorization: str = Header(default="")) -> None:
    if authorization != f"Bearer {settings.orch_api_token}":
        raise HTTPException(401, "bad or missing bearer token")


def snapshot(thread_id: str):
    snap = graph().get_state(cfg(thread_id))
    if not snap.values:
        raise HTTPException(404, f"no such thread: {thread_id}")
    return snap


# --- models ----------------------------------------------------------


class CreateThread(BaseModel):
    feature_id: str
    sources: dict[str, str] = Field(
        ..., description="stage 0 documents: PRD, mails, prior specs"
    )
    target_repo_path: str | None = None
    max_iterations: int = 3


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
        "durable": is_durable(),  # false means a restart loses running threads
    }


@app.get("/api/v1/models")
def models(_: None = Depends(auth)) -> dict:
    """What Main may choose from. Capability notes, no skill mapping."""
    return {"models": spawnable_models()}


@app.post("/api/v1/threads", status_code=201)
def create_thread(body: CreateThread, _: None = Depends(auth)) -> dict:
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
    files = snapshot(thread_id).values.get("files") or {}
    if name not in files:
        raise HTTPException(404, f"no such file: {name}")
    return {"path": name, "content": files[name]}


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

    answers = [a.model_dump() for a in body.answers]
    verdict = gates.check_answers_human(answers, actor="human", pending_ids=pending)
    if verdict is not None:
        raise HTTPException(400, str(verdict))

    out = graph().invoke(Command(resume=answers), cfg(thread_id))
    return {"thread_id": thread_id, **_summary(thread_id, out)}


@app.get("/api/v1/threads/{thread_id}/report")
def get_report(thread_id: str, _: None = Depends(auth)) -> dict:
    report = (snapshot(thread_id).values.get("files") or {}).get("report.md")
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
