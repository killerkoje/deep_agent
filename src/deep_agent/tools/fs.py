"""Filesystem tools for sub-agents.

These read and write the *documents* in state - spec.md, questions,
decisions. Code never comes through here: it lives on real disk under
`workspace`, because git, pytest and Playwright cannot read a dict, and
a source tree in state would bloat every checkpoint (SPEC 5.3).

Main does not get these. Only sub-agents do.
"""

from __future__ import annotations

from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

# A single document over this goes to disk instead (SPEC 5.3, rule 2).
MAX_FILE_BYTES = 256 * 1024


@tool
def ls(state: Annotated[dict, InjectedState]) -> str:
    """List the files available to you."""
    files = state.get("files") or {}
    if not files:
        return "(no files)"
    return "\n".join(f"{p}  ({len(c)} chars)" for p, c in sorted(files.items()))


@tool
def read_file(path: str, state: Annotated[dict, InjectedState]) -> str:
    """Read one of the files available to you."""
    files = state.get("files") or {}
    if path not in files:
        return f"not found: {path}\navailable: {', '.join(sorted(files)) or '(none)'}"
    return files[path]


@tool
def write_file(
    path: str,
    content: str,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Write one of your skill's declared output files."""
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        f"rejected: {path} exceeds {MAX_FILE_BYTES} bytes. "
                        "Large artifacts belong on disk, not in state.",
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )

    files = dict(state.get("files") or {})
    files[path] = content
    return Command(
        update={
            "files": files,
            "messages": [
                ToolMessage(f"wrote {path} ({len(content)} chars)", tool_call_id=tool_call_id)
            ],
        }
    )


# Resolved by name from a skill's `tool_names`.
SUBAGENT_TOOLS = {t.name: t for t in (ls, read_file, write_file)}


def resolve(names: tuple[str, ...]) -> list:
    return [SUBAGENT_TOOLS[n] for n in names if n in SUBAGENT_TOOLS]
