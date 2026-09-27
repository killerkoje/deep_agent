"""Checkpointer factory.

Short-term state, keyed by thread. Not the Store - that is long-term
memory keyed by namespace, and mixing them confuses two different
questions: "how far did this thread get?" versus "what did we learn?"
(docs/concepts.md 4).

InMemorySaver for local work, PostgresSaver once DATABASE_URL is set.
Swapping them is this one function, which is the payoff for not
hand-rolling persistence.
"""

from __future__ import annotations

from .config import settings

_SINGLETON = None


def get_checkpointer(database_url: str | None = None):
    """Postgres when configured, in-memory otherwise.

    A process restart loses nothing only in the Postgres case - that is
    what makes `waiting_human` survivable rather than a process you have
    to keep alive for days (SPEC 12.2).
    """
    url = database_url or settings.database_url
    if not url:
        from langgraph.checkpoint.memory import InMemorySaver

        return InMemorySaver()

    from langgraph.checkpoint.postgres import PostgresSaver  # type: ignore

    global _SINGLETON
    if _SINGLETON is None:
        cm = PostgresSaver.from_conn_string(url)
        _SINGLETON = cm.__enter__()
        _SINGLETON.setup()  # creates its own tables; we do not design them
    return _SINGLETON


def is_durable() -> bool:
    return bool(settings.database_url)
