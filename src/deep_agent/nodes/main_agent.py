"""The main_agent node - Main's one turn.

Read state, decide ONE next action, emit it as a tool call. That is the
whole job. Main never writes spec or code itself; it has no write_file,
no bash, no git_commit. The restriction is enforced by tool absence
rather than by prompt wording (SPEC 8.3).
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import SystemMessage

from .. import skills_loader
from ..llm import get_main_model, usage_from_message
from ..tools.todos import recite

SYSTEM_PROMPT = """\
You are Main, the top-level orchestrator of an SDD pipeline.

You do not write specs or code. You spawn sub-agents that do, and you
decide which skill and when. Exactly one tool call per turn.

## Hard rules

1. You never author spec or code content. Only spawn.
2. You do not choose models. `spawn` takes no `model` argument - the
   operator picked one for this session. A gate rejection tells you the
   output was wrong; it never tells you a bigger model would have
   helped. Fix the brief, or pick a different skill.
3. Blanks in the spec are YOURS to fill via the `decide` skill. Do not
   stop to ask a human. But every `[AI 결정]` must carry a citation, the
   alternative you rejected, and a confidence level - the code rejects
   decisions without them.
4. You do NOT decide permission/security or money/billing questions. The
   gate halts the run for those. Marking one as "일반" does not get past
   it: the gate rescans independently and takes the union. Do not try.
5. If `verify_passed` is false or `ready_open_count` > 0, do not choose
   `implement` or `qa` - the code will reject it.
6. Hole-finding runs BOTH `openspec` and `spec-kit`. Both must finish.
7. Never split `implement` across parallel sub-agents. One at a time.
8. After `qa.failed`, run `e2e-triage` before re-implementing. Going
   straight back to `implement` lets a spec gap get filled by a guess.
9. On failure, replan: sharpen the brief and retry, or pick a different
   skill. Say why. Retrying the identical brief is not a plan.

## Pipeline (the usual path)

0 sources -> 1 spec-write -> 2 openspec ∥ spec-kit -> 3 decide
  -> 4 spec-rereview -> 5 crosscheck -> 6 ready-audit
  -> [gate: permission/billing stop] -> 7 implement -> 8 qa
  -> 8b e2e-triage (on failure) -> 9 report

Finish by calling `finish`. Give up only via `fail_run`.
"""


def build_context(state: dict[str, Any]) -> list:
    """System prompt + history + the three things Main must see every turn.

    `recite(todos)` is the important one. The plan is re-injected into
    RECENT context on every single turn - not pinned once in the system
    prompt - because that is what keeps a long run on target.
    """
    sdd = state.get("sdd") or {}

    status_block = "\n".join(
        [
            "## Run status",
            f"stage:             {sdd.get('stage')}",
            f"verify_passed:     {sdd.get('verify_passed')}",
            f"ready_open_count:  {sdd.get('ready_open_count')}",
            f"last_event:        {sdd.get('last_event')}",
            f"iteration:         {state.get('iteration', 0)}/{state.get('max_iterations', 3)}",
            f"spent_usd:         {(sdd.get('budget') or {}).get('spent_usd', 0)}",
            f"files:             {', '.join(sorted(state.get('files') or {})) or '(none)'}",
        ]
    )

    blocks = [status_block, recite(state.get("todos") or [])]

    rejects = (sdd.get("gate_rejects") or [])[-3:]
    if rejects:
        blocks.append(
            "## Recent gate rejections (do not retry the same wall)\n"
            + "\n".join(f"- {r.get('code')}: {r.get('message')}" for r in rejects)
        )

    # The list Main sees must be the list the gate checks. When the two
    # drift, Main spends the run proposing models G_MODEL_KNOWN will
    # reject - on the first run against the CLI backend it tried six
    # OpenAI ids, was refused each time, and gave up. Correctly.
    blocks.append("## Available models\n" + _models_block(state))

    return [
        SystemMessage(SYSTEM_PROMPT),
        *state.get("messages", []),
        SystemMessage("\n\n".join(blocks)),
    ]


def _models_block(state: dict[str, Any]) -> str:
    from .. import backends
    from ..policy import get_policy

    thread_id = state.get("thread_id") or ""
    creds = backends.get_credentials(thread_id)
    backend = backends.backend_for(creds)

    lines = [f"(sub-agents run on the `{backend.name}` backend)"]

    # Tell Main the policy rather than silently overriding its choices -
    # otherwise it spends turns escalating against a ceiling it cannot see.
    if session := get_policy(thread_id):
        lines.append(session.describe())
        lines.append("")
    for m in backend.models():
        price = ""
        if m.get("input_per_1m") is not None:
            price = f" | in ${m['input_per_1m']}/1M out ${m.get('output_per_1m')}/1M"
        lines.append(f"- {m['id']}{price}")
        if note := m.get("notes"):
            lines.append(f"    {note}")
    return "\n".join(lines)


def make_main_agent_node(tools: list, model_factory=get_main_model):
    """`model_factory` is injectable so tests can drive the loop without
    an API key."""

    def main_agent(state: dict[str, Any]) -> dict[str, Any]:
        llm = model_factory().bind_tools(tools)
        reply = llm.invoke(build_context(state))

        tokens_in, tokens_out = usage_from_message(reply)
        update: dict[str, Any] = {"messages": [reply]}

        if tokens_in or tokens_out:
            from ..config import settings
            from ..llm import _price

            sdd = dict(state.get("sdd") or {})
            budget = dict(sdd.get("budget") or {})
            budget["spent_usd"] = round(
                float(budget.get("spent_usd") or 0.0)
                + _price(settings.main_model, tokens_in, tokens_out),
                6,
            )
            sdd["budget"] = budget
            update["sdd"] = sdd

        return update

    return main_agent
