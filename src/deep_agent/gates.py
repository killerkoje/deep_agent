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
# G_OPENSPEC_VALID - a deterministic verdict, which is why it earns a gate
# --------------------------------------------------------------------


def check_openspec_valid(
    exit_code: int, report: dict[str, Any] | None, skipped: bool = False
) -> GateReject | None:
    """Judge an `openspec validate --strict --json` run.

    The caller runs the CLI (that is I/O); this only reads the verdict,
    which keeps the rule testable without a subprocess.

    `skipped=True` when the binary is absent - a missing tool must not
    silently look like a pass, but it also must not block a local dev
    loop, so it surfaces as its own message.
    """
    if skipped:
        return GateReject("G_OPENSPEC_VALID", "openspec not installed; validation skipped")
    if exit_code == 0:
        return None

    findings = (report or {}).get("findings") or (report or {}).get("issues") or []
    if findings:
        head = "; ".join(
            str(f.get("message") or f.get("detail") or f)[:80] for f in findings[:3]
        )
        return GateReject("G_OPENSPEC_VALID", f"{len(findings)} finding(s): {head}")
    return GateReject("G_OPENSPEC_VALID", f"validate exited {exit_code}")


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


# decisions.md is what a person reads; this is what the gates read.
# Parsing here rather than trusting the sub-agent to also emit JSON: one
# artifact, one source of truth, and a malformed entry surfaces as a
# missing field the gate already rejects.
_D_HEAD = re.compile(r"^##\s+(D-\d{3,})\s*(.*)$", re.M)
_D_FIELD = re.compile(r"^\s*-\s*\*\*(.+?):\*\*\s*(.*)$", re.M)
_D_TAG = re.compile(r"\[(코드|AI 결정|답변|정지 후보|CROSS)\]")

_FIELD_KEYS = {
    "결정": "text",
    "근거": "rationale",
    "대안": "alternatives",
    "확신도": "confidence",
    "분류": "category",
    "답": "answer",
    "왜": "rationale",
    "선택지": "alternatives",
}


_JSON_BLOCK = re.compile(r"```(?:json)?\s*\r?\n(\[.*?\]|\{.*?\})\r?\n```", re.S)


def parse_decisions(md: str | None) -> list[dict[str, Any]] | None:
    """decisions.md -> the list the gates operate on, or None if the
    document does not carry one in a form we can read.

    None is the important return value. On the first live run the model
    did its job - it marked fifteen items as [정지 후보] - but wrote
    them as prose bullets instead of the heading shape the parser
    expected. The parser returned [], the human gate saw nothing to
    stop on, and a run full of unresolved permission questions sailed
    through. Returning None makes that state refuse instead of pass.
    """
    if not md or not md.strip():
        return None

    # 1. A declared JSON block is the contract. Skills are told to emit
    #    one precisely so a gate never has to read prose.
    for block in _JSON_BLOCK.findall(md):
        try:
            data = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            continue
        items = data.get("decisions") if isinstance(data, dict) else data
        if isinstance(items, list) and items and all(isinstance(d, dict) for d in items):
            return [_normalize_decision(d) for d in items]

    # 2. Fall back to the markdown shape.
    parsed = parse_decisions_md(md)
    return parsed or None


def _normalize_decision(d: dict[str, Any]) -> dict[str, Any]:
    out = dict(d)
    out.setdefault("category", "일반")
    if out.get("category") not in {"일반", "권한", "과금"}:
        out["category"] = "일반"
    if out.get("tag") == "[정지 후보]" and out["category"] == "일반":
        out["category"] = "권한"
    return out


def parse_decisions_md(md: str) -> list[dict[str, Any]]:
    """The markdown shape: `## D-001` headings with `- **key:**` fields."""
    if not md:
        return []

    out: list[dict[str, Any]] = []
    heads = list(_D_HEAD.finditer(md))
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(md)
        body = md[head.end() : end]

        entry: dict[str, Any] = {
            "id": head.group(1),
            "title": head.group(2).strip(),
            "tag": None,
            "category": "일반",
        }
        for key, value in _D_FIELD.findall(body):
            mapped = _FIELD_KEYS.get(key.strip())
            if mapped and not entry.get(mapped):
                entry[mapped] = value.strip()

        if tag := _D_TAG.search(body):
            entry["tag"] = f"[{tag.group(1)}]"
        # the tag marks authority; it is not part of the decision text
        for key in ("text", "rationale", "alternatives"):
            if entry.get(key):
                entry[key] = _D_TAG.sub("", entry[key]).strip()
        if entry.get("category") not in {"일반", "권한", "과금"}:
            entry["category"] = "일반"
        if entry["tag"] == "[정지 후보]" and entry.get("category") == "일반":
            # Flagged as a stop candidate but left unclassified - treat
            # it as raised rather than letting it fall through.
            entry["category"] = "권한"

        out.append(entry)
    return out


# --------------------------------------------------------------------
# G_READY input - counting what is still open in ready.md
# --------------------------------------------------------------------

# A gate reads a declared machine-readable value, never prose.
#
# The first live run is why. count_ready_open used to fall back to
# searching the section body for "없음", and matched it inside the
# sentence "매출 0/없음/음수 지점" - reporting a clean audit for a
# document whose own header said 24 items were open. A heuristic that
# can silently produce a PASSING value is worse than no gate at all.
#
# So: skills declare the number in frontmatter, and anything we cannot
# read stays None. None means "refuse", not "fine".

_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\s*?\r?\n", re.S)
_YAML_INT = re.compile(r"^\s*([A-Za-z_][\w-]*)\s*:\s*(-?\d+)\s*$", re.M)


def parse_frontmatter_ints(md: str | None) -> dict[str, int]:
    """Integer keys from a leading `---` block. Deliberately tiny - we
    only ever read declared counts out of it, not arbitrary YAML."""
    if not md:
        return {}
    block = _FRONTMATTER.match(md)
    if not block:
        return {}
    return {k: int(v) for k, v in _YAML_INT.findall(block.group(1))}


# Fallback only: a table whose rows carry an explicit open/closed status.
_READY_SECTION_1 = re.compile(r"^##\s*1[.\s][^\n]*\n(.*?)(?=^##\s|\Z)", re.M | re.S)
# `| R1 |`, `| [R1](#r1) |`, `| G01 |` - an id cell, however it is linked.
_READY_ROW = re.compile(
    r"^\|\s*\[?[A-Z]{0,2}\d+[A-Za-z]?\]?(?:\(#[^)]*\))?\s*\|(.*)$", re.M
)
_CLOSED = re.compile(r"닫힘|closed|해결|완료|resolved")


def count_ready_open(ready_md: str | None) -> int | None:
    """Open items in ready.md, or None if the document does not say.

    None and 0 are different. "the audit never ran" and "the audit
    found nothing" must not collapse into the same value, because one
    of them is a pass.
    """
    if not ready_md:
        return None

    # 1. The declared value wins. This is what skills are told to emit.
    declared = parse_frontmatter_ints(ready_md).get("ready_open_count")
    if declared is not None:
        return max(0, declared)

    # 2. Otherwise count table rows that are not marked closed.
    section = _READY_SECTION_1.search(ready_md)
    if not section:
        return None
    rows = _READY_ROW.findall(section.group(1))
    if not rows:
        return None  # unreadable -> refuse, never 0
    return sum(1 for rest in rows if not _CLOSED.search(rest))


# --------------------------------------------------------------------
# G_HUMAN_GATE - permission and billing, the two that cannot be undone
# --------------------------------------------------------------------

_DEFAULT_TERMS_PATH = Path(__file__).resolve().parents[2] / "config" / "sensitive_terms.json"


def load_sensitive_terms(path: Path | None = None) -> dict[str, list[str]]:
    data = json.loads((path or _DEFAULT_TERMS_PATH).read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _haystack(d: dict[str, Any]) -> str:
    return " ".join(
        str(d.get(k, "")) for k in ("text", "rationale", "section", "title")
    ).lower()


def scan_sensitive(
    decisions: list[dict[str, Any]], terms: dict[str, Any]
) -> set[str]:
    """The gate's OWN read of which decisions touch permission or money.

    Independent of `category` on purpose: an agent classifying its own
    decision as 일반 must not be able to clear the stop that way.

    Two tiers, because precision is what keeps this gate worth reading.
    A `strong` term names an irreversible act and raises on its own. A
    `weak` term is a domain noun - on a revenue dashboard every single
    decision says 매출 - and only raises when a rule-setting word sits
    with it. A gate that fires on almost everything gets rubber-stamped.
    """
    strong: dict[str, list[str]] = terms.get("strong") or {}
    weak: dict[str, list[str]] = terms.get("weak") or {}
    rule_words = [w.lower() for w in (terms.get("rule_words") or [])]

    # Older flat shape: {"권한": [...], "과금": [...]} - treat as strong.
    if not strong and any(k in terms for k in ("권한", "과금")):
        strong = {k: terms.get(k, []) for k in ("권한", "과금")}

    hits: set[str] = set()
    for d in decisions:
        hay = _haystack(d)
        raised = any(
            t.lower() in hay for bucket in strong.values() for t in bucket
        )
        if not raised and rule_words:
            raised = _weak_rule_pair(hay, weak, rule_words)
        if not raised:
            # A role noun beside a scope word: "지점 관리자도 전체 ... 조회".
            # Neither half is a signal alone, together they are the
            # shape this gate exists for.
            raised = _pair_near(
                hay, terms.get("role_words") or [], terms.get("scope_words") or []
            )
        if raised:
            hits.add(str(d.get("id")))
    return hits


# A weak term and a rule word must sit together - "매출 정의",
# "금액 기준" - not merely appear in the same paragraph. Co-occurrence
# alone raised 15 of 19 decisions on a revenue dashboard, because 기준
# and 정의 show up in every spec sentence ever written.
_NEAR = 14


def _pair_near(hay: str, left: list[str], right: list[str], window: int = 24) -> bool:
    """Two term lists appearing within `window` characters of each other."""
    for a in left:
        a = a.lower()
        i = hay.find(a)
        while i != -1:
            lo, hi = max(0, i - window), i + len(a) + window
            if any(b.lower() in hay[lo:hi] for b in right):
                return True
            i = hay.find(a, i + 1)
    return False


def _weak_rule_pair(hay: str, weak: dict[str, list[str]], rules: list[str]) -> bool:
    for bucket in weak.values():
        for term in bucket:
            t = term.lower()
            start = hay.find(t)
            while start != -1:
                window = hay[start : start + len(t) + _NEAR]
                if any(w in window for w in rules):
                    return True
                start = hay.find(t, start + 1)
    return False


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
    decisions: list[dict[str, Any]] | None, terms: dict[str, list[str]]
) -> GateReject | None:
    """`decisions=None` means the decision record could not be read.

    That is a refusal, not a pass. An unreadable record is exactly the
    state in which a permission question hides.
    """
    if decisions is None:
        return GateReject(
            "G_HUMAN_GATE",
            "decisions could not be read - cannot confirm no permission/billing "
            "decision is outstanding",
        )

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
# The E2E failure loop: G_TRIAGE_FIRST, G_LOOP, G_TEST_INTEGRITY
# --------------------------------------------------------------------

ROUTE_OF_CAUSE = {
    "impl_bug": "reimplement",
    "spec_gap": "respec",
    "test_defect": "fix_test",
    "environment": "retry",
}

# Only impl_bug burns loop budget. spec_gap leaves the loop entirely -
# it goes back to the spec, because letting it retry means the agent
# fills the gap by guessing and the guess passes the test (SPEC 5.5.2).
COUNTS_TOWARD_ITERATION = {"impl_bug"}


def check_triage_first(sdd: dict[str, Any], analysis: dict | None, e2e: dict) -> GateReject | None:
    """After qa.failed, diagnose before rebuilding.

    Four causes with four destinations; one reflex response gets three
    of them wrong. The dangerous one is spec_gap read as impl_bug.
    """
    if sdd.get("last_event") != "qa.failed":
        return None
    if not analysis:
        return GateReject(
            "G_TRIAGE_FIRST", "run e2e-triage before re-implementing", "implement"
        )
    if analysis.get("ran_after") != e2e.get("ran_at"):
        return GateReject(
            "G_TRIAGE_FIRST", "the diagnosis predates this failure", "implement"
        )
    return None


def _same_diagnosis_twice(history: list[dict]) -> str | None:
    """Two consecutive rounds blaming the same test for the same reason.

    That is not a fix that failed - it is a diagnosis that is wrong, and
    a third attempt at the same repair is waste.
    """
    if len(history) < 2:
        return None
    a, b = history[-1], history[-2]
    if a.get("root_cause") != b.get("root_cause"):
        return None
    shared = set(a.get("failed_ids") or []) & set(b.get("failed_ids") or [])
    return f"{sorted(shared)[0]} failed twice as {a.get('root_cause')}" if shared else None


def check_loop(
    iteration: int,
    max_iterations: int,
    sdd: dict[str, Any],
    implementation: dict,
    e2e: dict,
    triage_history: list[dict] | None = None,
) -> GateReject | None:
    """The five guards, checked before a reimplement (SPEC 5.5.4)."""
    if iteration >= max_iterations:
        return GateReject(
            "G_LOOP", f"loop budget spent {iteration}/{max_iterations}", "implement"
        )

    if repeated := _same_diagnosis_twice(triage_history or []):
        return GateReject(
            "G_LOOP", f"same diagnosis twice - re-diagnose ({repeated})", "implement"
        )

    at_impl = implementation.get("spec_hash_at_impl")
    if at_impl and sdd.get("spec_hash") and at_impl != sdd["spec_hash"]:
        return GateReject(
            "G_LOOP", "spec changed mid-loop - discard the loop", "implement"
        )

    prev = e2e.get("prev_passed_count")
    now = e2e.get("passed_count")
    if prev is not None and now is not None and now < prev:
        return GateReject("G_LOOP", f"regression: {prev} passing -> {now}", "implement")

    budget = sdd.get("budget") or {}
    cap = budget.get("max_usd")
    if cap is not None and float(budget.get("spent_usd") or 0.0) >= float(cap):
        return GateReject("G_LOOP", f"budget {budget.get('spent_usd')}/{cap}", "implement")

    return None


_TEST_PATHS = re.compile(
    r"(^|/)(tests?|e2e|__tests__)/|\.(spec|test)\.[jt]sx?$|(^|/)test_[^/]+\.py$"
)


def is_test_path(path: str) -> bool:
    return bool(_TEST_PATHS.search(path.replace("\\", "/")))


def check_test_integrity(
    skill: str,
    touched: list[str],
    route: str | None = None,
    before: dict[str, int] | None = None,
    after: dict[str, int] | None = None,
) -> GateReject | None:
    """Stop the loop from passing by weakening the tests.

    It will find that path if it can - and pass rate alone makes the
    result look like success, so this checks the SHAPE of what came out
    rather than the score.
    """
    offending = [p for p in touched if is_test_path(p)]

    if skill == "implement" and offending:
        return GateReject(
            "G_TEST_INTEGRITY",
            f"implement may not modify tests: {', '.join(offending[:3])}",
            skill,
        )

    if skill == "qa" and offending and route != "fix_test":
        return GateReject(
            "G_TEST_INTEGRITY", "qa may only edit tests when routed to fix_test", skill
        )

    if skill == "qa" and route == "fix_test" and before and after:
        for metric, label in (
            ("tests", "test count"),
            ("assertions", "assertion count"),
            ("mapped_acs", "AC mappings"),
        ):
            if metric in before and after.get(metric, 0) < before[metric]:
                return GateReject(
                    "G_TEST_INTEGRITY",
                    f"{label} dropped {before[metric]} -> {after.get(metric)}",
                    skill,
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
