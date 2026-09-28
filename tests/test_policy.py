"""Session model policy.

The rule with history behind it is the third one: an out-of-policy
model is an error, never a quiet substitution. This repo has already
shipped that bug twice in another shape - a parser returning 0 instead
of "cannot read", and one returning [] instead of "no record" - and
both times a gate passed something it should have refused. A silent
model downgrade is the same failure with a bigger blast radius,
because the run completes and the report looks entirely normal.
"""

from __future__ import annotations

from deep_agent.policy import (
    ModelPolicy,
    price_of,
    resolve_spawn_model,
    validate_policy,
)

CATALOG = [
    {"id": "haiku", "input_per_1m": 1.0, "output_per_1m": 5.0},
    {"id": "sonnet", "input_per_1m": 3.0, "output_per_1m": 15.0},
    {"id": "opus", "input_per_1m": 5.0, "output_per_1m": 25.0},
    {"id": "fable", "input_per_1m": 10.0, "output_per_1m": 50.0},
]


# --- inheritance ------------------------------------------------------


def test_subagents_inherit_main_when_unset():
    """One choice is enough to start."""
    p = ModelPolicy(main_model="sonnet")
    assert p.default_subagent == "sonnet"
    assert resolve_spawn_model(None, p, CATALOG) == ("sonnet", None)


def test_an_explicit_subagent_model_is_used():
    p = ModelPolicy(main_model="opus", subagent_model="haiku")
    assert p.default_subagent == "haiku"
    assert resolve_spawn_model(None, p, CATALOG) == ("haiku", None)


def test_a_pin_beats_the_session_default():
    """The operator pins the light roles; everything else inherits."""
    p = ModelPolicy(
        main_model="opus", subagent_model="sonnet", skill_models={"spec-write": "haiku"}
    )
    assert resolve_spawn_model("spec-write", p, CATALOG) == ("haiku", None)
    assert resolve_spawn_model("crosscheck", p, CATALOG) == ("sonnet", None)


def test_main_cannot_choose_at_all():
    """Resolution takes the skill, not a requested model. There is
    nowhere for an agent preference to enter."""
    import inspect

    assert list(inspect.signature(resolve_spawn_model).parameters)[0] == "skill"


# --- the ceiling ------------------------------------------------------


def test_a_subagent_may_not_cost_more_than_main():
    """Whatever Main costs per call is the worst case for the whole run."""
    rej = validate_policy(ModelPolicy(main_model="sonnet", subagent_model="opus"), CATALOG)
    assert rej.code == "G_MODEL_POLICY"
    assert "costs more than" in rej.message


def test_equal_tier_is_allowed():
    assert validate_policy(ModelPolicy("opus", "opus"), CATALOG) is None


def test_a_pin_above_main_is_refused_at_session_start():
    """The ceiling is checked once, before a token is spent."""
    rej = validate_policy(
        ModelPolicy(main_model="haiku", skill_models={"openspec": "fable"}), CATALOG
    )
    assert rej.code == "G_MODEL_POLICY"
    assert "capped at Main's tier" in rej.message
    assert "openspec" in rej.message


# --- unknown models ---------------------------------------------------


def test_unknown_main_model_is_rejected_with_the_options():
    rej = validate_policy(ModelPolicy(main_model="gpt-6-luna"), CATALOG)
    assert rej.code == "G_MODEL_POLICY"
    assert "not available" in rej.message
    assert "haiku, opus" in rej.message  # tells you what you can pick


def test_unknown_subagent_model_is_rejected():
    rej = validate_policy(ModelPolicy("opus", "gpt-4"), CATALOG)
    assert rej is not None and "not available" in rej.message


def test_unknown_model_at_spawn_time_is_rejected():
    """Reported, not replaced - a substitution nobody is told about is
    how a run finishes looking normal after running weaker than asked."""
    p = ModelPolicy(main_model="opus", skill_models={"qa": "made-up"})
    model, verdict = resolve_spawn_model("qa", p, CATALOG)
    assert verdict.code == "G_MODEL_POLICY"
    assert model == "made-up"


def test_a_pin_for_an_unknown_skill_is_refused():
    """It would silently never apply, and the operator would believe
    they had set it."""
    from deep_agent.policy import validate_skills

    rej = validate_skills(
        ModelPolicy("opus", skill_models={"speck-write": "haiku"}),
        ["spec-write", "qa"],
    )
    assert rej.code == "G_MODEL_POLICY"
    assert "speck-write" in rej.message


# --- the force switch -------------------------------------------------


def test_force_overrides_even_the_pins():
    p = ModelPolicy(
        main_model="opus",
        subagent_model="haiku",
        skill_models={"openspec": "opus"},
        force_subagent_model=True,
    )
    for skill in ("openspec", "qa", "implement"):
        assert resolve_spawn_model(skill, p, CATALOG) == ("haiku", None)


def test_force_is_announced_rather_than_silent():
    described = ModelPolicy(
        "opus", "haiku", force_subagent_model=True
    ).describe()
    assert "haiku" in described and "forced" in described


def test_main_is_told_it_does_not_choose():
    described = ModelPolicy("opus", "sonnet").describe()
    assert "sonnet" in described and "opus" in described
    assert "You do not choose models" in described


# --- helpers ----------------------------------------------------------


def test_price_lookup():
    assert price_of("sonnet", CATALOG) == (3.0, 15.0)
    assert price_of("nope", CATALOG) is None


def test_no_policy_defers_to_the_caller():
    """Local runs without a session fall back to the server default,
    which spawn supplies - resolution reports None rather than guessing."""
    assert resolve_spawn_model("qa", None, CATALOG) == (None, None)


# --- credentials must not fall back to the operator's ------------------


def test_supplied_but_empty_credentials_are_refused():
    """A caller who passes credentials must be usable on their own.

    Falling through to the server's key would run their work on the
    operator's account and bill the operator, with nothing in the
    response saying so. The same silent-substitution shape as a model
    downgrade, with someone else's money attached.
    """
    from deep_agent.backends import Credentials
    from deep_agent.backends.langgraph_backend import LangGraphBackend

    backend = LangGraphBackend()
    why = backend.available(Credentials(provider="openai", api_key=""))
    assert why is not None
    assert "own credentials" in why


def test_no_credentials_at_all_uses_the_server_default():
    """Local and single-tenant use still works."""
    from dataclasses import replace

    from deep_agent import config
    from deep_agent.backends.langgraph_backend import LangGraphBackend

    original = config.settings
    config.settings = replace(original, openai_api_key="sk-test")
    try:
        assert LangGraphBackend().available(None) is None
    finally:
        config.settings = original
