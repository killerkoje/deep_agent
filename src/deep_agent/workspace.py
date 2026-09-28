"""The run's files, on disk.

Artifacts used to be strings in `AgentState.files`, which meant nothing
outside this Python process could see them. git cannot commit a dict,
Playwright cannot open one, and an external CLI cannot read one - which
is why `implement` and `qa` were never buildable.

    workspace/{thread_id}/
    ├── artifacts/          spec.md, decisions.md, sources/, meta/
    ├── spawns/{spawn_id}/  what one sub-agent was allowed to see
    └── repo/               the target repo, when there is one

State keeps an INDEX (path -> sha256), never content. Three reasons:
checkpoints stay small, drift is detectable for free, and the gate that
asks "did the spec change since it was verified" becomes a hash compare
rather than a stored copy.

Gates still take content dicts, so they stay pure - callers read the
few files a gate needs and hand them over.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from .config import settings


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class OutsideWorkspace(ValueError):
    """A path that tried to escape the run's directory."""


@dataclass(frozen=True)
class Workspace:
    root: Path

    # --- construction -------------------------------------------------

    @classmethod
    def for_thread(cls, thread_id: str, base: Path | None = None) -> "Workspace":
        safe = "".join(c for c in thread_id if c.isalnum() or c in "-_.")
        ws = cls((base or settings.workspace_root) / safe)
        ws.artifacts.mkdir(parents=True, exist_ok=True)
        return ws

    @property
    def artifacts(self) -> Path:
        return self.root / "artifacts"

    @property
    def repo(self) -> Path:
        return self.root / "repo"

    # --- paths --------------------------------------------------------

    def resolve(self, rel: str) -> Path:
        """Absolute path for a workspace-relative one, refusing escapes.

        Sub-agent output paths are model-authored strings; `../../.ssh`
        must not resolve.
        """
        target = (self.artifacts / rel).resolve()
        root = self.artifacts.resolve()
        if root != target and root not in target.parents:
            raise OutsideWorkspace(f"{rel!r} resolves outside the workspace")
        return target

    # --- reading / writing --------------------------------------------

    def exists(self, rel: str) -> bool:
        try:
            return self.resolve(rel).is_file()
        except OutsideWorkspace:
            return False

    def read(self, rel: str) -> str | None:
        try:
            p = self.resolve(rel)
        except OutsideWorkspace:
            return None
        return p.read_text(encoding="utf-8") if p.is_file() else None

    def read_many(self, paths) -> dict[str, str]:
        """Content for the gates, which stay content-based and pure."""
        out: dict[str, str] = {}
        for rel in paths:
            if (body := self.read(rel)) is not None:
                out[rel] = body
        return out

    def write(self, rel: str, content: str) -> str:
        p = self.resolve(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return sha256(content)

    def list(self) -> list[str]:
        if not self.artifacts.exists():
            return []
        return sorted(
            p.relative_to(self.artifacts).as_posix()
            for p in self.artifacts.rglob("*")
            if p.is_file()
        )

    def index(self) -> dict[str, str]:
        """path -> sha256. This is what lives in AgentState."""
        return {rel: sha256(self.read(rel) or "") for rel in self.list()}

    # --- per-spawn isolation ------------------------------------------

    def stage_spawn(self, spawn_id: str, patterns) -> Path:
        """A directory holding ONLY what this skill declared as input.

        The dict version filtered a mapping; on disk the equivalent is a
        directory the sub-agent cannot see past. It also gives an
        external CLI process something real to be pointed at.
        """
        work = self.root / "spawns" / spawn_id
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)

        for rel in self.list():
            if any(fnmatch(rel, pat) for pat in patterns):
                dst = work / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.resolve(rel), dst)
        return work

    def collect(self, work: Path, declared) -> list[str]:
        """Pull back only the outputs the skill contract names.

        A sub-agent writing wherever it likes is how one skill quietly
        overwrites another's artifact.
        """
        taken: list[str] = []
        for p in sorted(work.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(work).as_posix()
            if not any(fnmatch(rel, d) or rel == d for d in declared):
                continue
            try:
                self.write(rel, p.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, OutsideWorkspace):
                continue
            taken.append(rel)
        return taken
