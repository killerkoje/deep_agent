"""Backend registry and per-thread credentials.

Credentials live here, in process memory, keyed by thread. They are
deliberately NOT in AgentState: state is checkpointed to disk today and
to Postgres later, and a checkpoint carrying someone's API key is a
leak sitting in a backup waiting to be read.

The consequence is honest rather than hidden - after a restart, a
parked run needs its credentials supplied again before it can resume.
That is the correct trade.
"""

from __future__ import annotations

import threading

from .base import Backend, Credentials, SubagentResult
from .claude_cli import ClaudeCliBackend
from .langgraph_backend import LangGraphBackend

__all__ = [
    "Backend",
    "Credentials",
    "SubagentResult",
    "get_backend",
    "available_backends",
    "set_credentials",
    "get_credentials",
    "clear_credentials",
    "set_model_factory",
]

_BACKENDS: dict[str, Backend] = {
    "claude-cli": ClaudeCliBackend(),
    "langgraph": LangGraphBackend(),
}

_creds: dict[str, Credentials] = {}
_lock = threading.Lock()


def get_backend(name: str | None = None) -> Backend:
    from ..config import settings

    key = name or getattr(settings, "subagent_backend", None) or "langgraph"
    if key not in _BACKENDS:
        raise KeyError(f"unknown backend {key!r}; have {', '.join(_BACKENDS)}")
    return _BACKENDS[key]


def backend_for(creds: Credentials | None, default: str | None = None) -> Backend:
    """What the caller brought decides which backend runs their work."""
    if creds is not None:
        if creds.provider in _BACKENDS:
            return _BACKENDS[creds.provider]
        if creds.provider in {"openai", "anthropic"}:
            return _BACKENDS["langgraph"]
    return get_backend(default)


def available_backends() -> list[dict]:
    out = []
    for name, backend in _BACKENDS.items():
        why = backend.available(None)
        out.append(
            {
                "name": name,
                "ready": why is None,
                "reason": why,
                "models": [m["id"] for m in backend.models()] if why is None else [],
            }
        )
    return out


# --- per-thread credentials ------------------------------------------


def set_credentials(thread_id: str, creds: Credentials | None) -> None:
    with _lock:
        if creds is None:
            _creds.pop(thread_id, None)
        else:
            _creds[thread_id] = creds


def get_credentials(thread_id: str) -> Credentials | None:
    with _lock:
        return _creds.get(thread_id)


def clear_credentials(thread_id: str) -> None:
    set_credentials(thread_id, None)


def set_model_factory(factory) -> None:
    """Tests drive the langgraph backend with scripted models."""
    _BACKENDS["langgraph"] = LangGraphBackend(model_factory=factory)
