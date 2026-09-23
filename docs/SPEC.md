# Deep Agent Orchestrator — SPEC

**문서 목적:** README 정책을 **서버에서 바로 구현할 수 있는 수준**으로 고정한다.  
**읽는 법:** 위에서 아래로 한 장씩. 구현은 §14 순서대로.  
**상태:** Draft v0.1 · 2026-09-22  
**근거:** [`README.md`](../README.md), [`docs/deep-review.md`](deep-review.md)  
**비사용:** LangGraph, LangChain deepagents, Temporal/Inngest (MVP 범위 밖)

---

## 0. 이 문서를 어떻게 읽으면 되나

| 장 | 내용 | 누구에게 |
|----|------|----------|
| §1 | 한 줄 진실 · 용어 | 전원 |
| §2 | **어디서 도는지** (서버) | 전원 — 여기부터 헷갈림이 풀림 |
| §3 | 시스템 경계 · 구성 요소 | 설계 |
| §4 | 디렉터리 · 모듈 맵 | 구현 시작 |
| §5 | Run 상태 · 파일 계약 | 구현 |
| §6 | 이벤트 | 구현 |
| §7 | HTTP API | 구현 |
| §8 | Main `tick` 알고리즘 | 핵심 |
| §9 | `spawn` · 모델 호출 | 핵심 |
| §10 | 스킬 5종 계약 | 스킬 담당 |
| §11 | 게이트 (코드) | 필수 |
| §12 | 사람 UI 최소 계약 | 프론트/운영 |
| §13 | 인수조건 · 비목표 | QA |
| §14 | 구현 순서 (스프린트) | 일정 |
| §15 | 열린 질문 (TBD) | 결정 대기 |

관련 파일:

- 학습·K8s·DB 플랜: [`PLAN.md`](PLAN.md)
- 게이트 상세: [`gates.md`](gates.md)
- 이벤트 JSON Schema: [`../schemas/events.schema.json`](../schemas/events.schema.json)
- Run 상태 Schema: [`../schemas/run-state.schema.json`](../schemas/run-state.schema.json)
- 가용 모델 목록 Schema: [`../schemas/models.available.schema.json`](../schemas/models.available.schema.json)
- SPEC 보일러: [`templates/spec-boilerplate.md`](templates/spec-boilerplate.md)

---

## 1. 한 줄 진실 · 용어

### 1.1 진실

사람은 **PRD**와 **질문 답**만 쓴다.  
**서버에 상주하는 Main 프로세스**가 매 턴 **스킬 + 모델**을 골라 서브 세션을 띄우고, SDD 게이트를 코드로 강제한다.  
역할→모델 고정표는 없다. LangGraph는 쓰지 않는다.

### 1.2 용어

| 용어 | 뜻 |
|------|-----|
| **Main** | 최상위 LLM + 그 LLM을 돌리는 서버 루프. 스펙/코드 본문 작성 금지 |
| **tick** | Main이 상태를 읽고 다음 행동 하나를 정하는 한 사이클 |
| **skill** | 작업 계약 ID (`spec-write`, `question-extract`, …). 모델이 아님 |
| **spawn** | Main이 `(skill, model, brief, …)`로 **새 모델 세션**을 실행하는 것 |
| **worker** | spawn으로 뜬 한 세션. 끝나면 이벤트만 발행 |
| **gate** | 코드 fail-closed 검사. 프롬프트만으로 PASS 불가 |
| **run** | 하나의 피처 오케스트레이션 단위. `run_id`로 식별 |
| **workspace** | `runs/{run_id}/` 디렉터리. 산출물·로그·상태 JSON |
| **event** | 단계 완료 신호. Main의 입력. 워커가 다음 워커를 부르지 않음 |

---

## 2. 어디서 도는가 (런타임)

### 2.1 결정 (고정)

| 항목 | 결정 |
|------|------|
| 실행 위치 | **항상 서버** (PC Cursor 세션 = 개발용일 뿐, 실운전 아님) |
| 언어 | **Python 3.11+** |
| 웹 프레임워크 | **FastAPI** (HTTP API + 백그라운드 워커 루프) |
| 오케스트레이션 엔진 | **자체 `tick` 루프** (아래 §8). **LangGraph 금지** |
| 상태 저장 | 디스크 `runs/{run_id}/state.json` + `audit.jsonl` (MVP). DB는 이후 |
| 모델 | OpenAI 호환 HTTP API (키 기반). OAuth는 후순위 |
| 대상 코드 | **별도 git 레포** (이 오케스트레이터가 clone/PR). MVP는 “로컬 path 지정”도 허용 |

### 2.2 프로세스 그림

```
┌─────────────────────────────────────────────┐
│  Server (Docker / VM)                        │
│                                              │
│  uvicorn app.main:app                        │
│       │                                      │
│       ├─ HTTP: /runs, /events, /human/…      │
│       ├─ Background: RunLoop.tick(run_id)    │
│       ├─ SpawnExecutor(skill, model, …)      │
│       ├─ GateEngine                          │
│       └─ runs/  (volume)                     │
│                                              │
│  env: OPENAI_API_KEY, MAIN_MODEL, …          │
└───────────────┬─────────────────────────────┘
                │ HTTPS
                ▼
         Model Provider API
                │
                ▼
         (optional) git remote / GH API
```

### 2.3 “파이프라인”의 실체

파이프라인은 다이어그램이 아니라 **서버 안 while/스케줄러가 `tick`을 반복 호출하는 것**이다.

- HTTP로 `prd.ready`가 들어오면 run이 `running`이 되고
- 루프가 Main을 불러 spawn하고
- `questions.ready`면 `waiting_human`으로 멈추고
- `answers.ready`면 다시 tick

---

## 3. 시스템 경계

### 3.1 In scope (이 레포가 만든다)

- Run 생성 · tick · spawn · gate · audit · total report
- 스킬 프롬프트/툴 바인딩
- 사람 답변 수신 API (최소)
- 가용 모델 목록 노출

### 3.2 Out of scope (이 레포가 안 만든다)

- PRD 작성 UI의 고급 제품화 (MVP는 md 업로드/텍스트 POST면 충분)
- 대상 서비스의 비즈니스 기능 자체
- 배포/롤백 오케스트레이션
- SDD 툴(OpenSpec 등) 재구현 — **호출만** (MVP는 프롬프트만으로 질문 추출 허용)
- LangGraph / deepagents 패키지

### 3.3 액터

| 액터 | 할 수 있는 것 |
|------|----------------|
| Human | PRD 제출, 질문 답, (운영) API 키·모델 목록 설정 |
| Main | tick 판단, spawn, wait_human, finish, report |
| Worker | 자기 스킬 산출물 + 완료 이벤트 |
| GateEngine | spawn 전/후 거부·통과 (모델 선택과 무관) |

---

## 4. 디렉터리 · 모듈 맵

### 4.1 레포 레이아웃 (구현 목표)

```
/
├── README.md
├── docs/
│   ├── SPEC.md                 ← 본 문서
│   ├── deep-review.md
│   ├── gates.md
│   └── templates/
│       └── spec-boilerplate.md
├── schemas/
│   ├── events.schema.json
│   ├── run-state.schema.json
│   └── models.available.schema.json
├── skills/
│   ├── spec-write/
│   │   ├── SKILL.md            # 시스템 프롬프트·입출력 계약
│   │   └── … 
│   ├── question-extract/
│   ├── spec-rereview/
│   ├── implement/
│   └── qa/
├── src/
│   └── deep_agent/
│       ├── __init__.py
│       ├── app.py              # FastAPI
│       ├── config.py
│       ├── models_catalog.py   # list_models()
│       ├── run_store.py        # runs/ FS
│       ├── events.py
│       ├── gates.py
│       ├── main_agent.py       # Main LLM + tools
│       ├── tick.py             # RunLoop
│       ├── spawn.py            # SpawnExecutor
│       ├── llm_client.py       # provider HTTP
│       ├── report.py
│       └── skills_loader.py
├── runs/                       # gitignore. 런타임 데이터
├── tests/
├── pyproject.toml / requirements.txt
└── Dockerfile
```

### 4.2 모듈 책임 (한 줄씩)

| 모듈 | 책임 |
|------|------|
| `app.py` | HTTP 라우트. 비즈니스는 여기 넣지 않음 |
| `tick.py` | run 상태 보고 Main 호출 → 액션 실행 |
| `main_agent.py` | Main 프롬프트 + tool schema. **파일 write 툴 없음** |
| `spawn.py` | skill 로드 → worker LLM 루프 → 산출물 경로 검증 → 이벤트 |
| `gates.py` | fail-closed. spawn 허용 여부 |
| `run_store.py` | state/artifacts/audit 읽기쓰기 |
| `llm_client.py` | `chat(model, messages, tools)` 단일 인터페이스 |

---

## 5. Run 상태 · 파일 계약

### 5.1 `runs/{run_id}/` 구조

```
runs/{run_id}/
├── state.json          # RunState (§5.2, schema)
├── todo.json           # Main Todo 목록
├── audit.jsonl         # 한 줄 한 이벤트/스폰
├── artifacts/
│   ├── prd.md
│   ├── spec.md
│   ├── questions.md
│   ├── answers.md      # 또는 questions.md에 답 기입
│   ├── qa-report.md
│   └── report.md
├── meta/
│   ├── spec.sha256
│   └── verify-token     # PASS 시에만 존재
└── workers/
    └── {spawn_id}/
        ├── request.json
        ├── summary.md      # Main에 반환되는 요약만
        └── raw/            # 선택: worker 로그
```

### 5.2 RunState (요약 — 전문은 schema)

필수 필드:

| 필드 | 타입 | 설명 |
|------|------|------|
| `run_id` | string | `run_YYYYMMDD_HHMMSS_<feature>` |
| `feature_id` | string | |
| `status` | enum | `created` \| `running` \| `waiting_human` \| `failed` \| `completed` |
| `phase` | string | 논리 단계 힌트 (Main 참고용, 게이트 아님) |
| `spec_hash` | string\|null | verified 이후 |
| `verify_passed` | bool | 게이트 진실 소스 |
| `last_event` | string\|null | |
| `spawn_history` | array | `{spawn_id, skill, model, brief, at, result}` |
| `waiting_for` | string\|null | 예: `answers` |
| `target_repo_path` | string\|null | implement용 |
| `budget` | object | `{max_usd?: number, spent_usd?: number}` |
| `error` | string\|null | |

### 5.3 산출물 불변 규칙

1. Worker는 **자기 스킬이 정한 경로만** 쓴다.  
2. Main은 artifacts에 **직접 write하지 않는다**.  
3. `answers.md`의 `답:` 라인은 **사람 API/업로드로만** 채워진다.  
4. `spec.md`가 바뀌면 `spec_hash` 재계산 + `verify_passed=false` + implement/qa invalidate.

---

## 6. 이벤트

### 6.1 이벤트 목록

| event | 발행자 | 효과 (Main 전형 동작) |
|-------|--------|------------------------|
| `prd.ready` | human | status=running → tick → spawn `spec-write` |
| `spec.draft.ready` | spec-write | spawn `question-extract` |
| `questions.ready` | question-extract | `waiting_human`, waiting_for=answers |
| `answers.ready` | human | spawn `spec-rereview` |
| `spec.verify.passed` | gate (rereview 후) | spawn `implement` 허용 |
| `spec.verify.failed` | gate / rereview | Main 재계획 또는 failed |
| `impl.pr.ready` | implement | spawn `qa` (새 세션) |
| `qa.passed` | qa | total report → completed |
| `qa.failed` | qa | Main 재계획 (impl 또는 docs 또는 human) |
| `run.report.ready` | Main/report | 종료 신호 |

### 6.2 공통 페이로드

모든 이벤트:

```json
{
  "event": "questions.ready",
  "run_id": "run_20260922_crm_metrics",
  "feature_id": "crm-metrics",
  "ts": "2026-09-22T08:00:00Z",
  "actor": "question-extract",
  "spec_ref": "artifacts/spec.md@sha256:…",
  "artifacts": {
    "questions": "artifacts/questions.md"
  },
  "spawn_id": "sp_01H…"
}
```

JSON Schema: `schemas/events.schema.json`.

### 6.3 규칙

- Worker는 **다른 skill을 호출하지 않는다.** 이벤트 append만.  
- `on_event`는 state 갱신 후 **tick 스케줄** (또는 즉시 tick).  
- 동일 이벤트 중복은 idempotent 하게 무시 (같은 `spawn_id` 재처리 방지).

---

## 7. HTTP API (MVP)

Base: `/api/v1`

| Method | Path | 설명 |
|--------|------|------|
| `POST` | `/runs` | body: `{feature_id, prd_markdown, target_repo_path?}` → `run_id` |
| `GET` | `/runs/{run_id}` | state.json |
| `GET` | `/runs/{run_id}/artifacts/{name}` | 파일 |
| `POST` | `/runs/{run_id}/events` | 외부/테스트용 이벤트 주입 |
| `POST` | `/runs/{run_id}/answers` | `{answers_markdown}` → `answers.ready` |
| `POST` | `/runs/{run_id}/tick` | 수동 tick (디버그). 서버 루프와 동일 코드 경로 |
| `GET` | `/models` | 가용 모델 목록 |
| `GET` | `/runs/{run_id}/report` | report.md (있을 때) |
| `GET` | `/health` | liveness |

인증 MVP: `Authorization: Bearer <ORCH_API_TOKEN>` 단일 토큰.

---

## 8. Main `tick` 알고리즘

### 8.1 의사코드

```text
function tick(run_id):
  state = load(run_id)
  if state.status in {completed, failed}:
    return
  if state.status == waiting_human:
    return   # answers.ready 올 때까지 대기

  enforce_invariants(state)   # 예: answers에 AI 추정 흔적 있으면 fail

  context = build_main_context(state)  # state 요약 + todo + last errors + list_models()
  action = main_llm.decide(context)    # tool call 1개 권장

  switch action.type:
    case update_todo:
      save_todo(action.items)
    case spawn:
      gate = enforce_gate(state, action.skill)
      if gate == reject:
        audit(gate_reject); return  # 또는 Main에 에러 넣어 재tick
      result = spawn(action.skill, action.model, action.brief, …)
      append_spawn_history(…)
      apply_worker_event(result.event)
    case wait_human:
      state.status = waiting_human
      state.waiting_for = action.reason
    case finish:
      write_total_report(run_id)
      state.status = completed
      emit run.report.ready
    case fail:
      state.status = failed
      state.error = action.reason

  save(state)
```

### 8.2 Main 시스템 프롬프트 강제 조항

Main에게 **반드시** 알릴 것:

1. 너는 스펙/코드를 쓰지 않는다. spawn만 한다.  
2. `model`은 매 spawn 필수. 고정 표 없다. `list_models()`만 본다.  
3. `verify_passed`가 false면 `implement` / `qa`를 고르지 마라 — 골라도 코드가 거부한다.  
4. `questions.ready` 이후에는 `wait_human`이 정상이다.  
5. QA는 implement와 **다른 spawn_id/세션**.  
6. 실패 시 같은 스킬·다른 모델 또는 다른 스킬로 재계획하고 audit에 이유를 남겨라.

### 8.3 Main 툴 스키마 (구현 고정)

```json
{
  "tools": [
    {
      "name": "update_todo",
      "parameters": { "items": [{"id": "string", "text": "string", "status": "pending|doing|done"}] }
    },
    {
      "name": "spawn",
      "parameters": {
        "skill": "spec-write|question-extract|spec-rereview|implement|qa",
        "model": "string",
        "brief": "string",
        "budget_usd": "number?"
      }
    },
    {
      "name": "wait_human",
      "parameters": { "reason": "string", "waiting_for": "answers|prd_revision" }
    },
    {
      "name": "finish",
      "parameters": { "summary": "string" }
    },
    {
      "name": "fail_run",
      "parameters": { "reason": "string" }
    }
  ]
}
```

Main에 **없는** 툴: `write_file`, `apply_patch`, `git_commit` (worker only).

### 8.4 서버 루프

- MVP: asyncio 백그라운드 태스크가 `status==running`인 run에 대해 주기적 `tick` (예: 2s) 또는 spawn 완료 콜백 즉시 tick.  
- `waiting_human`은 폴링하지 않음. `POST .../answers`가 status=running으로 바꾼 뒤 tick.

---

## 9. `spawn` · 모델 호출

### 9.1 SpawnRequest

| 필드 | 필수 | 설명 |
|------|------|------|
| `spawn_id` | yes | ulid/uuid |
| `skill` | yes | |
| `model` | yes | `list_models()`에 있어야 함 |
| `brief` | yes | Main이 worker에게 주는 지시 |
| `run_id` | yes | |
| `tools` | no | skill 기본 툴 ∪ 요청 |

### 9.2 실행 절차

1. `gates.allow_spawn(state, skill)` — 실패 시 예외/`GateReject`  
2. `skills_loader.load(skill)` → system prompt + 입력 파일 목록  
3. Worker messages 구성: system + brief + 필요 artifacts 텍스트/경로  
4. `llm_client.chat(model=…)` — tool loop (skill이 허용한 툴만)  
5. 출력 파일 존재·형식 검증 (skill contract)  
6. `summary.md` 작성 (Main 컨텍스트용, 본문 전체가 아님)  
7. 완료 이벤트 emit + audit.jsonl append  

### 9.3 세션 분리

- 매 spawn = 새 message history.  
- `qa` spawn은 implement의 conversation을 **절대 이어받지 않음**.  
- 같은 `model` id여도 세션은 분리.

### 9.4 모델 카탈로그

`config/models.available.json` 또는 env로 로드:

```json
{
  "models": [
    { "id": "gpt-4.1", "provider": "openai", "currency": "usd", "input_per_1m": 0, "notes": "" }
  ]
}
```

역할 매핑 필드 **금지** (`default_for_skill` 넣지 말 것).

### 9.5 LLM 클라이언트

```text
chat(model: str, messages: list, tools: list | None, max_tokens: int) -> ChatResult
```

- Provider는 `model` id prefix 또는 catalog의 `provider`로 분기.  
- MVP: OpenAI 하나만 구현해도 SPEC 충족.  
- 에러 시 spawn failed 이벤트/감사를 남기고 Main이 재계획.

---

## 10. 스킬 5종 계약

각 스킬 디렉터리의 `SKILL.md`가 진실. 여기 요약.

### 10.1 `spec-write`

| | |
|--|--|
| 입력 | `artifacts/prd.md`, `docs/templates/spec-boilerplate.md` |
| 출력 | `artifacts/spec.md` (draft) |
| 이벤트 | `spec.draft.ready` |
| 금지 | TBD/빈칸을 추정으로 채움. 모르는 것은 `TBD` 또는 질문 후보만 |

### 10.2 `question-extract`

| | |
|--|--|
| 입력 | `artifacts/spec.md`, REVIEW-PROMPT (스킬 내) |
| 출력 | `artifacts/questions.md` — 각 항목에 `답:` **공란** |
| 이벤트 | `questions.ready` |
| 금지 | `답:` 채우기 |
| 질문 규칙 | “답이 없으면 AI가 무엇을 잘못 만드는가” 한 줄 + 선택지+추천 |

구멍 taxonomy: 인수조건 판별/산식/출처 없음, `또는`/`~라면` 분기 없음, 절 간 모순, 표시≠산출, 화면≠데이터 출처.

### 10.3 `spec-rereview`

| | |
|--|--|
| 입력 | `spec.md`, `answers.md` (또는 답이 채워진 questions) |
| 출력 | 갱신 `spec.md` |
| 이후 | **코드** `gates.verify_spec()` 실행 → passed/failed 이벤트 |
| 금지 | 사람 답 무시하고 추정 보강 |

### 10.4 `implement`

| | |
|--|--|
| 입력 | **verified** `spec.md` + hash + `target_repo_path` |
| 출력 | 브랜치/PR URL을 `workers/.../summary.md`와 state에 기록 |
| 이벤트 | `impl.pr.ready` |
| 게이트 | `verify_passed==true` 필수 |
| 금지 | 스펙 밖 기능, 스택 교체, 구멍 조용히 메움 → escalate |

MVP 구현 수준:  
- v0: 패치 diff를 `artifacts/impl.patch`로만 남겨도 됨 (PR 자동화는 v1).  
- SPEC상 이벤트 이름 `impl.pr.ready` 유지 (patch-only여도 동일 이벤트, payload에 `pr_url` optional).

### 10.5 `qa`

| | |
|--|--|
| 입력 | verified spec 인수조건 ID + impl 산출물 |
| 출력 | `artifacts/qa-report.md` (조항→증거 매핑) |
| 이벤트 | `qa.passed` \| `qa.failed` |
| 필수 | implement와 **다른 spawn_id** |

---

## 11. 게이트 (코드) — 요약

상세·의사코드: [`gates.md`](gates.md).

| Gate ID | 언제 | 실패 시 |
|---------|------|---------|
| `G_ANSWERS_HUMAN` | answers.ready 수신 | 이벤트 거부 |
| `G_NO_IMPL_WITHOUT_VERIFY` | spawn implement/qa | GateReject |
| `G_VERIFY_SIX` | rereview 후 | `spec.verify.failed` |
| `G_SESSION_QA` | spawn qa | implement와 동일 spawn 이어받기 시도 시 거부 |
| `G_MODEL_KNOWN` | 모든 spawn | catalog에 없으면 거부 |
| `G_BUDGET` | spawn 전 (optional) | reject |

**원칙:** 프롬프트가 “PASS라고 함” ≠ PASS. `verify_passed`는 `gates.verify_spec()`만 set.

Verify 6항 (모두 필요):

1. 인수조건마다 판별/계산/출처  
2. 조건문·`또는` 분기  
3. 절 간 모순 0  
4. 추정 답 0  
5. 잔여 차단 질문 0 (또는 재질문 루프 명시)  
6. 승인 토큰 파일 + spec hash

MVP에서 1~5는 **휴리스틱 스크립트 + 체크리스트 파일**로 시작 가능. 완전 NLP 판별은 요구하지 않음. 다만 **6번(토큰+hash)과 answers 공란 검사**는 반드시 코드.

---

## 12. 사람 UI 최소 계약

제품 UI가 없어도 됨. 다음이면 SPEC 충족:

1. `POST /runs`에 PRD 마크다운  
2. `questions.ready` 후 `GET .../artifacts/questions.md`  
3. 사람이 답 채운 md를 `POST .../answers`  
4. `GET .../runs/{id}`로 status 폴링  

나중에 웹/Issue는 이 API 위 어댑터.

---

## 13. 인수조건 · 비목표

### 13.1 이 오케스트레이터 자체의 인수조건

1. 서버 프로세스만으로 run을 create → (사람 답 1회) → report까지 진행 가능  
2. `verify_passed` false일 때 implement spawn이 **HTTP/도메인 에러로 거부**  
3. questions의 `답:`을 worker가 채우면 `G_ANSWERS_HUMAN` 실패  
4. audit.jsonl에 모든 spawn의 skill+model+brief 기록  
5. LangGraph / langgraph / deepagents import **없음** (CI grep)  
6. 역할→모델 매핑 설정 파일 **없음**  
7. Main 프로세스에 스펙/코드 write 툴 **없음**

### 13.2 비목표

- PRD AI 단독 확정  
- 사람 답 없이 spec PASS  
- LangGraph 도입  
- 모델 고정 배치표  
- 배포 오케스트레이션  

---

## 14. 구현 순서 (공부·개발 같이)

| Step | 산출 | 완료 기준 |
|------|------|-----------|
| **S0** | 본 SPEC + schemas + gates.md + boilerplate | 문서 리뷰 |
| **S1** | FastAPI skeleton + `runs/` store + `/health` `/runs` | create run → state.json |
| **S2** | `llm_client` + `/models` + fake Main (규칙 기반 tick) | prd.ready → 파일 복사 수준 파이프 |
| **S3** | `gates` answers/verify stub | 거부 테스트 green |
| **S4** | skills `spec-write` / `question-extract` + 실제 Main LLM | questions.md 생성 |
| **S5** | human answers API + `spec-rereview` + verify token | verify_passed |
| **S6** | `implement` (patch 파일) + `qa` + report | E2E 1건 |
| **S7** | Dockerfile + volume + env 문서 | 서버 배포 |

각 Step마다 `tests/`에 게이트·이벤트 단위 테스트 추가.

---

## 15. 열린 질문 (구현 전 결정하면 좋음)

아래는 **추정으로 닫지 않음**. 결정되면 본 SPEC에 반영.

| ID | 질문 | 기본 제안 (비구속) |
|----|------|-------------------|
| Q1 | Main용 모델 id는 env 고정인가, catalog 중 Main이 고르나? | env `MAIN_MODEL` 고정 권장 (비용 통제) |
| Q2 | implement MVP가 patch 파일인가 GitHub PR인가? | S6는 patch, S7+ PR |
| Q3 | 멀티테넌시? | MVP 단일 토큰·단일 디스크 |
| Q4 | 모델 과금 추적은 provider usage 필드? | audit에 usage 있으면 기록 |

---

## 16. README와의 관계

- 정책·비전: README  
- **구현 계약:** 본 SPEC이 우선  
- 충돌 시: SPEC을 고치고 README를 맞춤  
- LangGraph 언급이 README에 남아 있으면 **폐기** (서버 + FastAPI + 자체 tick)

---

## 변경 이력

| 버전 | 날짜 | 내용 |
|------|------|------|
| 0.1 | 2026-09-22 | 초안. 서버·Python·FastAPI·No LangGraph. README 기반 구현 SPEC |
