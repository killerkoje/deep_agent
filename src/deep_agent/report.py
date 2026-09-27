"""The total report - stage 9.

Two audiences. A person asking "what did this run actually decide, and
on whose authority?", and a later run asking the same thing. So every
[AI 결정] appears with its grounds, and low-confidence ones are pulled
to the top rather than buried - if anything here is wrong, that is
where it will be.
"""

from __future__ import annotations

from typing import Any


def _fmt_usd(x: float) -> str:
    return f"${x:,.4f}"


def build_report(state: dict[str, Any], summary: str = "") -> str:
    sdd: dict[str, Any] = state.get("sdd") or {}
    decisions: list[dict] = sdd.get("decisions") or []
    history: list[dict] = sdd.get("spawn_history") or []
    e2e: dict = state.get("e2e") or {}

    ai = [d for d in decisions if d.get("tag") == "[AI 결정]"]
    human = [d for d in decisions if d.get("tag") == "[답변]"]
    low = [d for d in ai if d.get("confidence") == "하"]

    out: list[str] = [
        f"# Run report — {sdd.get('feature_id', '?')}",
        "",
        f"- run_id: `{sdd.get('run_id')}`",
        f"- thread_id: `{sdd.get('thread_id')}`",
        f"- status: **{state.get('status')}**  ·  stage: {sdd.get('stage')}",
        f"- verify_passed: **{sdd.get('verify_passed')}**"
        + (f"  ·  spec `{(sdd.get('spec_hash') or '')[:12]}`" if sdd.get("spec_hash") else ""),
        f"- E2E: **{e2e.get('status', 'not_run')}**"
        + (f" ({e2e.get('passed_count', 0)} passed)" if e2e.get("tests") else ""),
        f"- loop: {state.get('iteration', 0)}/{state.get('max_iterations', 3)}",
        "",
    ]

    if summary:
        out += ["## Summary", "", summary, ""]

    # Low confidence first. If something here is wrong, it is here.
    if low:
        out += [
            "## ⚠ Low-confidence decisions — read these first",
            "",
            "| id | decision | grounds | rejected alternative |",
            "|----|----------|---------|----------------------|",
        ]
        out += [
            f"| {d.get('id')} | {d.get('text', '')} | {d.get('rationale', '')} "
            f"| {d.get('alternatives', '')} |"
            for d in low
        ]
        out += [""]

    out += [
        "## Decisions",
        "",
        f"{len(ai)} by the agent, {len(human)} by a person "
        f"(permission/billing only), {len(decisions)} total.",
        "",
        "| id | by | category | decision | grounds |",
        "|----|----|----------|----------|---------|",
    ]
    for d in decisions:
        out.append(
            f"| {d.get('id')} | {d.get('tag', '')} | {d.get('category', '')} "
            f"| {d.get('text', '')} | {d.get('rationale') or d.get('answer') or ''} |"
        )
    out += [""]

    out += [
        "## Sub-agents",
        "",
        "Which skill ran on which model, and what it cost. There is no",
        "role-to-model table - every one of these was chosen per call.",
        "",
        "| skill | model | tokens in/out | cost | brief |",
        "|-------|-------|---------------|------|-------|",
    ]
    for h in history:
        out.append(
            f"| {h.get('skill')} | `{h.get('model')}` "
            f"| {h.get('tokens_in', 0)}/{h.get('tokens_out', 0)} "
            f"| {_fmt_usd(h.get('cost_usd') or 0)} | {(h.get('brief') or '')[:60]} |"
        )
    spent = (sdd.get("budget") or {}).get("spent_usd") or 0.0
    out += ["", f"**Total: {_fmt_usd(spent)}** across {len(history)} spawn(s).", ""]

    if rejects := sdd.get("gate_rejects"):
        out += ["## Gate rejections", ""]
        out += [
            f"- `{r.get('code')}` ({r.get('skill') or '-'}) — {r.get('message')}"
            for r in rejects
        ]
        out += [""]

    if todos := state.get("todos"):
        done = sum(1 for t in todos if t.get("status") == "done")
        out += ["## Plan", "", f"{done}/{len(todos)} complete", ""]
        out += [
            f"- [{'x' if t.get('status') == 'done' else ' '}] {t.get('id')} {t.get('text')}"
            for t in todos
        ]
        out += [""]

    out += [
        "## Artifacts",
        "",
        *[f"- `{p}`" for p in sorted(state.get("files") or {})],
        "",
    ]
    return "\n".join(out)
