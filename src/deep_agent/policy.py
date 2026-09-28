"""Who picks the model for a sub-agent. A person does, not the agent.

This reverses the original design, in which Main chose a model on every
spawn and escalated when a gate pushed back. Four live runs decided it:

  run 1  Main chose the top tier for all fifteen spawns - 35x the cost
         the work needed.
  run 3  Main proposed OpenAI ids against a Claude catalog, was refused
         six times, and ended the run. Its reasoning was sound; it had
         simply been handed a choice it could not make correctly.
  cli    Main escalated after a TIMEOUT, which says nothing about model
         capability. A slower model was the worst possible answer.
  run 4  Main started cheap and escalated once, for $0.69. This is the
         one time it worked.

One good outcome in four. Auto-selection sounds like the agentic
choice, but the agent has no signal for most of the decision: a gate
rejection tells it the output was wrong, never whether a bigger model
would have helped.

So the resolution order is fixed and knowable up front:

  1. a per-skill pin, if an operator set one
  2. the session-wide sub-agent model, if set
  3. Main's model

with one hard rule over all three: nothing runs above Main's price
tier. Whatever Main costs per call is the ceiling for the whole run.

`model` is gone from the spawn tool entirely rather than accepted and
ignored. An ignored parameter is a silent substitution, and silent
substitution is how a run finishes looking normal after being done by
a model nobody chose.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from .gates import GateReject


@dataclass(frozen=True)
class ModelPolicy:
    """What a session picked, at its three levels of specificity."""

    main_model: str
    subagent_model: str | None = None  # None -> inherit main_model
    skill_models: dict[str, str] = field(default_factory=dict)
    force_subagent_model: bool = False  # ignore pins, one model for all

    @property
    def default_subagent(self) -> str:
        return self.subagent_model or self.main_model

    def model_for(self, skill: str) -> str:
        if self.force_subagent_model:
            return self.default_subagent
        return self.skill_models.get(skill) or self.default_subagent

    def describe(self) -> str:
        lines = [
            f"Main runs on `{self.main_model}`.",
            f"Sub-agents run on `{self.default_subagent}` unless pinned otherwise.",
        ]
        if self.force_subagent_model:
            lines.append(
                f"The operator forced every sub-agent to `{self.default_subagent}`; "
                "per-skill pins are ignored."
            )
        elif self.skill_models:
            pins = ", ".join(f"{k}=`{v}`" for k, v in sorted(self.skill_models.items()))
            lines.append(f"Pinned: {pins}.")
        lines.append(
            "You do not choose models. `spawn` takes no `model` argument - the "
            "operator set this, and a failed spawn is a signal about the brief "
            "or the skill, not about the model."
        )
        return " ".join(lines)


def price_of(model: str, catalog: list[dict[str, Any]]) -> tuple[float, float] | None:
    for m in catalog:
        if m.get("id") == model:
            return (
                float(m.get("input_per_1m") or 0.0),
                float(m.get("output_per_1m") or 0.0),
            )
    return None


def _known(catalog: list[dict[str, Any]]) -> str:
    return ", ".join(sorted(m.get("id", "?") for m in catalog))


def validate_policy(
    policy: ModelPolicy, catalog: list[dict[str, Any]]
) -> GateReject | None:
    """Check the whole policy before a token is spent.

    Every failure here is a refusal with the valid options attached,
    never a fallback to something that happens to work. A run quietly
    downgraded to a cheaper model completes, reports normally, and
    tells nobody the work was not what was asked for.
    """
    main_price = price_of(policy.main_model, catalog)
    if main_price is None:
        return GateReject(
            "G_MODEL_POLICY",
            f"main_model {policy.main_model!r} is not available on this backend. "
            f"Choose one of: {_known(catalog)}",
        )

    for label, model in [
        ("subagent_model", policy.subagent_model),
        *[(f"skill_models[{s}]", m) for s, m in sorted(policy.skill_models.items())],
    ]:
        if model is None:
            continue

        price = price_of(model, catalog)
        if price is None:
            return GateReject(
                "G_MODEL_POLICY",
                f"{label} {model!r} is not available on this backend. "
                f"Choose one of: {_known(catalog)}",
            )
        if price[0] > main_price[0] or price[1] > main_price[1]:
            return GateReject(
                "G_MODEL_POLICY",
                f"{label} {model!r} costs more than main_model "
                f"{policy.main_model!r}. Sub-agents are capped at Main's tier - "
                f"raise main_model instead.",
            )
    return None


def validate_skills(policy: ModelPolicy, known_skills) -> GateReject | None:
    """A pin for a skill that does not exist would silently never apply."""
    unknown = sorted(set(policy.skill_models) - set(known_skills))
    if unknown:
        return GateReject(
            "G_MODEL_POLICY",
            f"skill_models names unknown skill(s): {', '.join(unknown)}. "
            f"Known skills: {', '.join(known_skills)}",
        )
    return None


def resolve_spawn_model(
    skill: str, policy: ModelPolicy | None, catalog: list[dict[str, Any]]
) -> tuple[str | None, GateReject | None]:
    """The model this spawn will run on, or why it cannot run."""
    if policy is None:
        return (None, None)  # local runs without a session

    model = policy.model_for(skill)
    if price_of(model, catalog) is None:
        return (
            model,
            GateReject(
                "G_MODEL_POLICY",
                f"{model!r} is not available on this backend. "
                f"Choose one of: {_known(catalog)}",
                skill,
            ),
        )
    return (model, None)


# --- per-thread storage ------------------------------------------------
# Alongside credentials, and for the same reason: never in AgentState,
# which gets checkpointed to disk and later to Postgres.

_policies: dict[str, ModelPolicy] = {}
_lock = threading.Lock()


def set_policy(thread_id: str, policy: ModelPolicy | None) -> None:
    with _lock:
        if policy is None:
            _policies.pop(thread_id, None)
        else:
            _policies[thread_id] = policy


def get_policy(thread_id: str) -> ModelPolicy | None:
    with _lock:
        return _policies.get(thread_id)
