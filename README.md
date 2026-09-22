# Workflow Orchestration Agent

> AI 에이전트 멀티플렉서 — PRD(사람) 이후 SPEC부터 구현·QA까지 오케스트레이션한다.  
> 목표는 **순수 Deep Agents**: **최상위 Main이 매 턴 무엇을 할지, 어떤 모델을 호출할지 스스로 정한다.**  
> 역할→모델 고정표는 없다. SDD 게이트만 하드 제약으로 남긴다.

**Status:** Draft / Research  
**Repo:** [heh139811-droid/work_flow_ochestration_agent](https://github.com/heh139811-droid/work_flow_ochestration_agent)  
**Local clone:** `C:\Users\PC\Documents\work_flow_ochestration_agent`

---

## 0. Deep Agents — Main이 모델까지 고른다

### 0.1 정의 (이 레포의 진실)

학계 고유 용어라기보다 [LangChain Deep Agents](https://www.langchain.com/blog/deep-agents)가 정리한 패턴이다.  
영감: **Claude Code / Deep Research / Manus**. shallow 툴루프와 대비해 Deep의 조건은 다음이다.

1. **계획(Todo)** — Main이 목표를 쪼개 추적  
2. **서브에이전트 위임** — Main이 일을 직접 쓰지 않고 **모델 세션을 spawn**  
3. **파일시스템 작업공간** — `runs/{run_id}/`에 중간 산출물 공유  
4. **자세한 시스템 프롬프트** — 스킬·게이트·금지 명시  

**Deep가 아닌 것:** 사람이 “Docs=Opus, Impl=Sol” 표를 정해 두고 모델만 교체하는 것.  
**Deep인 것:** Main이 상태를 읽고 **어떤 작업(스킬)을, 어떤 모델로, 지금** 돌릴지 **스스로 호출**하는 것.

### 0.2 어떻게 “알아서” 가능한가

Main에게 **`spawn` / `call_model` 툴**을 주고, 매 턴 `tick` 루프를 돌린다.  
**모델 배치표·역할→모델 레지스트리는 두지 않는다.**

```
① Main tick
   state / Todo / 이벤트 / 실패 요약 읽음
   → 다음 행동: plan | spawn | wait_human | finish

② Main이 툴 호출 (판단 = LLM)
   spawn(
     skill="spec-write",          # 무엇을
     model="<Main이 고른 모델>",  # 누구로
     brief="…",
     tools=[…],
     budget=…
   )

③ 런타임 (코드)
   지정 모델로 새 세션 실행
   스킬 프롬프트·툴만 주입 (모델은 Main이 넘긴 값)
   → 산출물은 FS, Main에는 요약만 반환

④ 게이트 (코드, fail-closed)
   예: spec.verify.passed 없으면 implement spawn 거부
   (모델 이름은 게이트 대상이 아님 — 스킬·전이·사람답·세션 분리만)
```

| 누가 | 뭘 고름 |
|------|---------|
| **Main (최상위 LLM)** | **스킬·모델·타이밍·brief·재계획** — 전부 |
| **게이트 (코드)** | 금지 전이, 사람 답 필수, QA≠Writer 세션, PASS 스크립트, (선택) budget 상한 |
| **사람** | PRD · 질문 답 · (운영) 사용 가능한 모델 API 키/목록 제공만 |

스킬(Docs형 / Impl형 / QA형)은 **작업 계약**이다.  
어떤 모델이 그 스킬을 돌릴지는 **매번 Main이 고른다.**

### 0.3 모델 정책 — 표 없음

- **역할→모델 표: 삭제.** 실험 정책으로도 두지 않는다.  
- Main은 가용 모델 목록(런타임이 노출) 안에서 **비용·난이도·실패 이력**을 보고 호출한다.  
- 같은 스킬이라도 턴마다 다른 모델을 쓸 수 있다.  
- 감사 로그·토탈 보고에 `skill + model + brief`를 남긴다.

예 (Main 판단 — 모델명은 예시일 뿐, 고정 아님):

- 스펙 구멍 → `spawn(skill=spec-rereview, model=…)`  
- QA FAIL이 구현 버그 → `spawn(skill=implement, model=…)`  
- 단순 패치 → 싼/빠른 모델로 implement  
- 어려운 스펙 정리 → 강한 모델로 Docs형 스킬  

### 0.4 지금 vs 목표

| | 지금 (초안) | Deep Agents 목표 |
|--|-------------|------------------|
| Main | `on_event → route(next)` 스위치에 가까움 | **`tick(state)` 루프** |
| 작업 | Spec / Impl / QA 고정 워커 | Main이 **`spawn(skill, model, brief, …)`** |
| 모델 | (과거) 사람이 표로 배치 | **표 없음 — Main이 매 호출 선택** |
| FS | md를 사람이 넘기는 계약 | `runs/{run_id}/`에 에이전트가 쓰고 공유 |
| 실패 | 고정 레일 되돌림 | Main이 **재계획 + 모델 재선택** |

§1~8의 SDD 파이프라인은 Deep 루프의 **기본 경로 + 하드 제약**이다.

**Deep이 되어도 안 푸는 SDD 제약**

- 빈 칸 추정 금지 · 질문 답은 사람  
- `spec.verify.passed` 없이 implement 불가  
- QA ≠ Writer (세션 분리)  
- 워커는 다음 단계를 직접 호출하지 않음 — **이벤트만**, 다음은 Main이 spawn  

### 0.5 구현 우선순위

1. Main `tick` + Todo + `runs/{run_id}/`  
2. `spawn(skill, model, brief, tools, budget)` — **model은 Main 필수 인자**  
3. 가용 모델 목록 API (키/엔드포인트만; 배치표 아님)  
4. PASS 6항 **스크립트** 게이트  
5. FAIL 시 재계획 + 모델 재선택을 감사 로그에 고정  

스택 후보: Cursor Skills MVP → [LangGraph / `deepagents`](https://www.langchain.com/blog/deep-agents) 실험.

---

## 1. 캐노니컬 파이프라인 (기본 경로 · SDD 제약)

> Deep Main이 보통 따르는 경로. 매 전이의 **최종 결정(스킬·모델)은 Main**이고, 아래 게이트는 코드가 막는다.

| # | 단계 | 주체 | 산출물 |
|---|------|------|--------|
| 1 | **PRD 작성** | 사람 | PRD (요구·범위·왜) |
| 2 | **SPEC 작성** | Main → spawn(스킬+모델) | 스펙 초안 md |
| 3 | **SPEC 검토 (질문 추출)** | Main → spawn | 질문지 md |
| 4 | **질문 답변** | 사람 | 답변 채워진 질문지 md |
| 5 | **스펙 재검토·반영** | Main → spawn | 보강된 스펙 + PASS/FAIL |
| 6 | **코드 구현** | Main → spawn | 브랜치 / PR |
| 7 | **QA** | Main → spawn (Impl과 **다른 세션**) | 증거 / 리포트 |
| 8 | **토탈 보고** | **Main** | 런 요약 (단계·해시·모델 이력·판정) |

```
[사람] PRD
   │
   ▼  prd.ready
[Main] tick → spawn(skill=spec-write, model=…)
   │
   ▼  spec.draft.ready
[Main] tick → spawn(skill=question-extract, model=…)
   │
   ▼  questions.ready  ── wait_human ──► [사람] 답변
   │
   ▼  answers.ready
[Main] tick → spawn(skill=spec-rereview, model=…)
   │
   ▼  spec.verify.passed   (코드 게이트)
[Main] tick → spawn(skill=implement, model=…)
   │
   ▼  impl.pr.ready
[Main] tick → spawn(skill=qa, model=…)   # 세션 분리
   │
   ▼  qa.passed | qa.failed
[Main] 재계획·모델 재선택 또는 토탈 보고
```

**AI 시작점 = 단계 2 (SPEC).**  
PRD와 질문 답변(4)만 사람이다. Main은 스펙/코드 **본문을 직접 쓰지 않고**, 모델 세션을 띄워 시킨다.

스택(프레임워크 등)은 **별도 Tech Plan 게이트를 두지 않는다.**  
기존 레포면 현행 스택을 따르고, 선택이 필요하면 SPEC/PRD에 이미 박혀 있거나 implement 스킬이 레포 컨벤션을 따른다. (상세: [`docs/deep-review.md`](docs/deep-review.md))

---

## 2. 한 줄 요약

사람은 **PRD**와 **검수 질문 답**만 쓴다.  
**Main이 매 턴 스킬과 모델을 골라 호출**하고, 마지막에 **토탈 보고**한다.  
빈 칸은 AI가 추정으로 메우지 않는다.  
모델 고정 배치는 없다.

---

## 3. 메인 ↔ 워커 관계

```
              ┌──────────────────────────┐
              │  Main (최상위)            │
              │  tick / Todo / spawn     │
              │  model 선택 / gate       │
              │  audit / report          │
              │  스펙·코드 본문 금지      │
              └────────────┬─────────────┘
                           │ spawn(skill, model, brief, …)
     ┌─────────┬───────────┼─────────┬─────────┬─────────┐
     ▼         ▼           ▼         ▼         ▼         ▼
  SpecWriter  Question   (Human)  SpecPatch  Implement  QA
  Extractor              Answers  Re-review
       └── 전부 Main이 고른 모델 세션 ──┘
```

| | Main | Worker (서브 세션) |
|--|------|---------------------|
| 역할 | **스킬·모델·타이밍** 판단, 게이트, 감사, 토탈 보고 | 자기 스킬 산출물만 |
| 트리거 | `tick(state)` 후 spawn | 끝나면 이벤트만 (**다음 단계 직접 호출 금지**) |
| 모델 | Main 자신 + **매 spawn마다 Main이 지정** | 지정받은 모델로만 실행 |

---

## 4. 단계별 계약

### 1 — PRD 작성 (사람)

**입력:** 요구사항, 미팅 결과, 필요성 판단  
**산출:** PRD md  
**완료:** `prd.ready`  
**다음:** Main이 `spec-write` + 모델 선택 후 spawn

메인은 PRD를 쓰지 않는다. 오케스트레이션 시작 신호만 받는다.

---

### 2 — SPEC 작성 ★ AI 시작

**입력:** PRD + **SPEC 보일러플레이트** (필수 섹션 골격)  
**동작:** Main이 고른 모델 세션이 보일러를 채운 스펙 초안 생성. 모르는 칸은 `TBD` / 빈 `답:` — **추정으로 채우지 않음**  
**산출:** `spec.md` (draft)  
**완료:** `spec.draft.ready`  
**다음:** Main이 `question-extract` + 모델 spawn

---

### 3 — SPEC 검토 · 질문 추출

**입력:** draft 스펙 + **스펙리뷰 프롬프트** (예: REVIEW-PROMPT) + 툴(OpenSpec / Spec Kit 등)  
**동작:** 프롬프트 규칙으로 툴을 돌려 **질문지만** 뽑는다. 답을 쓰지 않는다.  
**산출:** `questions.md` (`답:` 공란)  
**완료:** `questions.ready` → Main **wait_human**  
**다음:** 사람 답변 (단계 4)

**구멍 taxonomy (찾을 것)**

- 인수조건에 판별·계산 규칙 없음  
- `~라면` / `또는`에 분기 조건 없음  
- 절 간 모순 (구분하라 vs 뭉뚱그림)  
- 표시만 있고 산출·출처 없음  
- 화면만 있고 데이터 출처 없음  

**묻지 말 것:** 사인·프로세스·이미 결정·구현 재량(컴포넌트 구조 등)  
**규칙:** 질문마다 “답이 없으면 AI가 무엇을 잘못 만드는가” 한 줄. 선택지+추천안.

---

### 4 — 질문 답변 (사람)

**입력:** `questions.md`  
**동작:** 사람이 `답:`만 채움. AI 추정 답 금지.  
**산출:** `answers.md` (또는 같은 파일에 기입)  
**완료:** `answers.ready`  
**다음:** Main이 `spec-rereview` + 모델 spawn

---

### 5 — 스펙 재검토 · 반영

**입력:** draft 스펙 + 사람 답변  
**동작:**

1. 답을 스펙에 반영  
2. 재스캔 (같은 리뷰 프롬프트 / PASS 조건)  
3. 잔여 구멍이면 Main이 다시 질문 루프 spawn 또는 FAIL  

**Verify PASS (단계 6 허용 — 전부 필수, 코드 게이트)**

1. 인수조건마다 판별/계산/출처 있음  
2. 조건문·`또는`에 분기 조건 있음  
3. 절 간 모순 0  
4. 추정 답 0  
5. 잔여 차단 질문 0 (또는 명시적 재질문 루프)  
6. 승인 토큰 + **spec hash** (파일 존재 ≠ PASS)  

상세: [`docs/deep-review.md`](docs/deep-review.md)

**산출:** verified `spec.md` + hash  
**완료:** `spec.verify.passed`  
**다음:** Main이 `implement` + 모델 spawn  
**FAIL:** Main이 사람/PRD로 되돌리기

---

### 6 — 코드 구현

**입력:** verified 스펙만 (+ 대상 레포)  
**동작:**

- Main이 고른 모델로 스펙 → 구현 → PR  
- **기존 레포 스택·컨벤션 준수**  
- 스펙 밖 기능 금지  
- 새 구멍 → 추정 금지 → Main에 에스컬레이션  

**산출:** PR / 브랜치  
**완료:** `impl.pr.ready`  
**다음:** Main이 `qa` + 모델 spawn (Impl과 세션 분리)

| AI 재량 | AI 금지 |
|---------|---------|
| 컴포넌트 쪼개기, 로딩 UI 디테일 | 스택 교체, 스펙 없는 API, 권한·산식 임의 해석 |

---

### 7 — QA

**입력:** verified 스펙 인수조건(조항 ID) + PR  
**동작:** 조항 → 체크/테스트 **증거** 매핑. Writer/Impl과 **다른 세션** (모델이 같아도 세션은 분리).  
**완료:** `qa.passed` | `qa.failed`  
**FAIL:** Main이 implement 재spawn(다른 모델 가능) 또는 Docs형/사람

---

### 8 — 토탈 보고 (Main)

QA 종료 후 메인이 런 전체를 요약한다.

**보고에 넣을 것**

- `run_id`, 피처명, 시작~종료  
- 단계별 상태·타임스탬프·산출물 경로  
- **spawn 이력: skill + model + brief**  
- PRD / 스펙 / 질문지 / 답변 / PR 링크  
- spec hash, 최종 판정 (PASS/FAIL), FAIL 게이트  
- (선택) 미결 TBD, 사람 대기 횟수, 모델별 비용  

**산출:** `report.md` (또는 JSON) + 이벤트 `run.report.ready`

---

## 5. 이벤트 (트리거)

이벤트는 Main의 **입력 신호**다. 다음 워커를 이벤트가 직접 부르지 않는다 — **Main이 skill+model로 spawn**.

| 이벤트 | 발행 | Main 동작 (전형) |
|--------|------|------------------|
| `prd.ready` | 사람 | spawn(spec-write, model=…) |
| `spec.draft.ready` | Spec Writer | spawn(question-extract, model=…) |
| `questions.ready` | Question Extractor | **wait_human** |
| `answers.ready` | 사람 | spawn(spec-rereview, model=…) |
| `spec.verify.passed` | Spec Re-review | spawn(implement, model=…) — 게이트 통과 시 |
| `spec.verify.failed` | Spec Re-review | 재계획 / 보고 / 사람 |
| `impl.pr.ready` | Impl | spawn(qa, model=…) |
| `qa.passed` / `qa.failed` | QA | 재계획·모델 재선택 또는 **토탈 보고** |
| `run.report.ready` | Main | 런 종료 |

페이로드에는 항상 `run_id` + (해당 시) **spec hash**.  
스펙이 바뀌면 하위 단계 invalidate.

```json
{
  "event": "questions.ready",
  "run_id": "run_20260909_crm_metrics",
  "feature_id": "crm-metrics",
  "spec_ref": "docs/.../spec.md@sha",
  "actor": "question-extractor",
  "artifacts": {
    "questions": "docs/.../questions.md"
  }
}
```

---

## 6. 왜 이 구조인가

- **한 에이전트 end-to-end** → 검증이 구현에 오염되고, 빈 칸을 메우며, 실패 지점이 안 보임  
- **Main이 스킬·모델까지 판단** → 순수 Deep Agents  
- **질문은 AI · 답은 사람** → SDD 계약 유지  
- **3→4→5 분리** → 질문 추출 / 사람 답 / 반영·재검토를 섞지 않음  
- **완료 이벤트** → Main이 재실행·감사·사람 대기를 삽입하기 쉬움  
- **모델 고정표 없음** → “배치표 오케스트레이션”과 구분  
- **SDD 게이트는 코드** → Deep 자율이 추정·스킵으로 새지 않게 함  

심층 검토·업계 레퍼런스: [`docs/deep-review.md`](docs/deep-review.md)

---

## 7. 툴·스택 후보

> 초안. 실험 후 고정.

### 7.1 오케스트레이션

| 후보 | 메모 |
|------|------|
| Cursor Skills + 상태 파일 + GH Issue/PR | 1차 MVP (수동 tick에 가깝) |
| **LangGraph / [`deepagents`](https://www.langchain.com/blog/deep-agents)** | **목표 런타임** — Main 루프·모델 호출 spawn |
| GitHub Actions | 라벨/`answers.ready` 트리거 |
| Inngest / Temporal | 사람 대기가 길어질 때 |

### 7.2 단계별 자리 (스킬 — 모델 미고정)

| 단계 | 스킬·툴 |
|------|---------|
| Main | Todo + `tick` + **`spawn(skill, model, …)`** |
| 2~5 | SPEC 보일러 + REVIEW-PROMPT (+ OpenSpec / Spec Kit 등) |
| 5 재검토 | 동일 프롬프트 + PASS 6항 **스크립트** |
| 6 구현 | implement 스킬 (에디터/에이전트 런타임) |
| 7 QA | qa 스킬 — 인수조건 매핑 + (선택) OpenSpec `/opsx:verify` |

메인이 **스킬과 모델을 함께** spawn한다. 툴은 플러그인. **모델 기본값 테이블 없음.**

### 7.3 디렉터리 스케치

```
/
├── README.md
├── docs/
│   ├── deep-review.md
│   ├── gates.md
│   └── templates/          # SPEC 보일러플레이트
├── schemas/
│   ├── events.schema.json
│   ├── run-state.schema.json
│   └── models.available.json  # 런타임이 노출하는 가용 모델 목록 (배치표 아님)
├── skills/
│   ├── spec-write/
│   ├── question-extract/   # REVIEW-PROMPT 바인딩
│   ├── spec-rereview/
│   ├── implement/
│   └── qa/
├── runs/                   # run_id별 작업공간
├── workflows/
└── examples/
```

스펙·PRD 본체는 **대상 서비스 레포**에 두고, 이 레포는 오케스트레이션·스킬·템플릿만 둔다.

---

## 8. 메인 API 초안

```text
capabilities:
  - register_run(feature_id, prd_ref)
  - tick(state) -> plan | spawn | wait_human | finish
  - list_models() -> [model_id, …]                 # 가용 목록만
  - spawn(skill, model, brief, tools, budget) -> worker_run  # model 필수
  - update_todo(run_id, items)
  - on_event(event)
  - enforce_gate(stage) -> pass | wait_human | fail
  - write_audit(run_id, event)       # skill+model 이력
  - escalate(run_id, reason)
  - write_total_report(run_id)
```

**하지 않는 것:** PRD 작성, 질문 답 추정, 검증 스킵 후 구현, QA 스킵 머지 승인, Main이 스펙/코드 본문을 직접 작성, **역할→모델 고정표 운영**.

---

## 9. MVP 로드맵

### Phase 0 — 문서

- [x] 레포 / README  
- [x] [`docs/deep-review.md`](docs/deep-review.md)  
- [x] 캐노니컬 파이프라인 1~8 (기본 경로)  
- [x] Deep Agents: Main이 스킬·**모델** 자율 호출 (§0) — **모델 배치표 폐기**  
- [ ] `docs/gates.md` + 이벤트 스키마  
- [ ] `models.available` 스키마 (목록만)  
- [ ] SPEC 보일러플레이트 템플릿  

### Phase 1 — 수동 트리거 (게이트부터)

- [ ] REVIEW-PROMPT → Question Extractor skill  
- [ ] `questions.md` / `answers.md` 계약 + `답:` 공란 검사  
- [ ] 단계 5 PASS 6항 체크 (**스크립트**)  
- [ ] Impl → QA 수동 연쇄  
- [ ] 메인 **토탈 보고** 템플릿 (skill+model 이력)  

### Phase 2 — Main 루프 (Deep Agents)

- [ ] `tick(state)` + Todo + `runs/{run_id}/`  
- [ ] `spawn(skill, model, brief, …)` — model Main 선택  
- [ ] `list_models()` 가용 목록  
- [ ] 이벤트 → Main 입력 (워커가 다음 워커 직접 호출 금지)  
- [ ] FAIL 시 재계획 + 모델 재선택 — 감사 로그  

### Phase 3 — 자동 트리거·운영

- [ ] `prd.ready` → … → `qa.*` → `run.report.ready` 무인 루프 (사람 게이트만 대기)  
- [ ] budget / 비용 상한 (모델 강제 배정 아님)  
- [ ] LangGraph / `deepagents` 런타임 정착  

### Phase 4 — 확장

- [ ] 미팅 녹음 → PRD 보조 (확정은 사람)  
- [ ] 이슈 트래커 연동  
- [ ] 다중 레포 Impl  

---

## 10. 성공 기준

1. PRD·질문 답 외 **빈 칸 추정 0**  
2. `spec.verify.passed` 없이 implement spawn **코드가 거부**  
3. 3→4→5가 **파일로 분리**되어 추적 가능  
4. QA 후 **토탈 보고**가 항상 남음  
5. `run_id`로 PRD→질문→스펙해시→PR→QA→보고 연결  
6. 실무 1건 E2E (예: CRM 지표)  
7. Main이 스펙/코드를 직접 쓰지 않고 **spawn만** 한다  
8. FAIL 시 Main의 **재계획·모델 재선택**이 감사 로그에 남는다  
9. **역할→모델 고정표가 코드/문서에 없음** — 매 호출의 model은 Main이 고른 기록만 존재  

---

## 11. 비목표

- PRD까지 AI가 단독 확정  
- 사람 답 없이 스펙 PASS  
- 배포/롤백 오케스트레이션  
- SDD 툴 자체를 다시 만들기 (우리는 **오케스트레이션**)  
- **역할별 모델 고정 배치표를 Deep Agents라고 부르는 것**  
- 사람이 턴마다 모델을 수동 지정하는 운영을 기본으로 두는 것  

---

## 12. 리스크

| 리스크 | 대응 |
|--------|------|
| AI가 답 칸을 채움 | `answers_must_be_human`, CI로 `답:` 검사 |
| 3·5를 한 세션에 섞음 | 산출물 파일·이벤트·세션 분리 강제 |
| 스펙 드리프트 | spec hash + invalidate |
| 프롬프트만 게이트 | fail-closed 스크립트/훅 |
| 보고 누락 | QA 종료 시 메인 `write_total_report` 필수 |
| Main이 일을 가로챔 | 스펙/코드 작성 도구를 Main에 주지 않음 |
| Deep를 자율 추정으로 착각 | SDD 제약은 tick 안에서도 fail-closed |
| 모델 남발·비용 폭증 | budget 상한, 감사, (선택) 런당 비용 캡 — **배치표로 해결하지 않음** |

---

## 13. 다음 할 일

1. SPEC 보일러플레이트 (`docs/templates/`)  
2. `schemas/events.schema.json` + `models.available`  
3. Question Extractor ← REVIEW-PROMPT 바인딩  
4. 토탈 보고 템플릿 (skill+model 이력) + 샘플 런 1건  
5. Main `tick` / `spawn(skill, model, …)` / `runs/` 초안 구현 (Phase 2)  
