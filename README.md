# Workflow Orchestration Agent

> AI 에이전트 멀티플렉서 — PRD(사람) 이후 SPEC부터 구현·QA까지 오케스트레이션한다.

**Status:** Draft / Research  
**Repo:** [heh139811-droid/work_flow_ochestration_agent](https://github.com/heh139811-droid/work_flow_ochestration_agent)  
**Local clone:** `C:\Users\PC\Documents\work_flow_ochestration_agent`

---

## 1. 캐노니컬 파이프라인 (이 레포의 진실)

| # | 단계 | 주체 | 산출물 |
|---|------|------|--------|
| 1 | **PRD 작성** | 사람 | PRD (요구·범위·왜) |
| 2 | **SPEC 작성** | **AI** (SPEC 보일러플레이트 사용) | 스펙 초안 md |
| 3 | **SPEC 검토 (질문 추출)** | **AI** — 스펙리뷰 프롬프트 기반 툴 실행 | 질문지 md |
| 4 | **질문 답변** | 사람 | 답변 채워진 질문지 md |
| 5 | **스펙 재검토·반영** | **AI** | 보강된 스펙 + PASS/FAIL |
| 6 | **코드 구현** | **AI** | 브랜치 / PR |
| 7 | **QA** | **AI** | 인수조건 대비 증거 / 리포트 |
| 8 | **토탈 보고** | **메인 에이전트** | 런 요약 (단계·해시·산출물·판정) |

```
[사람] PRD
   │
   ▼  prd.ready
[AI]  SPEC 작성 (보일러플레이트)
   │
   ▼  spec.draft.ready
[AI]  SPEC 검토 = 리뷰 프롬프트 → 툴 실행 → 질문지.md
   │
   ▼  questions.ready  ── wait_human ──► [사람] 답변 기입
   │
   ▼  answers.ready
[AI]  스펙 재검토·반영 (PASS면 다음)
   │
   ▼  spec.verify.passed
[AI]  코드 구현
   │
   ▼  impl.pr.ready
[AI]  QA
   │
   ▼  qa.passed | qa.failed
[메인] 토탈 보고
```

**AI 시작점 = 단계 2 (SPEC).**  
PRD와 질문 답변(4)만 사람이다. 메인은 라우팅·게이트·최종 보고만 한다.

스택(프레임워크 등)은 **별도 Tech Plan 게이트를 두지 않는다.**  
기존 레포면 현행 스택을 따르고, 선택이 필요하면 SPEC/PRD에 이미 박혀 있거나 Impl이 레포 컨벤션을 따른다. (상세: [`docs/deep-review.md`](docs/deep-review.md))

---

## 2. 한 줄 요약

사람은 **PRD**와 **검수 질문 답**만 쓴다.  
메인이 **SPEC → 질문툴 → (사람 답) → 재검토 → 구현 → QA**를 이어 붙인 뒤 **토탈 보고**한다.  
빈 칸은 AI가 추정으로 메우지 않는다.

---

## 3. 메인 ↔ 워커 관계

```
              ┌──────────────────────┐
              │  Main Orchestrator   │
              │  route / gate / audit│
              │  + final report      │
              └──────────┬───────────┘
                         │ on_event → route(next)
     ┌─────────┬─────────┼─────────┬─────────┬─────────┐
     ▼         ▼         ▼         ▼         ▼         ▼
  SpecWriter  Question  (Human)  SpecPatch  Impl      QA
  (보일러)    Extractor  Answers  Re-review
```

| | Main | Worker |
|--|------|--------|
| 역할 | 상태머신, 게이트, 스킬 라우팅, 감사, **토탈 보고** | 자기 단계 산출물만 |
| 트리거 | 이전 단계 `*.ready` / `*.passed` 수신 후 다음 워커 기동 | 끝나면 이벤트만 발행 (다음 단계 직접 호출 금지) |

---

## 4. 단계별 계약

### 1 — PRD 작성 (사람)

**입력:** 요구사항, 미팅 결과, 필요성 판단  
**산출:** PRD md  
**완료:** `prd.ready`  
**다음:** Spec Writer

메인은 PRD를 쓰지 않는다. 오케스트레이션 시작 신호만 받는다.

---

### 2 — SPEC 작성 (AI) ★ AI 시작

**입력:** PRD + **SPEC 보일러플레이트** (필수 섹션 골격)  
**동작:** 보일러를 채운 스펙 초안 생성. 모르는 칸은 `TBD` / 빈 `답:` — **추정으로 채우지 않음**  
**산출:** `spec.md` (draft)  
**완료:** `spec.draft.ready`  
**다음:** Question Extractor

---

### 3 — SPEC 검토 · 질문 추출 (AI)

**입력:** draft 스펙 + **스펙리뷰 프롬프트** (예: REVIEW-PROMPT) + 툴(OpenSpec / Spec Kit 등)  
**동작:** 프롬프트 규칙으로 툴을 돌려 **질문지만** 뽑는다. 답을 쓰지 않는다.  
**산출:** `questions.md` (`답:` 공란)  
**완료:** `questions.ready` → 메인인 **wait_human**  
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
**다음:** Spec Re-review

---

### 5 — 스펙 재검토 · 반영 (AI)

**입력:** draft 스펙 + 사람 답변  
**동작:**

1. 답을 스펙에 반영  
2. 재스캔 (같은 리뷰 프롬프트 / PASS 조건)  
3. 교차규칙·잔여 구멍 있으면 다시 질문지 순환 (3←→4←→5) 또는 FAIL  

**Verify PASS (단계 6 허용 — 전부 필수)**

1. 인수조건마다 판별/계산/출처 있음  
2. 조건문·`또는`에 분기 조건 있음  
3. 절 간 모순 0  
4. 추정 답 0  
5. 잔여 차단 질문 0 (또는 명시적 재질문 루프)  
6. 승인 토큰 + **spec hash** (파일 존재 ≠ PASS)  

상세: [`docs/deep-review.md`](docs/deep-review.md)

**산출:** verified `spec.md` + hash  
**완료:** `spec.verify.passed`  
**다음:** Impl  
**FAIL:** 사람/PRD로 되돌리기

---

### 6 — 코드 구현 (AI)

**입력:** verified 스펙만 (+ 대상 레포)  
**동작:**

- 스펙 → 구현 → PR  
- **기존 레포 스택·컨벤션 준수** (새 프레임워크 슬쩍 도입 금지)  
- 스펙 밖 기능 금지  
- 새 구멍이 보이면 추정 금지 → 단계 3~5로 에스컬레이션  

**산출:** PR / 브랜치  
**완료:** `impl.pr.ready`  
**다음:** QA

| AI 재량 | AI 금지 |
|---------|---------|
| 컴포넌트 쪼개기, 로딩 UI 디테일 | 스택 교체, 스펙 없는 API, 권한·산식 임의 해석 |

---

### 7 — QA (AI)

**입력:** verified 스펙 인수조건(조항 ID) + PR  
**동작:** 조항 → 체크/테스트 **증거** 매핑. Writer와 다른 컨텍스트.  
**완료:** `qa.passed` | `qa.failed`  
**FAIL:** Impl로 (스펙 구멍이면 3~5로)

---

### 8 — 토탈 보고 (메인)

QA 종료 후 메인이 런 전체를 요약한다.

**보고에 넣을 것**

- `run_id`, 피처명, 시작~종료  
- 단계별 상태·타임스탬프·산출물 경로  
- PRD / 스펙 / 질문지 / 답변 / PR 링크  
- spec hash, 최종 판정 (PASS/FAIL), FAIL이면 어느 게이트에서 깨졌는지  
- (선택) 미결 TBD, 사람 대기 횟수  

**산출:** `report.md` (또는 JSON) + 이벤트 `run.report.ready`

---

## 5. 이벤트 (트리거)

| 이벤트 | 발행 | 메인 동작 |
|--------|------|-----------|
| `prd.ready` | 사람 | → Spec Writer |
| `spec.draft.ready` | Spec Writer | → Question Extractor |
| `questions.ready` | Question Extractor | → **wait_human** |
| `answers.ready` | 사람 | → Spec Re-review |
| `spec.verify.passed` | Spec Re-review | → Impl |
| `spec.verify.failed` | Spec Re-review | → 보고/되돌림 |
| `impl.pr.ready` | Impl | → QA |
| `qa.passed` / `qa.failed` | QA | → **토탈 보고** |
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
- **질문은 AI · 답은 사람** → SDD 계약 유지 (CRM 지표 실험과 동일)  
- **3→4→5 분리** → 질문 추출 / 사람 답 / 반영·재검토를 섞지 않음  
- **완료 이벤트** → 재실행·감사·사람 대기 삽입이 쉬움  
- **메인은 일 안 함 + 마지막에만 보고** → 멀티플렉서 본분

심층 검토·업계 레퍼런스: [`docs/deep-review.md`](docs/deep-review.md)

---

## 7. 툴·스택 후보

> 초안. 실험 후 고정.

### 7.1 오케스트레이션

| 후보 | 메모 |
|------|------|
| Cursor Skills + 상태 파일 + GH Issue/PR | 1차 MVP |
| GitHub Actions | 라벨/`answers.ready` 트리거 |
| Inngest / Temporal | 사람 대기가 길어질 때 |

### 7.2 단계별 툴 자리

| 단계 | 후보 |
|------|------|
| 2 SPEC | SPEC 보일러플레이트 + Cursor/Claude |
| 3 질문 추출 | **REVIEW-PROMPT** + OpenSpec explore / Spec Kit clarify |
| 5 재검토 | 동일 프롬프트 + PASS 6항 스크립트 |
| 6 구현 | Cursor / Claude Code / Copilot Agent |
| 7 QA | 인수조건 매핑 + (선택) OpenSpec `/opsx:verify` |

메인이 단계별로 skill을 **라우팅**한다. 툴은 플러그인.

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
│   └── run-state.schema.json
├── skills/
│   ├── spec-write/
│   ├── question-extract/   # REVIEW-PROMPT 바인딩
│   ├── spec-rereview/
│   ├── implement/
│   └── qa/
├── workflows/
└── examples/
```

스펙·PRD 본체는 **대상 서비스 레포**에 두고, 이 레포는 오케스트레이션·스킬·템플릿만 둔다.

---

## 8. 메인 API 초안

```text
capabilities:
  - register_run(feature_id, prd_ref)
  - on_event(event)
  - route(stage) -> worker_skill
  - enforce_gate(stage) -> pass | wait_human | fail
  - write_audit(run_id, event)
  - escalate(run_id, reason)
  - write_total_report(run_id)   # 단계 8
```

**하지 않는 것:** PRD 작성, 질문 답 추정, 검증 스킵 후 구현, QA 스킵 머지 승인.

---

## 9. MVP 로드맵

### Phase 0 — 문서

- [x] 레포 / README  
- [x] [`docs/deep-review.md`](docs/deep-review.md)  
- [x] 캐노니컬 파이프라인 1~8 고정 (본 문서)  
- [ ] `docs/gates.md` + 이벤트 스키마  
- [ ] SPEC 보일러플레이트 템플릿  

### Phase 1 — 수동 트리거

- [ ] REVIEW-PROMPT → Question Extractor skill  
- [ ] `questions.md` / `answers.md` 계약 + `답:` 공란 검사  
- [ ] 단계 5 PASS 6항 체크  
- [ ] Impl → QA 수동 연쇄  
- [ ] 메인 **토탈 보고** 템플릿  

### Phase 2 — 자동 트리거

- [ ] `prd.ready` → … → `qa.*` → `run.report.ready` 자동 라우팅  
- [ ] FAIL 시 이전 단계로 되돌림  
- [ ] 감사 로그  

### Phase 3 — 확장

- [ ] 미팅 녹음 → PRD 보조 (확정은 사람)  
- [ ] 이슈 트래커 연동  
- [ ] 다중 레포 Impl  

---

## 10. 성공 기준

1. PRD·질문 답 외 **빈 칸 추정 0**  
2. `spec.verify.passed` 없이 Impl 시작 불가  
3. 3→4→5가 **파일로 분리**되어 추적 가능  
4. QA 후 **토탈 보고**가 항상 남음  
5. `run_id`로 PRD→질문→스펙해시→PR→QA→보고 연결  
6. 실무 1건 E2E (예: CRM 지표)

---

## 11. 비목표

- PRD까지 AI가 단독 확정  
- 사람 답 없이 스펙 PASS  
- 배포/롤백 오케스트레이션  
- SDD 툴 자체를 다시 만들기 (우리는 **오케스트레이션**)

---

## 12. 리스크

| 리스크 | 대응 |
|--------|------|
| AI가 답 칸을 채움 | `answers_must_be_human`, CI로 `답:` 검사 |
| 3·5를 한 세션에 섞음 | 산출물 파일·이벤트 분리 강제 |
| 스펙 드리프트 | spec hash + invalidate |
| 프롬프트만 게이트 | fail-closed 스크립트/훅 |
| 보고 누락 | QA 종료 시 메인 `write_total_report` 필수 |

---

## 13. 다음 할 일

1. SPEC 보일러플레이트 (`docs/templates/`)  
2. `schemas/events.schema.json` (위 이벤트 표)  
3. Question Extractor ← REVIEW-PROMPT 바인딩  
4. 토탈 보고 템플릿 + 샘플 런 1건  
