"""Gates - fail-closed checks. Pure functions, stdlib only.

Three rules this module lives by:

1. NO langgraph import. Enforced by a CI grep (SPEC 13.1 #10). Callers
   translate a verdict into whatever their world needs: the spawn tool
   into a ToolMessage, the HTTP handler into a 400, CI into an exit code.
   The HTTP handler has no graph context at all, which is why this has
   to be callable from outside.

2. NO model calls. A gate that asks an LLM "is this ok?" is not a gate,
   it is another opinion - it can be talked round, and it answers
   differently on the same input twice. Where judgment IS needed (are
   two sections contradictory? is this a permission decision?), a
   sub-agent makes that judgment BEFORE the gate and leaves an artifact;
   the gate then counts the artifact (docs/gates.md).

3. Verdict only, no side effects. verify_spec() decides pass/fail and
   computes the hash; writing the token file is the caller's job. Mixing
   the two lets a gate start repairing what it is meant to be judging.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# --------------------------------------------------------------------
# verdict types
# --------------------------------------------------------------------


@dataclass(frozen=True)
class GateReject:
    code: str
    message: str = ""
    skill: str | None = None

    def __str__(self) -> str:
        return f"{self.code} — {self.message}" if self.message else self.code


@dataclass
class VerifyResult:
    passed: bool
    spec_hash: str | None = None
    reasons: list[str] = field(default_factory=list)


def spec_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def verify_token(run_id: str, digest: str) -> str:
    return f"VERIFY_OK:{run_id}:{digest}"


# --------------------------------------------------------------------
# G_MODEL_KNOWN / G_SKILL_KNOWN / G_NO_IMPL_WITHOUT_VERIFY /
# G_READY / G_BUDGET  - everything checked before a spawn
# --------------------------------------------------------------------


def allow_spawn(
    sdd: dict[str, Any],
    skill: str,
    model: str,
    catalog: Iterable[str],
    known_skills: Iterable[str],
) -> GateReject | None:
    """Every check that must pass before a sub-agent is started."""
    if skill not in set(known_skills):
        return GateReject(
            "G_SKILL_KNOWN",
            f"{skill!r} is not a known skill",
            skill,
        )

    if model not in set(catalog):
        return GateReject(
            "G_MODEL_KNOWN",
            f"{model!r} is not in the catalog",
            skill,
        )

    if skill in {"implement", "qa"} and not sdd.get("verify_passed"):
        return GateReject(
            "G_NO_IMPL_WITHOUT_VERIFY",
            "verify_passed=false",
            skill,
        )

    if skill == "implement":
        open_count = sdd.get("ready_open_count")
        if open_count is None:
            return GateReject(
                "G_READY", "ready-audit has not run yet", skill
            )
        if open_count > 0:
            return GateReject(
                "G_READY", f"ready.md section 1 still has {open_count} open", skill
            )

    budget = sdd.get("budget") or {}
    cap = budget.get("max_usd")
    if cap is not None and float(budget.get("spent_usd") or 0.0) >= float(cap):
        return GateReject(
            "G_BUDGET", f"{budget.get('spent_usd')}/{cap} usd", skill
        )

    return None


def spec_drifted(sdd: dict[str, Any], files: dict[str, str]) -> bool:
    """A verified spec that has since changed must not stay verified."""
    recorded = files.get("meta/spec.sha256")
    current = files.get("spec.md")
    if recorded is None or current is None:
        return False
    return recorded.strip() != spec_hash(current)


# --------------------------------------------------------------------
# G_DECISION_LOGGED - filling a blank is fine; hiding that you filled it
# is not
# --------------------------------------------------------------------

# A rationale has to point at something. "합리적이므로" is not a citation.
_CITATION = re.compile(r"(#L\d+|\bD-\d{3,}\b|\b\w[\w./-]*\.(md|py|ts|tsx|sql):\d+)")
_HANDWAVE = re.compile(
    r"^(합리적|일반적|관례|보통|자연스러|상식적|통상)", re.IGNORECASE
)
_CONFIDENCE = {"상", "중", "하"}


def check_decision(d: dict[str, Any]) -> str | None:
    """One decision. Returns a reason string if it fails, else None."""
    if d.get("tag") != "[AI 결정]":
        return None  # [코드] / [답변] / [CROSS] carry their own authority

    did = d.get("id", "?")
    rationale = (d.get("rationale") or "").strip()

    if not rationale:
        return f"{did}: no rationale"
    if not _CITATION.search(rationale):
        return f"{did}: rationale is not a citation ({rationale[:40]!r})"
    if _HANDWAVE.match(rationale):
        return f"{did}: rationale is hand-waving ({rationale[:40]!r})"
    if not (d.get("alternatives") or "").strip():
        return f"{did}: no alternative recorded"
    if d.get("confidence") not in _CONFIDENCE:
        return f"{did}: confidence must be one of 상/중/하"
    return None


def check_decisions_logged(decisions: list[dict[str, Any]]) -> GateReject | None:
    reasons = [r for r in (check_decision(d) for d in decisions) if r]
    if reasons:
        return GateReject("G_DECISION_LOGGED", "; ".join(reasons[:5]))
    return None


# --------------------------------------------------------------------
# G_HUMAN_GATE - permission and billing, the two that cannot be undone
# --------------------------------------------------------------------

_DEFAULT_TERMS_PATH = Path(__file__).resolve().parents[2] / "config" / "sensitive_terms.json"


def load_sensitive_terms(path: Path | None = None) -> dict[str, list[str]]:
    data = json.loads((path or _DEFAULT_TERMS_PATH).read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def scan_sensitive(
    decisions: list[dict[str, Any]], terms: dict[str, list[str]]
) -> set[str]:
    """The gate's OWN read of which decisions touch permission or money.

    Deliberately independent of `category`. The agent classifying its own
    decision as 일반 must not be able to clear the stop that way.
    """
    hits: set[str] = set()
    for d in decisions:
        haystack = " ".join(
            str(d.get(k, "")) for k in ("text", "rationale", "section", "title")
        ).lower()
        for bucket in ("권한", "과금"):
            if any(t.lower() in haystack for t in terms.get(bucket, ())):
                hits.add(str(d.get("id")))
                break
    return hits


def human_gate_items(
    decisions: list[dict[str, Any]], terms: dict[str, list[str]]
) -> list[dict[str, Any]]:
    """UNION of what the agent flagged and what the gate found itself.

    Union, not intersection. A false positive costs one question; a miss
    costs a leak or a payout.
    """
    ai_flagged = {
        str(d.get("id"))
        for d in decisions
        if d.get("category") in {"권한", "과금"} or d.get("tag") == "[정지 후보]"
    }
    gate_flagged = scan_sensitive(decisions, terms)
    raised = ai_flagged | gate_flagged
    return [d for d in decisions if str(d.get("id")) in raised]


def check_human_gate(
    decisions: list[dict[str, Any]], terms: dict[str, list[str]]
) -> GateReject | None:
    pending = [d for d in human_gate_items(decisions, terms) if not (d.get("answer") or "").strip()]
    if pending:
        ids = ", ".join(str(d.get("id")) for d in pending[:5])
        return GateReject(
            "G_HUMAN_GATE",
            f"{len(pending)} permission/billing decision(s) unanswered: {ids}",
        )
    return None


# --------------------------------------------------------------------
# G_ANSWERS_HUMAN - narrowed in v0.5 to the human-gate answers only
# --------------------------------------------------------------------

_AI_TRACE = re.compile(r"\(추정\)|TODO-AI|AI 판단|AI가 판단", re.IGNORECASE)


def check_answers_human(
    answers: list[dict[str, Any]],
    actor: str,
    pending_ids: Iterable[str],
) -> GateReject | None:
    if actor != "human":
        return GateReject("G_ANSWERS_HUMAN", f"actor={actor!r}, only a human may answer")

    given = {str(a.get("item_id")) for a in answers if (a.get("choice") or "").strip()}
    missing = set(map(str, pending_ids)) - given
    if missing:
        return GateReject(
            "G_ANSWERS_HUMAN",
            f"partial answer rejected; still open: {', '.join(sorted(missing))}",
        )

    for a in answers:
        blob = f"{a.get('choice', '')} {a.get('note', '')}"
        if _AI_TRACE.search(blob):
            return GateReject(
                "G_ANSWERS_HUMAN", f"{a.get('item_id')}: looks AI-authored"
            )
    return None


# --------------------------------------------------------------------
# G_VERIFY - the six items
# --------------------------------------------------------------------

_SUBTASK = re.compile(r"^#{2,4}\s+(?:AC-\d+|\d+\.\d+)\s", re.M)
_BRANCHY = re.compile(r"또는|라면")
_BRANCH_RESOLVED = re.compile(r"조건:|분기:|\|\s*조건\s*\|")
_BLOCKING_OPEN = re.compile(r"(\[BLOCKING\]|차단:\s*true)(?![\s\S]{0,400}?답:\s*\S)")


def _split_sections(md: str) -> list[str]:
    parts = re.split(r"\n(?=#{2,4}\s)", md)
    return [p for p in parts if p.strip()]


def verify_spec(
    sdd: dict[str, Any],
    files: dict[str, str],
    decisions: list[dict[str, Any]] | None = None,
) -> VerifyResult:
    """The six items (docs/gates.md 6.1).

    Items 1, 2 and 5 are heuristics and say so. Items 3, 4 and 6 are the
    ones that must be exact, and they are: an artifact must exist, every
    decision must carry its rationale, and the token must match the hash.
    """
    decisions = decisions if decisions is not None else (sdd.get("decisions") or [])
    reasons: list[str] = []

    spec = files.get("spec.md")
    if not spec:
        return VerifyResult(False, None, ["spec.md is missing"])

    # 1 - every acceptance criterion states how it is decided
    sections = [s for s in _split_sections(spec) if _SUBTASK.match(s)]
    if not sections:
        reasons.append("1: no acceptance criteria found (AC-nnn or n.m sections)")
    for s in sections:
        head = s.splitlines()[0].strip()
        present = sum(k in s for k in ("처리", "성공", "출처", "판별", "산식"))
        if present < 2:
            reasons.append(f"1: {head} has fewer than two of 처리/성공/출처")

    # 2 - branchy wording carries its branch condition
    for s in sections:
        if _BRANCHY.search(s) and not _BRANCH_RESOLVED.search(s):
            reasons.append(f"2: {s.splitlines()[0].strip()} uses 또는/라면 with no 조건:")

    # 3 - cross-section contradictions (Q2): the scan ran, and every
    #     [CROSS] it raised is closed. Not "a human says they looked".
    if "meta/crosscheck.json" not in files:
        reasons.append("3: crosscheck has not run (meta/crosscheck.json missing)")
    else:
        open_cross = [
            d for d in decisions
            if d.get("tag") == "[CROSS]" and not (d.get("text") or "").strip()
        ]
        if open_cross:
            ids = ", ".join(str(d.get("id")) for d in open_cross)
            reasons.append(f"3: unresolved [CROSS] items: {ids}")

    # 4 - no decision without its grounds
    if (rej := check_decisions_logged(decisions)) is not None:
        reasons.append(f"4: {rej.message}")

    # 5 - no blocking question left open
    for name in ("questions.openspec.md", "questions.speckit.md", "decisions.md"):
        body = files.get(name, "")
        if _BLOCKING_OPEN.search(body):
            reasons.append(f"5: {name} has an unanswered [BLOCKING] item")

    # 6 - token and hash. This one is never a heuristic.
    digest = spec_hash(spec)
    recorded = (files.get("meta/spec.sha256") or "").strip()
    if recorded and recorded != digest:
        reasons.append("6: spec.md changed since the hash was recorded")

    token = (files.get("meta/verify-token") or "").strip()
    run_id = sdd.get("run_id", "")
    if token and token != verify_token(run_id, digest):
        reasons.append("6: verify-token does not match this spec")

    return VerifyResult(passed=not reasons, spec_hash=digest, reasons=reasons)
