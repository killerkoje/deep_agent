# Gates — fail-closed

본 문서는 [`SPEC.md`](SPEC.md) §11의 구현 상세다.
**게이트는 코드다.** 모델이 "PASS"라고 말해도 파일이/플래그가 없으면 통과가 아니다.

**상태:** v0.4 · 2026-09-23

---

## 1. 세 가지 원칙

### 1.1 게이트는 순수 함수다

```python
# gates.py — LangGraph를 import하지 않는다 (CI grep으로 검증)
def allow_spawn(sdd: dict, skill: str, model: str, catalog: set[str]) -> GateReject | None:
    ...
```

엔진을 갈아도 게이트는 그대로 남아야 한다. 이식성의 핵심이다.

### 1.2 거부는 예외가 아니라 ToolMessage다 (v0.2 변경)

```python
reject = gates.allow_spawn(...)
if reject:
    return Command(update={
        "messages": [ToolMessage(
            f"GateReject: {reject.code} — {reject.message}",
            tool_call_id=tool_call_id,
        )],
        "sdd": push_reject(sdd, reject),
    })
```

Main이 거부 사유를 **읽고 스스로 재계획**한다.
**fail-closed와 자율성이 동시에 성립한다.**

예외를 던지면 그래프가 죽고 Main이 배우지 못한다.

### 1.3 최근 거부를 Main 컨텍스트에 넣는다

`sdd.gate_rejects`의 최근 N개를 매 턴 주입한다.
같은 벽에 반복해서 부딪히는 것을 막는다.

```python
class GateReject:
    code: str
    message: str
    skill: str | None
```

모든 거부는 감사 로그에 남긴다: `{type: "gate_reject", code, skill, thread_id, at}`

---

## 2. G_MODEL_KNOWN

**When:** 모든 `spawn`
**Check:** `model in models_catalog()`
**Fail:** `GateReject("G_MODEL_KNOWN", model)`

카탈로그는 `config/models.available.json`.
스키마가 `default_for_skill` / `role` / `skills` 필드를 **금지**하므로
이 파일이 배치표로 변질될 수 없다.

---

## 3. G_NO_IMPL_WITHOUT_VERIFY

**When:** `skill in {implement, qa}`

**Check (전부 필요):**

1. `sdd.verify_passed is True`
2. `files["meta/verify-token"]` 존재
3. `files["meta/spec.sha256"] == sha256(files["spec.md"])`

**Fail:** `GateReject("G_NO_IMPL_WITHOUT_VERIFY", skill)`

**스펙이 바뀌면** hash 불일치 → `verify_passed=false`로 내리고 거부.
이것이 스펙 드리프트를 막는 장치다.

---

## 4. G_READY (v0.2 신규)

**When:** `spawn implement`
**Check:** `sdd.ready_open_count == 0`
**Fail:** `GateReject("G_READY", f"ready 1절에 남은 문제 {n}건")`

`ready_open_count`는 `ready.md` **1절(남은 문제)** 의 항목 수다.
`ready-audit` 스킬이 끝나면 스크립트가 세어 State에 기록한다.

> **왜 별도 게이트인가** — verify 6항은 「스펙에 빠진 것」을 본다.
> ready는 「채워졌는데 근거 없는 것」을 본다. **겨누는 방향이 반대**라 둘 다 필요하다.

**MVP 세는 법:** `ready.md`의 `## 1. 남은 문제` 절에서
`| [R` 로 시작하는 표 행 중 상태가 `닫힘`이 아닌 것.

---

## 5. G_ANSWERS_HUMAN

**When:** `POST /threads/{tid}/answers` 또는 `answers.ready` 이벤트

**Check:**

1. 제출 경로가 **human 엔드포인트**일 것 (subagent가 쓰면 거부)
2. `actor` 필드가 skill id면 거부
3. `답:` 뒤에 내용이 있는 줄 **≥ 1**
4. 추정 흔적 패턴 금지 — `답: (추정)`, `답: TODO-AI`, `답: AI 판단`

**Fail:** HTTP 400 + 감사 기록

> **이 게이트는 예외적으로 ToolMessage가 아니라 HTTP 에러다.**
> 사람 API의 입력 검증이지 Main의 재계획 대상이 아니기 때문이다.

---

## 5.5 G_OPENSPEC_VALID (v0.3 신규)

**When:** `openspec` 스킬 spawn 종료 직후
**Check:** `openspec validate --strict --json` 종료코드 0

```python
ok, report = sdd_cli.openspec_validate(workdir, strict=True)
if not ok:
    return GateReject("G_OPENSPEC_VALID", summarize(report["findings"]))
```

**Fail:** GateReject ToolMessage → Main이 같은 스킬을 **다른 모델로 재spawn** 하거나
brief를 고쳐 다시 시도한다.

> **왜 이게 좋은 게이트인가** — CLI 검증은 **결정적**이다.
> LLM이 "잘 만들었다"고 말하는 것과 달리 종료코드와 JSON으로 나온다.
> **프롬프트 판정이 아닌 게이트를 하나 더 얻는 것**이므로 반드시 건다.

**주의:** `spec-kit` 쪽에는 대응하는 validate가 없다.
spec-kit 산출물은 `answer-triage`가 병합할 때 형식 검사만 한다.

---

## 5.6 G_STORE_WRITE (v0.3 신규)

**When:** `remember` 툴 호출

**Check (전부 필요):**

1. 호출자가 **Main**일 것 — 서브에이전트 툴 목록에 `remember`가 없다
2. `sdd.stage == "9-report"` 이후일 것 — **런 도중 저장 금지**
3. `evidence`와 `source_run_id`가 **비어 있지 않을** 것
4. 저장하려는 `text`가 `answers.md` 내용과 **일치하지 않을** 것

**Fail:** `GateReject("G_STORE_WRITE", reason)`

> **4번이 핵심이다.** 사람 답변을 Store에 넣으면
> 다음 run에서 과거 답이 **사람 답인 것처럼** 재사용되고,
> 「답은 사람이 한다」는 SDD 제약이 조용히 무력화된다.
> Store에 남기는 것은 **패턴**이지 **답**이 아니다.

**MVP 검사:** `normalize(text)`가 `answers.md`의 어떤 `답:` 값과
90% 이상 일치하면 거부.

---

## 6. G_VERIFY — 6항

**When:** `ready-audit` 성공 직후, `passed` 이벤트를 올리기 전
**Writer:** `gates.verify_spec(state) -> passed | failed`

### 6.1 항목

| # | 이름 | MVP 검사 방법 |
|---|------|----------------|
| 1 | 인수조건 판별/계산/출처 | `spec.md`에 `AC-` 또는 `### n.m` 세부태스크가 있고, 각 항목에 `처리:`·`성공:`·`출처:` 중 **2개 이상** 존재 (정규식) |
| 2 | 분기 조건 | 본문에 `또는`/`라면`이 있으면 같은 절에 `조건:` 또는 분기 표가 있을 것. 위반 목록 출력 |
| 3 | 절 간 모순 0 | **MVP:** `files["meta/human-crosscheck.ok"]` 존재 여부. 없으면 failed (§15 Q2) |
| 4 | 추정 답 0 | `questions.md`/`answers.md`에 `답: (추정)` / `답: TODO-AI` 패턴 **없음** |
| 5 | 잔여 차단 질문 0 | `questions.md`에 `차단: true` 또는 `[BLOCKING]` 중 미답 **없음** |
| 6 | 토큰 + hash | `sha256(spec.md)` 기록 + `meta/verify-token`에 `VERIFY_OK:{run_id}:{hash}` |

**1~5는 휴리스틱으로 시작해도 된다. 6번과 §5(answers), §4(ready)는 반드시 코드.**

### 6.2 PASS 시

```text
sdd.verify_passed = True
sdd.spec_hash     = hash
files["meta/spec.sha256"]  = hash
files["meta/verify-token"] = f"VERIFY_OK:{run_id}:{hash}"
emit spec.verify.passed
```

### 6.3 FAIL 시

```text
sdd.verify_passed = False
files.pop("meta/verify-token", None)
emit spec.verify.failed  (payload.reasons = [...])
```

Main은 reasons를 보고 **재질문 루프** 또는 **spec-rereview 재spawn**을 고른다.

---

## 6.5 G_TRIAGE_FIRST (v0.4 신규)

**When:** `spawn implement` 이고 `sdd.last_event == "qa.failed"`
**Check:** `state.analysis`가 **이번 실패에 대해** 존재할 것 (`analysis.ran_after == e2e.ran_at`)
**Fail:** `GateReject("G_TRIAGE_FIRST", "qa.failed 후에는 e2e-triage를 먼저 돌려라")`

> **왜 이 게이트가 필요한가** — 실패 원인은 4종인데 대응이 전부 다르다 (SPEC §5.5.2).
> 진단 없이 재구현으로 직행하면 `spec_gap`(스펙 빈칸)이 **추정으로 메워지고**,
> 그 추정이 테스트를 통과하는 순간 **추정이 사실상의 스펙이 된다.**
> `G_ANSWERS_HUMAN` / `G_VERIFY` / `G_READY`가 막으려던 실패가
> **E2E 루프를 통해 우회되는 경로**다. 여기서 끊는다.

---

## 6.6 G_LOOP (v0.4 신규)

**When:** `route == "reimplement"`로 `spawn implement` 직전

**Check — 5종 전부**

| # | 가드 | 거부 사유 |
|---|------|-----------|
| 1 | `iteration < max_iterations` (기본 3) | `"루프 예산 소진 3/3"` |
| 2 | 같은 `test.id` + 같은 `root_cause` **2연속** 아님 | `"같은 진단으로 2회 실패 — 진단이 틀렸다"` |
| 3 | `sdd.spec_hash == implementation.spec_hash_at_impl` | `"루프 중 스펙이 바뀜 — 하위 invalidate"` |
| 4 | `e2e.passed_count >= 직전 회차` | `"회귀: 통과 12 → 9"` |
| 5 | budget 여유 | `"G_BUDGET"` |

**Fail:** `GateReject("G_LOOP", reason)` + `status = "waiting_human"`

```python
def check_loop(state) -> GateReject | None:
    e2e, an, impl = state["e2e"], state["analysis"], state["implementation"]

    if state["iteration"] >= state["max_iterations"]:
        return GateReject("G_LOOP", f"예산 소진 {state['iteration']}/{state['max_iterations']}")

    if _same_diagnosis_twice(state):          # 가드 2
        return GateReject("G_LOOP", "같은 진단으로 2회 실패 — 진단을 다시 하라")

    if state["sdd"]["spec_hash"] != impl.get("spec_hash_at_impl"):
        return GateReject("G_LOOP", "루프 중 스펙 변경 — 루프 폐기")

    prev = _prev_passed_count(state)          # 가드 4
    if prev is not None and e2e["passed_count"] < prev:
        return GateReject("G_LOOP", f"회귀: {prev} → {e2e['passed_count']}")

    return None
```

> **가드 2가 비직관적이지만 중요하다.** 같은 진단으로 두 번 고쳤는데
> 같은 테스트가 또 실패하면, **고치는 법이 틀린 게 아니라 진단이 틀린** 것이다.
> 세 번째 시도는 낭비다. `e2e-triage`를 다른 모델로 다시 돌리거나 사람에게 올린다.

**`iteration`은 `impl_bug` 재시도만 센다.** `respec`으로 나갈 때는 올리지도, **리셋하지도** 않는다.

---

## 6.7 G_TEST_INTEGRITY (v0.4 신규)

에이전트가 **테스트를 약화시켜 통과**시키는 경로를 막는다. 루프를 돌리면 반드시 시도된다.

**When:** `implement` / `qa` 산출물 검사

**Check**

| 대상 | 규칙 |
|------|------|
| `implement` | **테스트 파일 경로를 건드리면 즉시 거부** (`**/*.spec.ts`, `**/*.test.*`, `tests/**`, `e2e/**`) |
| `qa` (평상시) | 테스트 수정 불가 |
| `qa` (`route == "fix_test"`) | 수정 가능. 단 아래 3조건 |

`fix_test` 3조건:

1. **`ac_ref` 매핑이 유지**될 것 — 조항에 연결되지 않은 테스트는 증거가 아니다
2. 테스트 **개수가 줄지 않을** 것
3. `expect` / assertion 개수가 **줄지 않을** 것 (단정을 지워 통과시키는 것 방지)

**Fail:** `GateReject("G_TEST_INTEGRITY", detail)`

> 통과율만 보면 이 조작이 **성공처럼 보인다.** 그래서 결과가 아니라
> **산출물의 형태**를 본다.

---

## 7. G_SESSION_QA

**When:** `spawn qa`

**Check:**

1. 새 `spawn_id` (implement의 것과 다름)
2. 서브그래프 초기 messages에 implement의 transcript가 **없음**
3. `brief`에 implement의 raw 로그 경로가 **포함되지 않음**

**Fail:** `GateReject("G_SESSION_QA")`

> 같은 `model` id를 써도 무방하다. **세션이 분리되면 된다.**
> QA가 구현자의 사고를 이어받으면 검증이 구현에 오염된다.

### 7.1 격리 단위 테스트 (필수)

```text
given  implement spawn 완료 (messages N개 생성)
when   부모 state를 확인
then   부모 messages 증가분 == ToolMessage 1개
```

**이 테스트가 깨지면 Deep Agent가 아니다.** S2에서 가장 먼저 작성한다.

---

## 8. G_BUDGET (선택)

**When:** spawn 전
**Check:** `sdd.budget.max_usd`가 없으면 skip. 있으면 `spent + estimate <= max`
**Fail:** `GateReject("G_BUDGET")`

멀티에이전트는 토큰을 단일 대비 **한 자릿수 배**로 쓴다.
계측은 Phase 1부터 켜고, 캡은 필요해지면 건다.

---

## 9. 테스트 목록 (필수)

| # | 테스트 | 기대 |
|---|--------|------|
| 1 | verify=false + spawn implement | GateReject ToolMessage |
| 2 | spec.md 수정 후 hash 불일치 | reject + `verify_passed=false` |
| 3 | `ready_open_count=2` + spawn implement | `G_READY` reject |
| 4 | subagent가 answers 제출 | 400 |
| 5 | 카탈로그에 없는 모델 | `G_MODEL_KNOWN` reject |
| 6 | verify-token 없음 | `spec.verify.failed` |
| 7 | qa spawn이 implement transcript 주입 | `G_SESSION_QA` reject |
| 8 | **spawn 후 부모 messages 증가분 == 1** | **green (격리)** |
| 9 | GateReject 후 Main이 다른 스킬 선택 | 재계획 확인 |
| 10 | `gates.py`에 `langgraph` import | CI grep 실패 |
| 11 | `openspec validate --strict` 실패 산출물 | `G_OPENSPEC_VALID` reject |
| 12 | **서브에이전트가 `remember` 호출** | 툴 자체가 없음 (AttributeError 아닌 미등록) |
| 13 | **`answers.md` 문장을 `remember`로 저장 시도** | `G_STORE_WRITE` reject |
| 14 | `evidence` 없는 `remember` | reject |
| 15 | 셸 allowlist 밖 명령 (`rm -rf` 등) | 거부 · 실행 안 됨 |
| 16 | `qa.failed` 직후 `implement` spawn | `G_TRIAGE_FIRST` reject |
| 17 | **`root_cause=spec_gap`** 진단 | `verify_passed=false` · 단계 3으로 복귀 · `iteration` 안 오름 |
| 18 | `iteration=3` + reimplement | `G_LOOP` reject · `waiting_human` |
| 19 | **같은 test·같은 root_cause 2연속** | `G_LOOP` reject (가드 2) |
| 20 | **통과 12 → 9로 감소** | `G_LOOP` reject (회귀) |
| 21 | 루프 중 `spec.md` 변경 | `G_LOOP` reject (hash 불일치) |
| 22 | **`implement`가 `*.spec.ts` 수정** | `G_TEST_INTEGRITY` reject |
| 23 | `fix_test`가 assertion을 삭제 | `G_TEST_INTEGRITY` reject |
| 24 | `e2e-triage`가 코드 수정 시도 | 툴 미제공 · 산출물 검사 실패 |

---

## 10. 구현 스케치

```python
# gates.py — 순수. LangGraph import 금지.

class GateReject(Exception):
    def __init__(self, code: str, message: str = "", skill: str | None = None):
        self.code, self.message, self.skill = code, message, skill


def allow_spawn(sdd: dict, skill: str, model: str, catalog: set[str]) -> GateReject | None:
    if model not in catalog:
        return GateReject("G_MODEL_KNOWN", model, skill)

    if skill in {"implement", "qa"}:
        if not sdd.get("verify_passed"):
            return GateReject("G_NO_IMPL_WITHOUT_VERIFY",
                              "verify_passed=false", skill)

    if skill == "implement":
        n = sdd.get("ready_open_count")
        if n is None or n > 0:
            return GateReject("G_READY", f"ready 남은 문제 {n}건", skill)

    b = sdd.get("budget") or {}
    if b.get("max_usd") is not None and b.get("spent_usd", 0) >= b["max_usd"]:
        return GateReject("G_BUDGET", f"{b['spent_usd']}/{b['max_usd']} usd", skill)

    return None
```

세션 분리(`G_SESSION_QA`)는 `allow_spawn`이 아니라
**`SpawnExecutor`가 메시지를 조립하는 지점**에서 강제한다.
모델 id로 판정할 수 없기 때문이다.

---

## 변경 이력

| 버전 | 날짜 | 내용 |
|------|------|------|
| 0.1 | 2026-09-22 | 초안 |
| 0.2 | 2026-09-23 | 거부를 **ToolMessage**로 변경, `G_READY` 신설, 격리 테스트 필수화, `gates.py` 순수성 조항 |
| 0.3 | 2026-09-23 | **`G_OPENSPEC_VALID`** (CLI 결정적 검증), **`G_STORE_WRITE`** (장기기억 오염·answers 승격 방지), 셸 allowlist 테스트 |
| 0.4 | 2026-09-23 | **E2E 실패 루프 게이트 3종** — `G_TRIAGE_FIRST`(진단 없이 재구현 금지), `G_LOOP`(가드 5종·회귀·같은진단 2연속), `G_TEST_INTEGRITY`(테스트 약화로 통과 조작 방지) |
