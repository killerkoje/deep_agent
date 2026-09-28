"""Filesystem tools for sub-agents - real files, real directory.

These used to read and write a dict in state. They now work inside the
sub-agent's staged directory, which is what makes the artifacts visible
to anything that is not this Python process: git, Playwright, an
external CLI.

The directory is the boundary. A sub-agent is handed only the inputs
its skill contract declares, so "cannot read qa-report.md" is enforced
by it not being there, not by a check it might route around.

Main does not get these. Only sub-agents do.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

MAX_FILE_BYTES = 2 * 1024 * 1024  # documents; source trees live in repo/


def _workdir(state: dict) -> Path | None:
    raw = state.get("workdir")
    return Path(raw) if raw else None


def _safe(work: Path, rel: str) -> Path | None:
    """Refuse a path that climbs out. Model-authored strings land here."""
    target = (work / rel).resolve()
    return target if work.resolve() in target.parents or target == work.resolve() else None


@tool
def ls(state: Annotated[dict, InjectedState]) -> str:
    """List the files you can read."""
    work = _workdir(state)
    if work is None or not work.exists():
        return "(no workspace)"
    found = [
        f"{p.relative_to(work).as_posix()}  ({p.stat().st_size} bytes)"
        for p in sorted(work.rglob("*"))
        if p.is_file()
    ]
    return "\n".join(found) or "(no files)"


@tool
def read_file(path: str, state: Annotated[dict, InjectedState]) -> str:
    """Read one of the files available to you."""
    work = _workdir(state)
    if work is None:
        return "no workspace"
    target = _safe(work, path)
    if target is None or not target.is_file():
        have = [p.relative_to(work).as_posix() for p in work.rglob("*") if p.is_file()]
        return f"not found: {path}\navailable: {', '.join(have) or '(none)'}"
    return target.read_text(encoding="utf-8", errors="replace")


@tool
def write_file(
    path: str,
    content: str,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Write one of your skill's declared output files."""
    work = _workdir(state)
    if work is None:
        return Command(
            update={
                "messages": [ToolMessage("no workspace to write into", tool_call_id=tool_call_id)]
            }
        )

    target = _safe(work, path)
    if target is None:
        return Command(
            update={
                "messages": [
                    ToolMessage(f"rejected: {path} is outside your workspace", tool_call_id=tool_call_id)
                ]
            }
        )

    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        f"rejected: {path} exceeds {MAX_FILE_BYTES} bytes",
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return Command(
        update={
            "messages": [
                ToolMessage(f"wrote {path} ({len(content)} chars)", tool_call_id=tool_call_id)
            ]
        }
    )


SUBAGENT_TOOLS = {t.name: t for t in (ls, read_file, write_file)}


def resolve(names: tuple[str, ...]) -> list:
    return [SUBAGENT_TOOLS[n] for n in names if n in SUBAGENT_TOOLS]
