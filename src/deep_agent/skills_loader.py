"""Skill contracts.

A skill is a work contract - what goes in, what must come out, what the
sub-agent is allowed to touch. It is not a model. Which model runs a
skill is Main's choice at spawn time, every time (README 0.2).

S2 ships the registry with inline prompts so the loop can be exercised
end to end. S4 replaces the prompt bodies with skills/<name>/SKILL.md;
the contract shape here does not change.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Pipeline stages 0-9, docs/SPEC.md 10.
SKILLS = (
    "spec-write",
    "openspec",
    "spec-kit",
    "decide",
    "spec-rereview",
    "crosscheck",
    "ready-audit",
    "implement",
    "qa",
    "e2e-triage",
)


@dataclass(frozen=True)
class Skill:
    name: str
    prompt: str
    inputs: tuple[str, ...] = ()  # file globs the sub-agent may read
    outputs: tuple[str, ...] = ()  # files it must produce
    event: str | None = None
    needs_workspace: bool = False  # real disk, not state files
    tool_names: tuple[str, ...] = field(default=())


_COMMON = """\
You are a sub-agent running one skill in an SDD pipeline.

You see only this brief and the files listed below. You cannot see the
orchestrator's conversation or any other sub-agent's work - that is
deliberate, so your judgment is independent.

When you are done, your FINAL message is the summary the orchestrator
reads. Keep it short and factual: what you produced, and anything that
blocked you. Do not call the next step yourself; report and stop.

## Output paths are exact

Write your declared output files at EXACTLY the paths given, with
write_file. Not `specs/<name>.md`, not `<skill>-output.md`, not a name
you think reads better - the orchestrator looks for the exact path and
rejects the run otherwise, which costs a full retry.

If an input file you expected is absent, say so in your summary and
work with what you have. Do not stop to ask; you get no reply.
"""

_REGISTRY: dict[str, Skill] = {
    "spec-write": Skill(
        name="spec-write",
        prompt=_COMMON
        + """
Fill the SPEC boilerplate from the source documents.

Tag every rule line with its authority:
  [코드]      verified in the current code (cite file:line)
  [AI 결정]   you decided it - REQUIRES rationale (a citation),
              the alternative you rejected, and confidence 상|중|하
  no tag      grounded in a source document (link the line)

Write each sub-task as 처리 / 성공 / 실패 / 예외 / 출처.
실패 is what must NOT happen, not the opposite of 성공.

Do not answer questions here - leave gaps as TBD for the hole-finders.
""",
        inputs=("sources/*",),
        outputs=("spec.md",),
        event="spec.draft.ready",
        tool_names=("ls", "read_file", "write_file"),
    ),
    "openspec": Skill(
        name="openspec",
        prompt=_COMMON
        + """
Find holes in the spec. Produce questions only - never answers.

Find: acceptance criteria with no rule for deciding true/false;
`~라면` conditions the implementer cannot evaluate; one section
distinguishing what another lumps together; "표시한다" with no
statement of how the value is produced; a screen with no data source;
`또는` with no branch condition.

Do not ask: who signs off, when to ship, anything already decided,
performance numbers with no basis, component structure.

Every question carries one line: "if this is unanswered, what will the
AI build wrong?" Offer options and a recommendation.
""",
        inputs=("spec.md",),
        outputs=("questions.openspec.md",),
        event="questions.ready",
        tool_names=("ls", "read_file", "write_file"),
    ),
    "spec-kit": Skill(
        name="spec-kit",
        prompt=_COMMON
        + """
Same hole-finding brief as the other tool, but aim at the permission and
role axis. Produce options plus a recommendation so each answer closes
in one line.

You cannot see the other hole-finder's output. Overlap is expected and
useful - questions both tools raise independently are the strong signal.
""",
        inputs=("spec.md",),
        outputs=("questions.speckit.md",),
        event="questions.ready",
        tool_names=("ls", "read_file", "write_file"),
    ),
    "decide": Skill(
        name="decide",
        prompt=_COMMON
        + """
Merge the two question sets and ANSWER them. Do not stop for a human.

For each question:
  - derivable from code      -> verify and cite file:line       [코드]
  - derivable from decisions -> show the derivation             (no tag)
  - technical judgment       -> decide it                       [AI 결정]
  - permission/security or money/billing -> DO NOT DECIDE       [정지 후보]
    state the options and a recommendation, set category

Every [AI 결정] needs rationale (a citation, not "it seems reasonable"),
the rejected alternative, and confidence 상|중|하. Missing any of these
is rejected by the gate.

Low confidence is fine - write 확신도: 하 and give the alternative.
Refusing to decide is the worse failure, except for the stop categories.

## decisions.md must carry a machine-readable block

Prose is for people; the gates read this. Put it near the top:

```json
[
  {"id": "D-001", "tag": "[AI 결정]", "text": "the decision in one line",
   "rationale": "a citation - file#Lnn or D-nnn, never 'it seems reasonable'",
   "alternatives": "what you rejected and why",
   "confidence": "상|중|하", "category": "일반"},
  {"id": "D-002", "tag": "[정지 후보]", "text": "...", "category": "권한"}
]
```

Every item you answered and every item you left for a person goes in
this list. One that is missing from it is invisible to the gate - and a
permission question the gate cannot see is one that ships unanswered.
Write the human-readable version below it as well.
""",
        inputs=("spec.md", "questions.openspec.md", "questions.speckit.md"),
        outputs=("decisions.md",),
        event="decisions.ready",
        tool_names=("ls", "read_file", "write_file"),
    ),
    "spec-rereview": Skill(
        name="spec-rereview",
        prompt=_COMMON
        + """
Fold the decisions into the spec. Tag each line with its authority and
link it to its D-nnn entry. Do not re-open settled decisions, and do not
strengthen a decision beyond what its entry says.
""",
        inputs=("spec.md", "decisions.md"),
        outputs=("spec.md",),
        event="spec.updated",
        tool_names=("ls", "read_file", "write_file"),
    ),
    "crosscheck": Skill(
        name="crosscheck",
        prompt=_COMMON
        + """
Find contradictions BETWEEN sections of the updated spec.

Before raising anything, ask: does an implementation satisfying both
sections exist? If yes, it is not a contradiction - do not raise it.
A total at the top plus a breakdown below is ordinary dashboard design.

Raise only these shapes:
  - the same term defined differently in two sections
  - a rule in section A defeated by a feature in section B
  - two aggregation cut-offs that double-count or drop records

For each, show in one line why no implementation satisfies both.
""",
        inputs=("spec.md", "decisions.md"),
        outputs=("decisions.md", "meta/crosscheck.json"),
        event="crosscheck.ready",
        tool_names=("ls", "read_file", "write_file"),
    ),
    "ready-audit": Skill(
        name="ready-audit",
        prompt=_COMMON
        + """
Audit every [AI 결정] in the spec. Sort each into: derived (show the
derivation), assumption that drifted in (the target), technical
decision, implementation tuning.

Rank your sources first - detail is not authority and neither is
recency. A decision-maker's earlier mail beats a later proposal
document.

Also classify each decision as 일반 / 권한 / 과금. Be liberal with
권한 and 과금: a false positive costs one question, a miss costs a leak
or a payout.

Section 1 of your output is the only part open for discussion.

## ready.md must declare the count in frontmatter

Start the file with exactly this, before anything else:

```
---
ready_open_count: <number of items still open in section 1>
---
```

The gate reads that number and nothing else. It does not count your
table and it does not read your prose - a previous run wrote "24 open"
in the heading and the word 없음 in an unrelated sentence, and a
prose-reading gate called the audit clean.
""",
        inputs=("spec.md", "decisions.md", "sources/*"),
        outputs=("ready.md",),
        event="ready.audit.ready",
        tool_names=("ls", "read_file", "write_file"),
    ),
    "implement": Skill(
        name="implement",
        prompt=_COMMON
        + """
Implement the verified spec, in this order:
  1. backend contract (response types first)
  2. storage
  3. screens

Skipping 1 means inventing field names that later disagree with the
backend. Follow the existing stack and conventions; do not swap
frameworks. Nothing outside the spec.

You may NOT modify test files. If a new gap appears, do not fill it by
guessing - say so in your summary and stop.
""",
        inputs=("spec.md",),
        outputs=(),
        event="impl.ready",
        needs_workspace=True,
    ),
    "qa": Skill(
        name="qa",
        prompt=_COMMON
        + """
Map every acceptance criterion to evidence and run the E2E suite.
Attach traces and screenshots BY PATH, never by content.

Every test result carries its ac_ref. A test not tied to a clause is
not evidence.
""",
        inputs=("spec.md",),
        outputs=("qa-report.md",),
        event="qa.passed",
        needs_workspace=True,
        tool_names=("ls", "read_file", "write_file"),
    ),
    "e2e-triage": Skill(
        name="e2e-triage",
        prompt=_COMMON
        + """
Diagnose the failure. Do not fix anything - read and classify.

Your verdict decides what happens next, so do not pick the convenient one:

  impl_bug    the spec specifies this behavior and the code differs.
              Quote the spec line. No quote means it is not impl_bug.
  spec_gap    the implementer had to decide something the spec left open.
              Show that no rule for it exists.
  test_defect spec and code are right, the test is wrong. Name which:
              selector, waiting, or data setup.
  environment the app did not come up, or a dependency is missing.

If you cannot tell, set confidence low and route to escalate. Do NOT
default to impl_bug - that is the common misdiagnosis, and calling a
spec_gap an impl_bug lets the next attempt fill the gap by guessing.
""",
        inputs=("spec.md",),
        outputs=(),
        event="triage.diagnosed",
        needs_workspace=True,
    ),
}


class UnknownSkill(KeyError):
    pass


def load(name: str) -> Skill:
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise UnknownSkill(
            f"unknown skill {name!r}; expected one of {', '.join(SKILLS)}"
        ) from exc


def select_inputs(files: dict[str, str], patterns: tuple[str, ...]) -> dict[str, str]:
    """Hand the sub-agent only the files its contract declares.

    A sub-agent seeing the whole workspace defeats the point - qa has no
    business reading the implementer's notes.
    """
    import fnmatch

    picked: dict[str, str] = {}
    for pattern in patterns:
        for path, content in files.items():
            if fnmatch.fnmatch(path, pattern):
                picked[path] = content
    return picked
