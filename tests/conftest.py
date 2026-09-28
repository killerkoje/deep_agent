"""Give every test its own workspace directory.

Artifacts live on disk now, so without this each test inherits whatever
the previous one left behind. That is not a tidiness problem: a test
asserting "verify refuses because crosscheck never ran" passed because
meta/crosscheck.json was still sitting there from an earlier test on
the same thread_id.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from deep_agent import config
from deep_agent import workspace as workspace_mod


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    scoped = replace(config.settings, workspace_root=root)
    monkeypatch.setattr(workspace_mod, "settings", scoped)
    monkeypatch.setattr(config, "settings", scoped)
    return root
