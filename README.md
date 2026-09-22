# Workflow Orchestration Agent

> AI 에이전트 멀티플렉서 — PRD(사람) 이후 SPEC부터 구현·QA까지 오케스트레이션한다.  
> 목표는 **실제 Deep Agents**: **최상위 Main(Astra)이 매 턴 상태를 보고 다음에 어떤 서브(역할)를 띄울지 스스로 판단**한다.  
> 모델만 사람이 바꿔 끼우는 것이 아니다. SDD 게이트는 그 루프 안의 **하드 제약**으로 남긴다.

**Status:** Draft / Research  
**Repo:** [heh139811-droid/work_flow_ochestration_agent](https://github.com/heh139811-droid/work_flow_ochestration_agent)  
**Local clone:** `C:\Users\PC\Documents\work_flow_ochestration_agent`

---

## 0. Deep Agents — Main이 알아서 고른다

### 0.1 정의 (이 레포의 진실)

학계 고유 용어라기보다 [LangChain Deep Agents](https://www.langchain.com/blog/deep-agents)가 정리한 패턴이다.  
영감: **Claude Code / Deep Research / Manus**. shallow 툴루프와 대비해 Deep의 조건은 다음이다.

1. **계획(Todo)** — Main이 목표를 쪼개 추적  
2. **서브에이전트 위임** — Main이 일을 직접 하지 않고 **spawn**  
3. **파일시스템 작업공간** — `runs/{run_id}/`에 중간 산출물 공유  
4. **자세한 시스템 프롬프트** — 역할·게이트·금지 명시  

**Deep가 아닌 것:** 사람이 “이번엔 Opus, 저번엔 Sol”만 바꾸는 것.  
**Deep인 것:** Astra가 상태를 읽고 **Docs / Impl / QA / wait_human / finish** 중 무엇을 할지 **스스로 선택**하는 것.

### 0.2 어떻게 “알아서” 가능한가

마법이 아니다. Main에게 **`spawn` 툴**을 주고, 매 턴 `tick` 루프를 돌린다.

```
① Main(Astra) tick
   state / Todo / 이벤트 / 실패 요약 읽음
   → 다음 행동 결정: plan | spawn | wait_human | finish

② Main이 툴 호출 (LLM이 고르는 지점)
   spawn(role="Docs", brief="질문 3개 반영해 SPEC 재작성", budget=…)

③ 런타임 (코드 — 모델이 아님)
   역할 레지스트리: Docs → 기본 Opus
   (허용 시 Main이 model= 으로 escalation)
   → 새 세션으로 서브 실행
   → 산출물은 FS, Main에는 요약만 반환 (컨텍스트 오염 방지)

④ 게이트 (코드, fail-closed)
   예: spec.verify.passed 없으면 Impl spawn 거부
```

| 누가 | 뭘 고름 |
|------|---------|
| **Main (LLM)** | **역할·타이밍·brief·재계획** |
| **레지스트리 (설정)** | 역할 → 기본 모델 |
| **게이트 (코드)** | 금지 전이, 예산, 세션 분리, PASS 스크립트 |

모델 선택은 대개 **역할 선택의 부작용**이다.  
escalation이 필요하면 `spawn(role="Impl", model="…")`처럼 **허용 목록 안에서만** override.

### 0.3 모델 배치 (기본값 — Main이 override 가능)

| 역할 | 모델 (가칭) | 하는 일 |
|------|-------------|---------|
| **Main** | Astra (최상위) | Todo·tick·spawn·게이트·재시도·토탈 보고. **스펙/코드 본문 금지** |
| **Docs 서브** | Opus | SPEC·질문 추출·답 반영·재검토 (PRD 확정은 사람) |
| **Impl 서브** | 5.6 sol | verified SPEC만 보고 구현·PR |
| **QA 서브** | 5.6 sol | PRD+SPEC 대비 에러·엣지·증거. **Impl과 세션 분리** |

예 (Main 판단):

- 스펙 구멍 → `spawn(Docs)`  
- QA FAIL이 구현 버그 → `spawn(Impl)`  
- QA FAIL이 스펙 모호 → `spawn(Docs)` 또는 `wait_human`  
- 단순 핫픽스 → Impl만, Docs 스킵 (게이트가 허용할 때만)

### 0.4 지금 vs 목표

| | 지금 (초안) | Deep Agents 목표 |
|--|-------------|------------------|
| Main | `on_event → route(next)` 스위치에 가까움 | **`tick(state)` 루프** — 매 턴 자율 판단 |
| 서브 | Spec / Impl / QA **고정 워커 표** | Main이 **`spawn(role, brief, …)`** |
| 모델 | 사람이 표대로 배치 | 표는 **기본값**, Main이 역할(및 허용 시 모델) 선택 |
| FS | md를 사람이 넘기는 계약 | `runs/{run_id}/`에 에이전트가 쓰고 공유 |
| 실패 | 고정 레일 되돌림 | Main이 **재계획** (질문 루프 / PRD 되돌림 / 부분 Impl) |

§1~8의 SDD 파이프라인은 “항상 같은 레일”이 아니라, Deep 루프가 따르는 **기본 경로 + 하드 제약**이다.

**Deep이 되어도 안 푸는 SDD 제약**

- 빈 칸 추정 금지 · 질문 답은 사람  
- `spec.verify.passed` 없이 Impl 불가  
- QA ≠ Writer (세션 분리)  
- 워커는 다음 단계를 직접 호출하지 않음 — **이벤트만**, 다음은 Main이 spawn  

### 0.5 구현 우선순위

1. Main `tick` + Todo + `runs/{run_id}/`  
2. `spawn(role, brief, tools, budget)` + 역할→모델 레지스트리  
3. PASS 6항 **스크립트** 게이트 (프롬프트 ≠ 게이트)  
4. FAIL 시 재계획 정책을 Main 프롬프트 + 감사 로그에 고정  

스택 후보: Cursor Skills MVP → [LangGraph / `deepagents`](https://www.langchain.com/blog/deep-agents) 실험.

---

## 1. 캐노니컬 파이프라인 (기본 경로 · SDD 제약)

> Deep Main이 보통 따르는 경로. 매 전이의 **최종 결정은 Main의 spawn**이고, 아래 게이트는 코드가 막는다.

| # | 단계 | 주체 | 산출물 |
|---|------|------|--------|
| 1 | **PRD 작성** | 사람 | PRD (요구·범위·왜) |
| 2 | **SPEC 작성** | **Docs (AI)** | 스펙 초안 md |
| 3 | **SPEC 검토 (질문 추출)** | **Docs (AI)** | 질문지 md |
| 4 | **질문 답변** | 사람 | 답변 채워진 질문지 md |
| 5 | **스펙 재검토·반영** | **Docs (AI)** | 보강된 스펙 + PASS/FAIL |
| 6 | **코드 구현** | **Impl (AI)** | 브랜치 / PR |
| 7 | **QA** | **QA (AI)** | 인수조건 대비 증거 / 리포트 |
| 8 | **토탈 보고** | **Main** | 런 요약 (단계·해시·산출물·판정) |

```
[사람] PRD
   │
   ▼  prd.ready
[Main] tick → spawn(Docs)  SPEC 작성
   │
   ▼  spec.draft.ready
[Main] tick → spawn(Docs)  질문 추출
   │
   ▼  questions.ready  ── wait_human ──► [사람] 답변
   │
   ▼  answers.ready
[Main] tick → spawn(Docs)  재검토·반영
   │
   ▼  spec.verify.passed   (코드 게이트)
[Main] tick → spawn(Impl)
   │
   ▼  impl.pr.ready
[Main] tick → spawn(QA)
   │
   ▼  qa.passed | qa.failed
[Main] 재계획 또는 토탈 보고
```

**AI 시작점 = 단계 2 (SPEC).**  
PRD와 질문 답변(4)만 사람이다. Main은 스펙/코드를 쓰지 않고 **판단·위임·보고만** 한다.

스택(프레임워크 등)은 **별도 Tech Plan 게이트를 두지 않는다.**  
기존 레포면 현행 스택을 따르고, 선택이 필요하면 SPEC/PRD에 이미 박혀 있거나 Impl이 레포 컨벤션을 따른다. (상세: [`docs/deep-review.md`](docs/deep-review.md))

---

## 2. 한 줄 요약

사람은 **PRD**와 **검수 질문 답**만 쓴다.  
**Main(Astra)이 매 턴 판단해** Docs(Opus) / Impl(Sol) / QA(Sol) / 사람을 spawn하고, 마지막에 **토탈 보고**한다.  
빈 칸은 AI가 추정으로 메우지 않는다.  
§1 파이프라인은 Deep 루프의 **기본 경로 + SDD 게이트**다.

---

## 3. 메인 ↔ 워커 관계

```
              ┌──────────────────────────┐
              │  Main (Astra)            │
              │  tick / Todo / spawn     │
              │  gate / audit / report   │
              │  스펙·코드 본문 금지      │
              └────────────┬─────────────┘
                           │ spawn(role, brief, …)
     ┌─────────┬───────────┼─────────┬─────────┬─────────┐
     ▼         ▼           ▼         ▼         ▼         ▼
  SpecWriter  Question   (Human)  SpecPatch  Impl      QA
  (Docs)      Extractor  Answers  Re-review  (Sol)     (Sol)
```

| | Main | Worker (서브) |
|--|------|----------------|
| 역할 | 상태 보고 **다음에 누굴 spawn할지 판단**, 게이트, 감사, 토탈 보고 | 자기 역할 산출물만 |
| 트리거 | `tick(state)` 또는 이벤트 수신 후 spawn | 끝나면 이벤트만 발행 (**다음 단계 직접 호출 금지**) |
| 모델 | Astra | 레지스트리 기본값 (Docs=Opus, Impl/QA=Sol). Main이 허용 목록 내 override 가능 |

---

## 4. 단계별 계약

### 1 — PRD 작성 (사람)

**입력:** 요구사항, 미팅 결과, 필요성 판단  
**산출:** PRD md  
**완료:** `prd.ready`  
**다음:** Main이 Docs(Spec Writer) spawn

메인은 PRD를 쓰지 않는다. 오케스트레이션 시작 신호만 받는다.

---

### 2 — SPEC 작성 (Docs) ★ AI 시작

**입력:** PRD + **SPEC 보일러플레이트** (필수 섹션 골격)  
**동작:** 보일러를 채운 스펙 초안 생성. 모르는 칸은 `TBD` / 빈 `답:` — **추정으로 채우지 않음**  
**산출:** `spec.md` (draft)  
**완료:** `spec.draft.ready`  
**다음:** Main이 Question Extractor spawn

---

### 3 — SPEC 검토 · 질문 추출 (Docs)

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
**다음:** Main이 Spec Re-review spawn

---

### 5 — 스펙 재검토 · 반영 (Docs)

**입력:** draft 스펙 + 사람 답변  
**동작:**

1. 답을 스펙에 반영  
2. 재스캔 (같은 리뷰 프롬프트 / PASS 조건)  
3. 교차규칙·잔여 구멍 있으면 Main이 다시 Docs 질문 루프 또는 FAIL  

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
**다음:** Main이 Impl spawn  
**FAIL:** Main이 사람/PRD로 되돌리기

---

### 6 — 코드 구현 (Impl)

**입력:** verified 스펙만 (+ 대상 레포)  
**동작:**

- 스펙 → 구현 → PR  
- **기존 레포 스택·컨벤션 준수** (새 프레임워크 슬쩍 도입 금지)  
- 스펙 밖 기능 금지  
- 새 구멍이 보이면 추정 금지 → Main에 에스컬레이션 → Docs 루프  

**산출:** PR / 브랜치  
**완료:** `impl.pr.ready`  
**다음:** Main이 QA spawn

| AI 재량 | AI 금지 |
|---------|---------|
| 컴포넌트 쪼개기, 로딩 UI 디테일 | 스택 교체, 스펙 없는 API, 권한·산식 임의 해석 |

---

### 7 — QA (QA 서브)

**입력:** verified 스펙 인수조건(조항 ID) + PR  
**동작:** 조항 → 체크/테스트 **증거** 매핑. Writer/Impl과 **다른 세션**.  
**완료:** `qa.passed` | `qa.failed`  
**FAIL:** Main이 Impl 재spawn, 또는 스펙 구멍이면 Docs/사람

---

### 8 — 토탈 보고 (Main)

QA 종료 후 메인이 런 전체를 요약한다.

**보고에 넣을 것**

- `run_id`, 피처명, 시작~종료  
- 단계별 상태·타임스탬프·산출물 경로  
- Main이 고른 spawn 이력 (역할·brief·모델)  
- PRD / 스펙 / 질문지 / 답변 / PR 링크  
- spec hash, 최종 판정 (PASS/FAIL), FAIL이면 어느 게이트에서 깨졌는지  
- (선택) 미결 TBD, 사람 대기 횟수  

**산출:** `report.md` (또는 JSON) + 이벤트 `run.report.ready`

---

## 5. 이벤트 (트리거)

이벤트는 Main의 **입력 신호**다. 다음 워커를 이벤트가 직접 부르지 않는다 — **Main이 spawn**.

| 이벤트 | 발행 | Main 동작 (전형) |
|--------|------|------------------|
| `prd.ready` | 사람 | spawn(Docs / Spec Writer) |
| `spec.draft.ready` | Spec Writer | spawn(Docs / Question) |
| `questions.ready` | Question Extractor | **wait_human** |
| `answers.ready` | 사람 | spawn(Docs / Re-review) |
| `spec.verify.passed` | Spec Re-review | spawn(Impl) — 게이트 통과 시 |
| `spec.verify.failed` | Spec Re-review | 재계획 / 보고 / 사람 |
| `impl.pr.ready` | Impl | spawn(QA) |
| `qa.passed` / `qa.failed` | QA | 재계획 또는 **토탈 보고** |
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
- **Main이 판단 · 서브가 실행** → 실제 Deep Agents  
- **질문은 AI · 답은 사람** → SDD 계약 유지  
- **3→4→5 분리** → 질문 추출 / 사람 답 / 반영·재검토를 섞지 않음  
- **완료 이벤트** → Main이 재실행·감사·사람 대기를 삽입하기 쉬움  
- **모델 표는 기본값** → “모델만 교체”를 Deep라고 부르지 않음  
- **SDD 게이트는 코드** → Deep 자율이 추정·스킵으로 새지 않게 함  

심층 검토·업계 레퍼런스: [`docs/deep-review.md`](docs/deep-review.md)

---

## 7. 툴·스택 후보

> 초안. 실험 후 고정.

### 7.1 오케스트레이션

| 후보 | 메모 |
|------|------|
| Cursor Skills + 상태 파일 + GH Issue/PR | 1차 MVP (수동 tick에 가깝) |
| **LangGraph / [`deepagents`](https://www.langchain.com/blog/deep-agents)** | **목표 런타임** — Main 루프·서브스폰 |
| GitHub Actions | 라벨/`answers.ready` 트리거 |
| Inngest / Temporal | 사람 대기가 길어질 때 |

### 7.2 단계별 툴 자리

| 단계 | 후보 |
|------|------|
| Main | Astra — Todo + `tick` + **`spawn`** |
| 2~5 Docs | Opus + SPEC 보일러 + REVIEW-PROMPT |
| 3 질문 추출 | **REVIEW-PROMPT** + OpenSpec explore / Spec Kit clarify |
| 5 재검토 | 동일 프롬프트 + PASS 6항 **스크립트** |
| 6 구현 | 5.6 sol (Cursor / Claude Code 등) |
| 7 QA | 5.6 sol — 인수조건 매핑 + (선택) OpenSpec `/opsx:verify` |

메인이 역할별로 skill을 **spawn**한다. 툴은 플러그인. 모델은 레지스트리 기본값.

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
│   └── role-registry.json  # role → default model, tools, budget
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
  - tick(state) -> plan | spawn | wait_human | finish   # 핵심
  - spawn(role, brief, tools, budget, model?) -> worker_run
  - update_todo(run_id, items)
  - on_event(event)                  # tick 입력으로 합침
  - enforce_gate(stage) -> pass | wait_human | fail
  - write_audit(run_id, event)       # spawn 이력 포함
  - escalate(run_id, reason)
  - write_total_report(run_id)
```

**하지 않는 것:** PRD 작성, 질문 답 추정, 검증 스킵 후 구현, QA 스킵 머지 승인, Main이 스펙/코드 본문을 직접 작성, 레지스트리 밖 모델 임의 호출.

---

## 9. MVP 로드맵

### Phase 0 — 문서

- [x] 레포 / README  
- [x] [`docs/deep-review.md`](docs/deep-review.md)  
- [x] 캐노니컬 파이프라인 1~8 (기본 경로)  
- [x] Deep Agents: Main 자율 spawn 정의·메커니즘 (§0)  
- [ ] `docs/gates.md` + 이벤트 스키마  
- [ ] `role-registry` 스키마  
- [ ] SPEC 보일러플레이트 템플릿  

### Phase 1 — 수동 트리거 (게이트부터)

- [ ] REVIEW-PROMPT → Question Extractor skill  
- [ ] `questions.md` / `answers.md` 계약 + `답:` 공란 검사  
- [ ] 단계 5 PASS 6항 체크 (**스크립트**)  
- [ ] Impl → QA 수동 연쇄  
- [ ] 메인 **토탈 보고** 템플릿  

### Phase 2 — Main 루프 (Deep Agents)

- [ ] `tick(state)` + Todo + `runs/{run_id}/`  
- [ ] `spawn(role, brief, …)` + 역할→모델 레지스트리  
- [ ] 이벤트 → Main 입력 (워커가 다음 워커 직접 호출 금지)  
- [ ] FAIL 시 재계획 (질문 루프 vs PRD 되돌림 vs 부분 Impl) — 감사 로그  
- [ ] spawn 이력이 토탈 보고에 포함  

### Phase 3 — 자동 트리거·운영

- [ ] `prd.ready` → … → `qa.*` → `run.report.ready` 무인 루프 (사람 게이트만 대기)  
- [ ] 모델 escalation 정책 (비용·난이도)  
- [ ] LangGraph / `deepagents` 런타임 정착  

### Phase 4 — 확장

- [ ] 미팅 녹음 → PRD 보조 (확정은 사람)  
- [ ] 이슈 트래커 연동  
- [ ] 다중 레포 Impl  

---

## 10. 성공 기준

1. PRD·질문 답 외 **빈 칸 추정 0**  
2. `spec.verify.passed` 없이 Impl spawn **코드가 거부**  
3. 3→4→5가 **파일로 분리**되어 추적 가능  
4. QA 후 **토탈 보고**가 항상 남음  
5. `run_id`로 PRD→질문→스펙해시→PR→QA→보고 연결  
6. 실무 1건 E2E (예: CRM 지표)  
7. Main이 스펙/코드를 직접 쓰지 않고 **spawn만** 한다  
8. FAIL 시 Main의 **재계획 경로**가 감사 로그에 남는다  
9. “누가 다음 단계를 골랐는가” = **항상 Main** (사람이 모델만 교체한 기록이 아님)  

---

## 11. 비목표

- PRD까지 AI가 단독 확정  
- 사람 답 없이 스펙 PASS  
- 배포/롤백 오케스트레이션  
- SDD 툴 자체를 다시 만들기 (우리는 **오케스트레이션**)  
- **“모델만 바꿔 끼우기”를 Deep Agents라고 부르는 것**  
- Main이 레지스트리 없는 임의 모델로 자유 라우팅  

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
| spawn 남발·비용 폭증 | budget / 허용 역할 목록 / escalation 정책 |

---

## 13. 다음 할 일

1. SPEC 보일러플레이트 (`docs/templates/`)  
2. `schemas/events.schema.json` + `role-registry`  
3. Question Extractor ← REVIEW-PROMPT 바인딩  
4. 토탈 보고 템플릿 (spawn 이력 포함) + 샘플 런 1건  
5. Main `tick` / `spawn` / `runs/` 초안 구현 (Phase 2)  
