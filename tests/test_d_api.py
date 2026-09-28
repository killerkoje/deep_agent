"""D - the HTTP API.

The route that matters is POST /human-gate. Before it existed the gate
could park a run and nothing outside a test could release it.

It is also the proof that pulling gates out of the spawn tool was
necessary rather than tidy: this handler has no graph context, no
tool_call_id and nowhere to return a Command, yet it runs the same
gates.check_answers_human and just translates the verdict into a 400.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from deep_agent import app as app_mod
from deep_agent.graph import build_graph
from deep_agent.tools import spawn as spawn_mod
from tests.fakes import ScriptedChatModel, tool_call
from tests.test_s7_pipeline import _CURRENT_SKILL, DECISIONS_SNEAKY, HAPPY, route_sub

TOKEN = {"Authorization": "Bearer change-me"}


@pytest.fixture
def api(monkeypatch):
    """A client wired to scripted models, sharing one graph so the
    checkpointer persists across requests the way it would in a server."""
    factory, _ = route_sub(DECISIONS_SNEAKY)

    real = spawn_mod.spawn.func

    def patched(skill, model, brief, state, tool_call_id):
        _CURRENT_SKILL.set(skill)
        return real(skill, model, brief, state, tool_call_id)

    spawn_mod.spawn.func = patched
    spawn_mod.set_model_factory(factory)

    main = ScriptedChatModel(HAPPY[:-1], final_text="parked")
    graph = build_graph(model_factory=lambda: main)
    monkeypatch.setattr(app_mod, "_GRAPH", graph)

    # This graph runs on injected models, so from its point of view the
    # credentials are present. The real check is covered separately.
    monkeypatch.setattr(
        app_mod, "settings", replace(app_mod.settings, openai_api_key="test-key")
    )

    try:
        yield TestClient(app_mod.app)
    finally:
        spawn_mod.spawn.func = real
        spawn_mod.set_model_factory(None)


def start(api) -> str:
    r = api.post(
        "/api/v1/threads",
        headers=TOKEN,
        json={"feature_id": "crm", "sources": {"prd.md": "# PRD"}},
    )
    assert r.status_code == 201, r.text
    return r.json()["thread_id"]


# --- basics ----------------------------------------------------------


def test_health_reports_whether_state_survives_restart(api):
    body = api.get("/health").json()
    assert body["ok"] is True
    assert body["durable"] is False  # no DATABASE_URL locally


def test_auth_is_required(api):
    assert api.get("/api/v1/models").status_code == 401
    assert api.get("/api/v1/models", headers=TOKEN).status_code == 200


def test_models_endpoint_exposes_capability_not_assignment(api):
    models = api.get("/api/v1/models", headers=TOKEN).json()["models"]
    ids = {m["id"] for m in models}

    assert "gpt-6-astra" in ids
    assert "text-embedding-3-small" not in ids  # not spawnable
    for m in models:
        assert not ({"default_for_skill", "role", "skills"} & set(m))


def test_unknown_thread_is_404(api):
    assert api.get("/api/v1/threads/nope", headers=TOKEN).status_code == 404


# --- the human gate round trip ---------------------------------------


def test_run_parks_on_a_permission_decision(api):
    tid = start(api)

    body = api.get(f"/api/v1/threads/{tid}", headers=TOKEN).json()
    assert body["waiting_on_human"] is True
    assert body["status"] == "waiting_human"

    gate = api.get(f"/api/v1/threads/{tid}/human-gate", headers=TOKEN).json()
    assert gate["waiting"] is True
    assert [i["id"] for i in gate["items"]] == ["D-013"]


def test_partial_answer_is_refused(api):
    tid = start(api)
    r = api.post(
        f"/api/v1/threads/{tid}/human-gate",
        headers=TOKEN,
        json={"answers": []},
    )
    assert r.status_code == 400
    assert "still open" in r.json()["detail"]


def test_ai_authored_answer_is_refused(api):
    tid = start(api)
    r = api.post(
        f"/api/v1/threads/{tid}/human-gate",
        headers=TOKEN,
        json={"answers": [{"item_id": "D-013", "choice": "① (추정)"}]},
    )
    assert r.status_code == 400
    assert "AI-authored" in r.json()["detail"]


def test_answering_releases_the_run(api):
    tid = start(api)
    r = api.post(
        f"/api/v1/threads/{tid}/human-gate",
        headers=TOKEN,
        json={"answers": [{"item_id": "D-013", "choice": "① 지점명 마스킹"}]},
    )
    assert r.status_code == 200
    assert r.json()["waiting_on_human"] is False

    after = api.get(f"/api/v1/threads/{tid}", headers=TOKEN).json()
    assert after["waiting_on_human"] is False


def test_answering_a_thread_that_is_not_parked_is_409(api):
    tid = start(api)
    api.post(
        f"/api/v1/threads/{tid}/human-gate",
        headers=TOKEN,
        json={"answers": [{"item_id": "D-013", "choice": "① 마스킹"}]},
    )
    again = api.post(
        f"/api/v1/threads/{tid}/human-gate",
        headers=TOKEN,
        json={"answers": [{"item_id": "D-013", "choice": "② 비노출"}]},
    )
    assert again.status_code == 409


# --- artifacts --------------------------------------------------------


def test_files_are_readable_while_the_run_is_parked(api):
    tid = start(api)
    listing = api.get(f"/api/v1/threads/{tid}", headers=TOKEN).json()["files"]
    assert "spec.md" in listing and "decisions.md" in listing

    body = api.get(f"/api/v1/threads/{tid}/files/spec.md", headers=TOKEN).json()
    assert "신규 고객" in body["content"]

    assert api.get(f"/api/v1/threads/{tid}/files/nope.md", headers=TOKEN).status_code == 404


def test_report_is_404_until_the_run_finishes(api):
    tid = start(api)
    assert api.get(f"/api/v1/threads/{tid}/report", headers=TOKEN).status_code == 404


def test_history_shows_the_checkpoints(api):
    tid = start(api)
    checkpoints = api.get(f"/api/v1/threads/{tid}/history", headers=TOKEN).json()[
        "checkpoints"
    ]
    assert len(checkpoints) > 3
    assert {c["stage"] for c in checkpoints} & {"1-spec-draft", "6-ready"}


def test_missing_api_key_is_a_clear_503(api, monkeypatch):
    """A predictable, user-fixable condition should read as one - not as
    a 500 carrying a provider stack trace."""
    monkeypatch.setattr(
        app_mod, "settings", replace(app_mod.settings, openai_api_key=None)
    )
    r = api.post(
        "/api/v1/threads",
        headers=TOKEN,
        json={"feature_id": "crm", "sources": {"prd.md": "# PRD"}},
    )
    assert r.status_code == 503
    assert "OPENAI_API_KEY" in r.json()["detail"]


def test_health_reports_whether_credentials_are_present(api):
    assert api.get("/health").json()["model_credentials"] is True
