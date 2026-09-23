# Deep Agent Orchestrator — SPEC

**문서 목적:** README 정책을 **바로 구현할 수 있는 수준**으로 고정한다.
**읽는 법:** 위에서 아래로 한 장씩. 구현은 §14 순서대로.
**상태:** Draft v0.4 · 2026-09-23
**근거:** [`README.md`](../README.md), [`concepts.md`](concepts.md), [`deep-review.md`](deep-review.md)
**엔진:** **LangGraph 사용.** `deepagents` 패키지는 미사용 (§2.2)

> **v0.2 → v0.3 변경 요약**
> **Store(장기기억) 범위 편입** — Checkpoint와 분리된 `BaseStore` (§5.4, §12.4)
> **pgvector 채택** — 보류 철회. 과거 FAIL 회수·판례 검색 (§12.3)
> **openspec / spec-kit을 CLI로 호출** (Q4 결정) — scaffold·validate는 CLI, 구멍찾기는 LLM (§10.2)
> `G_OPENSPEC_VALID` 게이트 신설 — `openspec validate --strict --json`
>
> **v0.1 → v0.2 변경 요약**
> 자체 `tick` 루프 → **LangGraph 그래프 루프**
> `runs/{run_id}/state.json` → **AgentState + checkpointer (thread 단위)**
> `run_id` → **`thread_id`** (run_id는 SDD 메타로 잔존)
> 파이프라인 8단계 → **SDD 0~9** (자료수집 · ready 권한검수 추가)
> 스킬 5종 → **8종** (openspec / spec-kit 분리, answer-triage, ready-audit 추가)

---

## 0. 이 문서를 어떻게 읽으면 되나

| 장 | 내용 | 누구에게 |
|----|------|----------|
| §1 | 한 줄 진실 · 용어 | 전원 |
| §2 | **어디서 무엇 위에서 도는지** | 전원 — 여기부터 헷갈림이 풀림 |
| §3 | 시스템 경계 | 설계 |
| §4 | 디렉터리 · 모듈 맵 | 구현 시작 |
| §5 | **State 계약** | 핵심 |
| §6 | 이벤트 | 구현 |
| §7 | HTTP API | 구현 |
| §8 | **그래프 · 에이전트 루프** | 핵심 |
| §9 | **`spawn` — 서브에이전트 격리** | 핵심 |
| §10 | 스킬 9종 계약 | 스킬 담당 |
| §11 | 게이트 | 필수 |
| §12 | 압축 · 영속화 | Phase 3 |
| §13 | 인수조건 · 비목표 | QA |
| §14 | 구현 순서 | 일정 |
| §15 | 열린 질문 | 결정 대기 |

관련 파일:

- 개념 사전: [`concepts.md`](concepts.md)
- 학습·인프라 플랜: [`PLAN.md`](PLAN.md)
- 게이트 상세: [`gates.md`](gates.md)
- 이벤트 Schema: [`../schemas/events.schema.json`](../schemas/events.schema.json)
- SDD 상태 Schema: [`../schemas/run-state.schema.json`](../schemas/run-state.schema.json)
- 모델 카탈로그 Schema: [`../schemas/models.available.schema.json`](../schemas/models.available.schema.json)
- SPEC 보일러플레이트: [`templates/spec-boilerplate.md`](templates/spec-boilerplate.md)

---

## 1. 한 줄 진실 · 용어

### 1.1 진실

사람은 **자료(PRD·원본문서)** 와 **질문 답**만 쓴다.
**LangGraph 그래프 안의 Main 에이전트**가 매 턴 **스킬 + 모델**을 골라
**격리된 서브 세션**을 띄우고, SDD 게이트를 코드로 강제한다.
역할→모델 고정표는 없다.

### 1.2 용어

| 용어 | 뜻 |
|------|-----|
| **Main** | 최상위 LLM. 그래프의 `main_agent` 노드. 스펙/코드 본문 작성 금지 |
| **AgentState** | 그래프가 들고 다니는 TypedDict (§5) |
| **thread_id** | 하나의 지속 실행 단위 식별자. 체크포인트의 키 |
| **checkpoint** | 그 thread가 어디까지 실행됐는지의 스냅샷 |
| **skill** | 작업 계약 ID (`spec-write`, `openspec`, …). **모델이 아니다** |
| **spawn** | Main이 `(skill, model, brief)`로 **격리된 서브 그래프**를 실행하는 **툴** |
| **subagent** | spawn으로 뜬 한 세션. 끝나면 **요약 문자열만** 반환 |
| **gate** | 코드 fail-closed 검사. 프롬프트만으로 PASS 불가 |
| **run** | 하나의 피처 오케스트레이션 단위. `run_id` = SDD 메타 키 |
| **표기 4종** | `[코드]` / `[답변]` / `[스펙 결정]` / 무표시 — 권한 등급 |
| **ready** | 단계 6 권한 검수. 「채워졌는데 근거 없는 것」을 찾는다 |

> **`thread_id` vs `run_id`** — 하나의 run은 하나의 thread로 시작한다.
> 실행/재개의 키는 `thread_id`, SDD 산출물·감사의 키는 `run_id`다.
> MVP에서는 `thread_id == run_id`로 두어도 된다. (§15 Q3)

---

## 2. 어디서 · 무엇 위에서 도는가

### 2.1 결정 (고정)

| 항목 | 결정 |
|------|------|
| 실행 위치 | **서버** (PC 세션은 개발용) |
| 언어 | **Python 3.11+** |
| 실행 엔진 | **LangGraph** — State / Node / Edge / Checkpointer |
| 웹 | **FastAPI** (HTTP API) |
| 단기 상태 (Checkpoint) | Phase 1~2: `InMemorySaver` → Phase 3: **`PostgresSaver`** (thread 단위) |
| **장기 기억 (Store)** | **`PostgresStore` + pgvector** — thread를 넘어 재사용 (§5.4, §12.4) |
| **임베딩** | **`text-embedding-3-small`** (1536d) — pgvector 인덱스 한계와 맞음 (§12.3) |
| 파일 | **하이브리드** — 문서는 State `files`, 코드는 실제 디스크 (§5.3) |
| 모델 | **OpenAI API** (키 기반). Main = env `MAIN_MODEL` 고정, 서브 = Main이 선택 |
| SDD 툴 | **openspec / spec-kit CLI** — scaffold·validate (§10.2) |
| 대상 코드 | 별도 git 레포 (clone 또는 로컬 path) |
| E2E | **Playwright** (단계 8) |
| 외부 노출 | **cloudflared** → 추후 AWS/GCP |

### 2.2 LangGraph는 쓰고 `deepagents`는 안 쓰는 이유

| | 정체 | 결정 |
|--|------|------|
| **LangGraph** | 실행 프레임워크 | **사용** — checkpointer · interrupt · thread 재개를 직접 짤 이유가 없다 |
| **`deepagents`** | 완성된 Deep Agent 하네스 (planning·subagent·FS 툴이 이미 구현됨) | **미사용** — 배우려는 네 기둥이 블랙박스가 된다. 소스는 레퍼런스로만 읽는다 |

**이식성 조건 (하드 제약):**
`gates.py` 와 `skills/` 로직은 **LangGraph 타입을 import하지 않는다.**
순수 함수로 짜고 노드·툴에서 호출만 한다.

### 2.3 프로세스 그림

```
┌──────────────────────────────────────────────┐
│  Server (Docker / K8s)                        │
│                                               │
│  uvicorn deep_agent.app:app                   │
│       │                                       │
│       ├─ HTTP: /threads /events /human/…      │
│       ├─ graph.ainvoke(state, config)         │
│       │     config = {"configurable":         │
│       │               {"thread_id": …}}       │
│       ├─ spawn 툴 → 격리 서브그래프           │
│       └─ gates (순수 함수)                    │
│                                               │
│  env: OPENAI_API_KEY, MAIN_MODEL, DATABASE_URL│
└────────────┬─────────────────────┬───────────┘
             │ HTTPS               │ 5432
             ▼                     ▼
      Model Provider API    PostgreSQL (checkpointer)
             │
             ▼
   (선택) git remote / 대상 레포
```

### 2.4 "파이프라인"의 실체

파이프라인은 다이어그램이 아니라 **그래프가 `main_agent ⇄ tools`를 반복하는 것**이다.

- `POST /threads`로 자료가 들어오면 그래프가 시작되고
- Main이 `spawn` 툴을 호출해 서브를 띄우고
- 사람 답이 필요하면 **`interrupt()`로 멈추고 체크포인트에 저장**되고
- `POST /threads/{id}/answers`가 **`Command(resume=…)`** 로 재개한다

**중요:** `waiting_human` 동안 **프로세스가 떠 있을 필요가 없다.**
상태는 checkpointer에 있다.

---

## 3. 시스템 경계

### 3.1 In scope

- 그래프 · 툴 · 서브에이전트 격리 · 게이트 · 감사 · 토탈 보고
- 스킬 프롬프트/툴 바인딩 (9종)
- 사람 답변 수신 + resume
- 가용 모델 목록 노출
- 체크포인트 영속화 (thread 단위)

### 3.2 Out of scope

- PRD 작성 UI 제품화 (MVP는 md POST)
- 대상 서비스의 비즈니스 기능
- 배포/롤백 오케스트레이션
- SDD 툴(OpenSpec·Spec Kit) 재구현 — **프롬프트 바인딩 또는 CLI 호출만**
- `deepagents` 패키지

### 3.3 액터

| 액터 | 할 수 있는 것 |
|------|----------------|
| Human | 자료 제출, 질문 답, (운영) 모델 카탈로그 설정 |
| Main | todos, spawn, wait_human, finish, fail |
| Subagent | 자기 스킬 산출물 + **요약 반환** |
| Gates | spawn 전/이벤트 수신 시 거부 (모델 선택과 무관) |

---

## 4. 디렉터리 · 모듈 맵

### 4.1 레포 레이아웃

```
/
├── README.md
├── .gitignore
├── docs/
│   ├── SPEC.md                  ← 본 문서
│   ├── concepts.md              ← 개념 사전 (LangGraph 용어)
│   ├── PLAN.md
│   ├── deep-review.md
│   ├── gates.md
│   └── templates/
│       └── spec-boilerplate.md
├── schemas/
│   ├── events.schema.json
│   ├── run-state.schema.json
│   └── models.available.schema.json
├── config/
│   └── models.available.json    ← 카탈로그 실데이터 (schema가 검증)
├── skills/
│   ├── spec-write/SKILL.md
│   ├── openspec/SKILL.md
│   ├── spec-kit/SKILL.md
│   ├── answer-triage/SKILL.md
│   ├── spec-rereview/SKILL.md
│   ├── ready-audit/SKILL.md
│   ├── implement/SKILL.md
│   └── qa/SKILL.md
├── src/
│   └── deep_agent/
│       ├── __init__.py
│       ├── app.py               # FastAPI
│       ├── config.py
│       ├── state.py             # AgentState TypedDict
│       ├── graph.py             # build_graph()
│       ├── nodes/
│       │   └── main_agent.py    # Main LLM 노드
│       ├── tools/
│       │   ├── todos.py         # write_todos
│       │   ├── spawn.py         # ★ 격리 서브그래프
│       │   ├── fs.py            # read_file / write_file / ls (State files)
│       │   └── human.py         # wait_human → interrupt()
│       ├── subagent.py          # 서브 그래프 빌더
│       ├── skills_loader.py
│       ├── sdd_cli.py           # openspec / specify 서브프로세스 래퍼
│       ├── gates.py             # ★ 순수 함수 (LangGraph import 금지)
│       ├── models_catalog.py
│       ├── llm.py
│       ├── checkpoint.py        # InMemorySaver | PostgresSaver  (단기)
│       ├── store.py             # PostgresStore + pgvector        (장기)
│       ├── memory.py            # 회수·기록 정책 (무엇을 기억할지)
│       ├── compaction.py        # 컨텍스트 압축
│       ├── observability.py     # 토큰·비용·툴콜 기록
│       └── report.py
├── workspace/                   # gitignore. 실제 디스크 작업공간
├── tests/
├── pyproject.toml
└── Dockerfile
```

### 4.2 모듈 책임

| 모듈 | 책임 | 금지 |
|------|------|------|
| `app.py` | HTTP 라우트 + graph 호출 | 비즈니스 로직 |
| `state.py` | AgentState 정의 | |
| `graph.py` | 노드·엣지 배선, compile(checkpointer) | 단계별 노드 추가 |
| `nodes/main_agent.py` | 컨텍스트 조립 + LLM 호출 | 파일 write |
| `tools/spawn.py` | **격리 서브그래프 실행 + 요약 반환** | 부모 messages 전달 |
| `sdd_cli.py` | openspec/specify 서브프로세스 + JSON 파싱 | 셸 임의 실행 (allowlist만) |
| `gates.py` | fail-closed 판정 | **LangGraph import** |
| `checkpoint.py` | saver 팩토리 (단기) | Store와 혼용 |
| `store.py` | `BaseStore` 구현 + 임베딩 (장기) | Checkpoint와 혼용 |
| `memory.py` | **무엇을 기억하고 무엇을 안 기억할지** | run 고유 결정을 일반 지식으로 승격 |
| `compaction.py` | messages 압축 | todos·spec_hash 삭제 |

---

## 5. State 계약

### 5.1 AgentState

```python
from typing import TypedDict, Annotated, Literal
from langgraph.graph.message import add_messages

class AgentState(TypedDict):
    # 대화
    messages: Annotated[list, add_messages]
    thread_id: str
    user_id: str

    # 딥에이전트 핵심
    todos: list             # [{id, text, status}]      ← Planning
    files: dict             # {path: content}           ← Filesystem

    # 실행 제어
    status: Literal["running", "waiting_human", "done", "error"]
    next_action: str | None

    # SDD (§5.2)
    sdd: dict

    # 구현·검증 루프 (§5.5)
    implementation: dict          # ImplState
    e2e: dict                     # E2EState
    analysis: dict | None         # FailureAnalysis — 마지막 실패 진단
    iteration: int                # impl_bug 재시도 횟수만 센다
    max_iterations: int           # 기본 3
```

- **리듀서를 직접 구현하지 않는다.** `add_messages` 기본 제공분을 쓴다.
- `todos` / `files`는 기본 덮어쓰기 리듀서로 충분하다 (Main만 쓰므로 경합 없음).
- **`messages`를 수동으로 자르지 않는다.** 압축은 `compaction.py`가 담당 (§12.1).

### 5.2 `sdd` 서브 딕셔너리

| 필드 | 타입 | 설명 |
|------|------|------|
| `run_id` | string | `run_YYYYMMDD_HHMMSS_<feature>` |
| `feature_id` | string | |
| `stage` | string\|null | 0~9 힌트. **게이트가 신뢰하지 않음** |
| `spec_hash` | string\|null | verified 이후 |
| `verify_passed` | bool | **게이트 진실 소스** |
| `ready_open_count` | int\|null | ready 1절 남은 문제 수. **0이어야 구현 가능** |
| `last_event` | string\|null | |
| `spawn_history` | array | `{spawn_id, skill, model, brief, at, result, tokens, cost_usd}` |
| `waiting_for` | string\|null | `answers` \| `sources` |
| `target_repo_path` | string\|null | implement 대상 |
| `budget` | object | `{max_usd?, spent_usd}` |
| `gate_rejects` | array | 최근 거부 (Main 컨텍스트에 주입) |
| `error` | string\|null | |

스키마 전문: [`../schemas/run-state.schema.json`](../schemas/run-state.schema.json)

### 5.3 Filesystem — 하이브리드 (하드 제약)

| 대상 | 위치 | 접근 |
|------|------|------|
| `spec.md` `questions.md` `answers.md` `ready.md` `qa-report.md` `report.md` | **State `files`** | `tools/fs.py` |
| `sources/*` (PRD·원본·메일) | State `files` | 읽기 전용 |
| **대상 레포 코드 · 빌드 산출물 · Playwright 결과** | **실제 디스크** `workspace/{run_id}/` 또는 `target_repo_path` | subagent의 shell/파일 툴 |

**규칙**

1. **코드를 State `files`에 넣지 않는다.** 체크포인트가 매 스텝 비대해진다.
2. State `files`의 한 파일은 **256KB 이하**. 초과 시 디스크로 내린다.
3. Main은 `files`에 **직접 write하지 않는다** (write 툴을 안 준다).
4. `answers.md`는 **사람 API로만** 채워진다.
5. `spec.md`가 바뀌면 `spec_hash` 재계산 + `verify_passed=false` + 하위 invalidate.

### 5.4 Store — 장기 기억 (v0.3 신규)

**Checkpoint와 완전히 다른 것이다.** 섞으면 안 된다.

| | Checkpoint | **Store** |
|--|-----------|----------|
| 질문 | "이 thread가 어디까지 했나?" | "과거에 무엇을 배웠나?" |
| 범위 | 한 thread | **thread를 넘어 재사용** |
| 키 | `thread_id` | `(namespace, key)` |
| 소유 | LangGraph `PostgresSaver` (스키마 자동) | **우리가 설계** (`store.py`) |
| 조회 | 정확 복구 | **의미 유사 검색 (pgvector)** |

#### 5.4.1 네임스페이스

```text
("org", <org_id>, "glossary")        도메인 용어·약어
("org", <org_id>, "conventions")     스택·컨벤션·금지사항
("org", <org_id>, "verdicts")        ready-audit 판례 (권한 등급 판정 이력)
("org", <org_id>, "failures")        과거 FAIL — 원인·해결
("user", <user_id>, "preferences")   보고 형식 등
```

#### 5.4.2 레코드

```json
{
  "key": "mem_01H…",
  "value": {
    "kind": "failure | verdict | convention | glossary | preference",
    "text": "회수 대상 본문 (임베딩 원천)",
    "source_run_id": "run_…",
    "evidence": "artifacts 경로 또는 인용",
    "confidence": "observed | confirmed",
    "created_at": "…"
  }
}
```

`text`만 임베딩한다.

#### 5.4.3 쓰기 정책 — 좁게 연다

| | 규칙 |
|--|------|
| **누가** | **Main만.** `remember` 툴. 서브에이전트는 Store에 쓰지 못한다 |
| **언제** | 단계 9(토탈 보고) 직후. 런 도중에는 쓰지 않는다 |
| **무엇을** | 일반화되는 것 — 반복될 실패 원인, 확정된 컨벤션, 판례 |
| **무엇을 안 쓰나** | **`answers.md`의 사람 답변.** 그건 이 run 고유의 결정이지 일반 지식이 아니다 |

> **왜 answers를 Store에 안 넣나** — 다음 run에서 비슷한 질문이 나왔을 때
> 과거 답을 **사람 답변인 것처럼** 재사용하면 `G_ANSWERS_HUMAN`이 무력화된다.
> 답은 매번 사람이 한다. Store에 남기는 것은 「이런 구멍이 반복된다」는 **패턴**뿐이다.

#### 5.4.4 읽기 정책

| 시점 | 네임스페이스 | 용도 |
|------|-------------|------|
| 런 시작 (단계 0~1) | `conventions`, `glossary` | 스펙 초안의 전제 |
| 구멍찾기 전 (단계 2) | `failures` | **「이 스펙과 비슷한 과거 FAIL」** |
| 권한검수 전 (단계 6) | `verdicts` | 과거 판정과 일관성 |
| 보고 (단계 9) | `preferences` | 형식 |

**회수 결과는 근거가 아니라 참고다.** Store에서 나온 문장은
`[스펙 결정]`으로 취급하며, `[답변]`으로 승격할 수 없다.

#### 5.4.5 오염 방지

1. Store 레코드는 **`source_run_id`와 `evidence`가 없으면 저장하지 않는다.**
2. 회수된 내용이 스펙에 반영되면 **출처를 Store 레코드 키로 링크**한다.
3. `confidence: observed`는 1회 관측, `confirmed`는 2회 이상 재현. **`confirmed`만 자동 주입**하고 `observed`는 Main이 명시 조회할 때만 준다.
4. 네임스페이스는 **org/user로 격리**한다. 다른 조직의 기억이 새지 않는다.

### 5.5 구현·검증 루프 상태 (v0.4 신규)

#### 5.5.1 타입

```python
class TestResult(TypedDict):
    id: str                       # 테스트 제목 또는 파일::케이스
    ac_ref: str | None            # ★ 인수조건 조항 ID — SDD와 연결되는 고리
    status: Literal["passed", "failed", "skipped", "flaky"]
    message: str | None
    duration_ms: int | None

class E2EState(TypedDict):
    status: Literal["not_run", "passed", "failed", "blocked"]
    tests: list[TestResult]
    screenshots: list[str]        # ★ 디스크 경로만. 내용 금지
    traces: list[str]             # ★ 경로만. trace.zip은 수십 MB다
    logs: list[str]               # ★ 경로만
    report_path: str | None
    passed_count: int             # 회귀 감지용 (§5.5.4-4)
    ran_at: str | None

class FailureAnalysis(TypedDict):
    root_cause: Literal["impl_bug", "spec_gap", "test_defect", "environment"]
    confidence: Literal["low", "medium", "high"]
    rationale: str                # 왜 그렇게 판단했나
    affected_files: list[str]
    affected_ac: list[str]        # 어떤 인수조건이 걸렸나
    suggested_fix: str
    route: Literal["reimplement", "respec", "fix_test", "retry", "escalate"]

class ImplState(TypedDict):
    files: list[str]
    changes: list[str]
    branch: str | None
    patch_path: str | None
    spec_hash_at_impl: str | None # ★ 루프 중 스펙 드리프트 감지 (§5.5.4-3)
```

**하이브리드 FS 규칙이 그대로 적용된다** (§5.3).
스크린샷·트레이스·로그는 **경로만** State에 담는다.
Playwright `trace.zip`은 수십 MB라 체크포인트에 넣으면 즉사한다.

#### 5.5.2 실패 원인 4종 → 경로 (정책의 핵심)

| `root_cause` | 무엇인가 | `route` | Main 동작 | `iteration++` |
|--------------|---------|---------|-----------|--------------|
| `impl_bug` | 스펙은 맞는데 코드가 틀림 | `reimplement` | `spawn implement` (같은 스펙·다른 모델 가능) | **✓** |
| **`spec_gap`** | **구현자가 해석해야 했던 빈칸** | `respec` | **`verify_passed=false` → 단계 3 재질문 루프** | **✗ 루프 이탈** |
| `test_defect` | 셀렉터·대기·flaky | `fix_test` | `spawn qa` — **테스트만** 수정 | ✗ (별도 카운터) |
| `environment` | 서버 미기동·DB 없음·포트 충돌 | `retry` | 1회 재시도 → 안 되면 escalate | ✗ |

> **`spec_gap`에서 implement로 돌아가면 안 되는 이유**
> 돌아가면 AI가 빈칸을 **추정으로 메운다.** 그 추정이 테스트를 통과하는 순간
> **추정이 사실상의 스펙이 된다.** `G_ANSWERS_HUMAN`·`G_VERIFY`·`G_READY`가
> 막으려던 실패가 E2E 루프로 우회되는 경로다.
> 그래서 `spec_gap`은 iteration을 올리지 않고 **루프를 빠져나가** 단계 3~6으로 되돌린다.

#### 5.5.3 진단은 별도 세션 (`e2e-triage`, 9번째 스킬)

원인 분류를 **누가** 하느냐가 중요하다.

| 후보 | 문제 |
|------|------|
| `implement`가 자기 실패를 진단 | 자기 코드를 방어한다. `spec_gap`을 `impl_bug`로 부른다 |
| `qa`가 진단 | 자기가 쓴 테스트를 방어한다. `test_defect`를 못 본다 |
| Main이 진단 | 트레이스·로그를 읽어야 하는데 요약만 받는다 |

→ **`e2e-triage` 스킬을 별도 spawn**한다. implement·qa와 **다른 세션**이며,
트레이스·로그·스크린샷 경로를 받아 **자기 컨텍스트에서** 읽고 `FailureAnalysis`만 반환한다.
컨텍스트 격리가 필요한 전형적인 자리다.

#### 5.5.4 루프 가드 (전부 코드)

| # | 가드 | 동작 |
|---|------|------|
| 1 | `iteration >= max_iterations` (기본 3) | 중단 → `waiting_human` |
| 2 | **같은 `test.id`가 같은 `root_cause`로 2연속 실패** | 진단이 틀린 것이다. 강제 escalate |
| 3 | `sdd.spec_hash != implementation.spec_hash_at_impl` | 루프 중 스펙이 바뀜 → 루프 폐기, 하위 invalidate |
| 4 | **`e2e.passed_count`가 직전 회차보다 감소** | **회귀.** 즉시 중단 |
| 5 | `G_BUDGET` 초과 | 중단 |

> **2번이 비직관적이지만 중요하다.** 같은 진단으로 두 번 고쳤는데 같은 테스트가
> 또 실패하면, 고치는 법이 틀린 게 아니라 **진단이 틀린** 것이다.
> 세 번째 시도는 낭비다.

**`iteration`은 `impl_bug` 재시도만 센다.** `respec`으로 나가면 리셋하지 않고 **보존**한다 —
스펙을 고친 뒤에도 남은 예산이 얼마인지 알아야 하기 때문이다.

---

## 6. 이벤트

### 6.1 목록

| event | 발행자 | Main 전형 동작 |
|-------|--------|----------------|
| `sources.ready` | human | spawn `spec-write` |
| `spec.draft.ready` | spec-write | spawn `openspec` ∥ `spec-kit` |
| `questions.ready` | openspec / spec-kit | 둘 다 끝나면 spawn `answer-triage` |
| `triage.ready` | answer-triage | **`wait_human`** → interrupt |
| `answers.ready` | human | spawn `spec-rereview` |
| `spec.updated` | spec-rereview | spawn `ready-audit` |
| `ready.audit.ready` | ready-audit | 게이트 `verify_spec()` 실행 |
| `spec.verify.passed` | **gate** | spawn `implement` 허용 |
| `spec.verify.failed` | **gate** | 재계획 / 재질문 루프 |
| `impl.ready` | implement | spawn `qa` (새 spawn_id) |
| `qa.passed` | qa | 토탈 보고 → done |
| `qa.failed` | qa | **spawn `e2e-triage`** — 직접 재구현하지 않는다 |
| `triage.diagnosed` | e2e-triage | `route`에 따라 분기 (§5.5.2) |
| `loop.exhausted` | gate | 루프 가드 발동 → `waiting_human` |
| `run.report.ready` | Main | 종료 |

> **`qa.failed` → 바로 `implement` 재spawn은 금지다.**
> 원인이 4종인데 대응이 하나면 `spec_gap`이 조용히 추정으로 메워진다 (§5.5.2).
> 반드시 `e2e-triage`를 거친다.

### 6.2 공통 페이로드

```json
{
  "event": "questions.ready",
  "thread_id": "th_20260923_crm_metrics",
  "run_id": "run_20260923_140000_crm_metrics",
  "feature_id": "crm-metrics",
  "ts": "2026-09-23T14:00:00Z",
  "actor": "openspec",
  "spec_ref": "spec.md@sha256:…",
  "artifacts": { "questions": "questions.openspec.md" },
  "spawn_id": "sp_01H…"
}
```

### 6.3 규칙

- Subagent는 **다른 skill을 호출하지 않는다.** 요약 반환 + 이벤트 append만.
- 동일 `spawn_id` 재처리는 **멱등하게 무시**한다.
- `questions.ready`는 **둘(openspec·spec-kit) 다 도착해야** 다음으로 간다.

---

## 7. HTTP API (MVP)

Base: `/api/v1`

| Method | Path | 설명 |
|--------|------|------|
| `POST` | `/threads` | `{feature_id, sources: {name: markdown}, target_repo_path?}` → `thread_id` |
| `GET` | `/threads/{tid}` | 현재 state 요약 (status, todos, stage, verify_passed) |
| `GET` | `/threads/{tid}/files/{name}` | State `files` 내용 |
| `POST` | `/threads/{tid}/answers` | `{answers_markdown}` → **`Command(resume=…)`** |
| `POST` | `/threads/{tid}/events` | 외부/테스트용 이벤트 주입 |
| `GET` | `/threads/{tid}/history` | checkpoint 목록 (디버그) |
| `GET` | `/threads/{tid}/report` | `report.md` |
| `GET` | `/models` | 가용 모델 목록 |
| `GET` | `/health` | liveness |

인증 MVP: `Authorization: Bearer <ORCH_API_TOKEN>` 단일 토큰.

**`POST /answers`의 책임**

1. `gates.check_answers_human(payload, actor)` — 실패 시 400
2. `files["answers.md"]` 갱신
3. `graph.ainvoke(Command(resume=...), config={"configurable": {"thread_id": tid}})`

---

## 8. 그래프 · 에이전트 루프

### 8.1 배선

```python
builder = StateGraph(AgentState)
builder.add_node("main_agent", main_agent_node)
builder.add_node("tools", ToolNode(TOOLS))

builder.add_edge(START, "main_agent")
builder.add_conditional_edges("main_agent", tools_condition)  # tools | END
builder.add_edge("tools", "main_agent")

graph = builder.compile(checkpointer=get_checkpointer())
```

**노드는 둘뿐이다.** 단계별 노드(spec 노드 → impl 노드 …)를 만들지 않는다.
그건 워크플로지 에이전트가 아니다.

### 8.2 `main_agent_node`

```text
def main_agent_node(state) -> dict:
    msgs = compaction.fit(state["messages"])        # §12.1

    context = [
        system_prompt(),                            # §8.4
        *msgs,
        recite(state["todos"]),                     # ★ 매 턴 재주입
        summarize_sdd(state["sdd"]),                # stage, verify_passed, ready_open_count
        recent_gate_rejects(state["sdd"]),          # 왜 막혔는지
        available_models(),                         # list_models()
    ]

    ai = llm.bind_tools(TOOLS).invoke(context)
    observability.record(ai)                        # 토큰·비용
    return {"messages": [ai]}
```

**`recite(todos)`가 핵심이다.** 시스템 프롬프트에 한 번 넣고 마는 것과 결과가 다르다.
계획을 **최근 컨텍스트에 매 턴 다시 넣어야** 긴 런에서 목표 표류가 안 난다.

### 8.3 Main 툴 목록

| 툴 | 파라미터 | 반환 |
|----|----------|------|
| `write_todos` | `items: [{id, text, status}]` | `Command(update={"todos": …})` |
| `spawn` | `skill, model, brief, budget_usd?` | **요약 문자열** 또는 `GateReject: …` |
| `read_file` | `path` | State `files` 내용 |
| `ls` | — | 파일 목록 |
| `wait_human` | `reason, waiting_for` | `interrupt()` |
| `finish` | `summary` | 보고 작성 → `status="done"` |
| `fail_run` | `reason` | `status="error"` |

**Main에 없는 툴:** `write_file`, `apply_patch`, `git_commit`, `bash`
→ Main이 일을 가로채는 것을 **툴 부재로** 막는다. 프롬프트로 막지 않는다.

### 8.4 Main 시스템 프롬프트 강제 조항

1. 너는 스펙/코드를 쓰지 않는다. **spawn만** 한다.
2. `model`은 매 spawn 필수. **고정표는 없다.** `list_models()`만 본다.
3. `verify_passed=false`거나 `ready_open_count>0`이면 `implement`/`qa`를 고르지 마라 — 골라도 코드가 거부한다.
4. `triage.ready` 이후에는 `wait_human`이 정상이다.
5. QA는 implement와 **다른 spawn_id**여야 한다.
6. 단계 2(구멍 찾기)는 **openspec·spec-kit 둘 다** 돌린다. 둘 다 끝나야 다음이다.
7. **구현은 병렬로 쪼개지 않는다.** implement는 한 번에 하나.
8. 실패 시 같은 스킬·다른 모델 또는 다른 스킬로 재계획하고 **이유를 남겨라**.

---

## 9. `spawn` — 서브에이전트 격리

### 9.1 왜 노드가 아니라 툴인가 (하드 제약)

`messages`에 `add_messages` 리듀서가 붙어 있다.
서브에이전트를 **같은 그래프의 노드**로 두면 그 세션의 중간 메시지가
**전부 부모 `messages`에 병합된다.** 컨텍스트 격리가 그 순간 사라진다.

**돌아가긴 하므로 한참 뒤에야 발견되는 종류의 버그다. 처음부터 툴로 짠다.**

### 9.2 구현 골격

```python
@tool
def spawn(
    skill: str,
    model: str,
    brief: str,
    state: Annotated[AgentState, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
    budget_usd: float | None = None,
) -> Command:
    # ① 게이트 — 순수 함수. 예외 대신 문자열 반환
    reject = gates.allow_spawn(state["sdd"], skill, model, catalog)
    if reject:
        return Command(update={
            "messages": [ToolMessage(
                f"GateReject: {reject.code} — {reject.message}",
                tool_call_id=tool_call_id,
            )],
            "sdd": push_reject(state["sdd"], reject),
        })

    # ② 스킬 로드
    sk = skills_loader.load(skill)

    # ③ 격리 실행 — 부모 messages를 넘기지 않는다
    sub = subagent.build(sk, model)
    result = sub.invoke({
        "messages": [SystemMessage(sk.system_prompt),
                     HumanMessage(brief)],          # ← brief만
        "files": select_inputs(state["files"], sk.inputs),
        "workspace": state["sdd"].get("target_repo_path"),
    })

    # ④ 산출물 검증 (skill contract)
    outputs = sk.validate_outputs(result["files"])

    # ⑤ 요약만 반환
    return Command(update={
        "messages": [ToolMessage(result["summary"], tool_call_id=tool_call_id)],
        "files": {**state["files"], **outputs},
        "sdd": append_spawn(state["sdd"], skill, model, brief, result),
    })
```

### 9.3 격리 규칙

1. 서브에게 넘기는 것은 **`brief` + 스킬이 선언한 입력 파일**뿐이다.
2. 부모 `messages`는 **절대** 넘기지 않는다.
3. 부모에 돌아오는 것은 **`summary` 문자열 하나 + 산출 파일**뿐이다.
4. `qa` spawn은 `implement`의 transcript를 **어떤 형태로도** 받지 않는다.
5. 같은 `model` id여도 세션은 분리된다.

### 9.4 병렬 spawn (단계 2 전용)

`openspec`과 `spec-kit`은 동시에 돌 수 있다.
Main이 **한 턴에 두 개의 tool call**을 내면 `ToolNode`가 병렬 실행한다.

**단계 7(implement)은 병렬 금지.** §8.4-7 참조.

### 9.5 모델 카탈로그

`config/models.available.json`:

```json
{
  "models": [
    { "id": "…", "provider": "openai", "input_per_1m": 0, "output_per_1m": 0 }
  ]
}
```

- 스키마가 `default_for_skill` / `role` / `skills` 필드를 **금지**한다.
- `G_MODEL_KNOWN`이 이 목록으로 검증한다.

---

## 10. 스킬 9종 계약

각 스킬 디렉터리의 `SKILL.md`가 진실. 여기 요약.

| skill | 입력 | 출력 | 이벤트 | 핵심 금지 |
|-------|------|------|--------|-----------|
| `spec-write` | `sources/*`, 보일러플레이트 | `spec.md` (draft) | `spec.draft.ready` | TBD를 추정으로 채움 |
| `openspec` | `spec.md`, REVIEW-PROMPT | `questions.openspec.md` | `questions.ready` | `답:` 채우기 |
| `spec-kit` | `spec.md`, REVIEW-PROMPT | `questions.speckit.md` | `questions.ready` | `답:` 채우기 |
| `answer-triage` | questions 둘 | `questions.md` (병합·4갈래) | `triage.ready` | 사람 몫을 대신 답함 |
| `spec-rereview` | `spec.md`, `answers.md` | 갱신 `spec.md` | `spec.updated` | 답 무시하고 추정 보강 |
| `ready-audit` | `spec.md`, `answers.md`, `sources/*` | `ready.md` | `ready.audit.ready` | 기본값 골라놓고 넘어감 |
| `implement` | **verified** `spec.md` + hash + repo | 브랜치/patch | `impl.ready` | 스펙 밖 기능, 스택 교체, **테스트 파일 수정** |
| `qa` | verified AC + impl 산출물 | `qa-report.md` + Playwright | `qa.passed`/`failed` | implement 세션 이어받기 |
| `e2e-triage` | 실패 테스트 + 트레이스·로그 경로 + spec | `FailureAnalysis` | `triage.diagnosed` | 코드·테스트 **수정** (진단만 한다) |

### 10.1 `spec-write` — 표기 4종 필수

생성하는 모든 규칙 줄에 출처 등급을 단다.

| 표시 | 뜻 |
|------|-----|
| `[코드]` | 현재 코드에서 확인한 사실 |
| `[답변]` | 사람이 직접 정한 것 |
| `[스펙 결정]` | 이 문서가 처음 정한 것 |
| 무표시 | 출처 링크 원문에 근거 있음 |

**세부태스크는 다섯 절로 쓴다:** `처리 · 성공 · 실패 · 예외 · 출처`
`실패`는 성공의 반대말이 아니라 **하지 말아야 할 것**을 적는다.

### 10.2 `openspec` / `spec-kit` — CLI 호출 (Q4 결정)

#### 10.2.1 ⚠️ CLI는 LLM을 부르지 않는다

이걸 먼저 잡아야 설계가 안 꼬인다.

`openspec` / `specify`는 **스캐폴딩 + 검증** 도구다. 질문지를 만들어 주는 게 아니라,
**AI 에이전트가 실행할 지시문(instructions)을 emit**하고 결과를 **검증**한다.

```
openspec init                          구조 생성 (1회)
openspec instructions <artifact> --json  ← 지시문을 꺼낸다
        │
        ▼
  [우리 서브에이전트 LLM]  ← 실제 구멍찾기는 여기서
        │
        ▼
openspec validate --strict --json      ← 결정적 검증  ★게이트 재료
openspec status --json                 아티팩트 완료 상태
```

**역할 분담**

| 누가 | 무엇 |
|------|------|
| **CLI** | 구조 생성 · **지시문 제공** · **결정적 검증** |
| **서브에이전트 LLM** | 지시문을 실행해 **구멍을 찾는다** |

`validate --strict --json`이 **결정적**이라는 점이 핵심이다.
프롬프트 판정이 아니라 종료코드와 JSON으로 나오므로 **게이트로 쓸 수 있다** (`G_OPENSPEC_VALID`, §11).

#### 10.2.2 실행 계약

| | openspec | spec-kit |
|--|----------|----------|
| 바이너리 | `openspec` (npm `@fission-ai/openspec`) | `specify` (spec-kit) |
| 검증 확인 | `1.12.0` | (컨테이너에서 확인) |
| 초기화 | `openspec init <path>` | `specify init` |
| 지시문 | `openspec instructions <artifact> --json` | 생성된 슬래시커맨드 프롬프트 파일 |
| 검증 | `openspec validate --strict --json` | — |
| 산출 | `questions.openspec.md` | `questions.speckit.md` |

**서브에이전트 셸 툴 — allowlist만**

```text
허용: openspec {init,instructions,validate,status,list,show}
      specify  {init,check}
      playwright test            (qa 스킬만)
      git {status,diff,add,commit,checkout,branch}  (implement 스킬만)
금지: 그 외 전부. 셸 임의 실행 금지
```

`sdd_cli.py`가 서브프로세스를 감싸고 **인자를 화이트리스트 검증**한다.
LLM이 만든 문자열을 셸에 그대로 넘기지 않는다.

**Dockerfile 요구사항**

```text
node 22+ + npm i -g @fission-ai/openspec@latest
uv / uvx + spec-kit
npx playwright install --with-deps   (qa용)
```

> **로컬 개발 주의:** Windows에서 `specify.exe`가 Application Control 정책에 막히는 경우가 있다.
> 서버 컨테이너에서는 무관하므로 **로컬은 openspec만으로 진행**해도 된다.

#### 10.2.3 찾을 것 / 묻지 말 것

두 스킬은 **같은 REVIEW-PROMPT를 공유**하고 도구만 다르다.

**찾을 것**

- 인수 조건에 있는데 **판별·계산 규칙이 없는** 항목
- `~라면 ~한다` 조건문으로 남아 **조건을 판정할 수 없는** 줄
- 한 절이 **구분하라** 한 것을 다른 절이 **뭉뚱그린** 곳
- `표시한다`고만 쓰고 **그 값을 어떻게 만드는지** 안 쓴 곳
- **화면은 정했는데 데이터 출처**가 안 정해진 곳
- 선택지를 `또는`으로 남기고 **분기 조건**을 안 쓴 곳

**묻지 말 것**

- 누가 정하나 · 언제 하나 · 누가 사인하나
- 답변 파일에 **이미 답이 있는** 것
- 「아직 정해지지 않은 것」 절에 이미 있는 것
- 근거 없이 숫자만 정하는 성능 목표 · 보존 기간
- 컴포넌트 구조 · 로딩 UI 등 **구현자 재량**

**규칙**

- 질문마다 **"답이 없으면 AI가 무엇을 잘못 만드는가"** 한 줄
- **선택지 + 추천안**. 답이 한 줄에 떨어져야 한다
- 이미 답이 있으면 **「이미 결정: 인용」**

> `시니어 검수` 같은 프레이밍을 쓰면 도구가 사람 시니어를 상상해
> "누가 사인하나 / 언제 배포하나"를 쏟아낸다. **쓰지 않는다.**

### 10.3 `answer-triage` — 4갈래

| 갈래 | 처리 |
|------|------|
| 코드를 보면 사실이 나온다 | 열어 확인하고 `파일:줄` |
| 이미 정한 것에서 유도된다 | 유도 경로를 인용으로 |
| 기술 판단 | 추천안 + 이유 한 줄 |
| **사업·조직 지식 필요** | **사람에게 묻는다** |

경계가 애매하면 **마지막으로 보낸다.** 사업 판단을 대신 정하지 않는다.

> 실측: 51문 중 44문이 앞의 셋으로 풀렸고, 사람 답이 꼭 필요한 것은 **4개**였다.

### 10.4 `ready-audit` — 권한 검수

`[스펙 결정]` **전건**을 넷으로 분류한다.

| 분류 | 처리 |
|------|------|
| **도출됨** | 유도 경로를 인용으로 보인다. 그대로 둔다 |
| **우연히 들어간 가정** | **표적.** 전부 질문으로 올린다 |
| 기술 결정 | 도메인 지식 불필요 |
| 구현 튜닝 | 결과를 안 바꾼다 |

**지시에 반드시 넣을 것**

```text
입력은 자료일 뿐 진실이 아니다. 자세하다는 이유로 옳다고 가정하지 않는다.
로드 베어링 결정은 사용자 의도에서 도출되거나 사용자가 골라야 한다.
기본값을 골라놓고 넘어가는 것은 금지다.
억지로 「가정」을 만들지 마라. 도출되면 도출된 것이다.
자료마다 권한 등급을 먼저 매기고 시작해라. 최신 문서가 이기는 게 아니다.
```

**산출물 `ready.md` 구조 — 1절만 논의 대상**

| 절 | 무엇 |
|----|------|
| **1. 남은 문제** | 아직 안 닫힌 것 전부 ← **`ready_open_count`의 출처** |
| 2. 닫힌 것 — 대장 | 충돌 · 질답 · 더한 것 |
| 3. 도구가 검사한 것 | 권한 등급표 · 분류 수치 · 오탐 판정 |
| 4. 통과 여부 | 체크박스 |

**1절 항목이 닫히면 지우지 말고 그 자리에서 「닫힘」으로 바꾼다.**
왜 그렇게 정했는지는 6개월 뒤에 필요하다.

**한계:** ready는 **문서만 본다. 대화는 못 본다.**
단계 4(답변 기록)를 건너뛰면 오탐이 쏟아진다. 반드시 4 뒤에 돌린다.

### 10.5 `implement` — 구현 순서

```
① 백엔드 계약     집계 API 응답 타입을 먼저 확정
② 저장 구조       새로 굳히는 값 · 기록할 이력
③ 화면            ①②가 있어야 붙일 대상이 생긴다
```

①을 건너뛰고 화면부터 만들면 **응답 필드 이름을 지어내게 되고** 나중에 어긋난다.

- 대상: **실제 디스크** (`target_repo_path`)
- 기존 레포 스택·컨벤션 준수. **스택 교체 금지**
- 스펙 밖 기능 금지
- 새 구멍 → **추정 금지 → 요약에 escalate 명시**
- MVP v0: patch 파일도 허용. 이벤트명은 `impl.ready` 유지

### 10.6 `qa` — 조항 ↔ 증거

- verified 스펙의 **인수조건 ID**마다 증거를 매핑한다
- **Playwright E2E**를 실행하고 결과를 첨부한다
- implement와 **다른 spawn_id**. transcript 주입 금지 (`G_SESSION_QA`)
- 모든 `TestResult`에 **`ac_ref`를 단다.** 조항에 연결되지 않은 테스트는 증거가 아니다
- 산출물은 **경로**로 넘긴다 — 트레이스·스크린샷 내용을 State에 넣지 않는다

**테스트 수정 권한:** 평상시 없음. `route == "fix_test"`로 재spawn될 때만 열리며,
그때도 **AC 매핑을 유지**해야 한다 (`G_TEST_INTEGRITY`).

### 10.7 `e2e-triage` — 실패 진단 전용 (v0.4 신규)

| | |
|--|--|
| 입력 | 실패 `TestResult[]` + 트레이스·로그·스크린샷 **경로** + `spec.md` + 변경 파일 목록 |
| 출력 | `FailureAnalysis` (§5.5.1) |
| 이벤트 | `triage.diagnosed` |
| 세션 | implement·qa와 **전부 다름** |
| **금지** | **코드·테스트 수정.** 읽고 진단만 한다 |

**분류 지시 (프롬프트 강제 조항)**

```text
네 판정이 다음 행동을 정한다. 편한 쪽으로 분류하지 마라.

impl_bug   — 스펙이 이 동작을 명시했고, 코드가 그와 다르다.
             스펙의 해당 줄을 인용하라. 인용 못 하면 impl_bug가 아니다.
spec_gap   — 구현자가 결정해야 했던 빈칸이 있다.
             "스펙 어디에도 이 판별 규칙이 없다"를 보여라.
test_defect— 스펙도 코드도 맞는데 테스트가 틀렸다.
             셀렉터·대기·데이터 준비 중 무엇인지 지목하라.
environment— 앱이 뜨지 않았거나 외부 의존이 없다.

판정 못 하겠으면 confidence: low 로 두고 escalate를 route로 내라.
impl_bug로 기본 분류하지 마라 — 그게 제일 흔한 오진이고,
spec_gap을 impl_bug로 부르면 AI가 빈칸을 추정으로 메우게 된다.
```

> **왜 `impl_bug`가 기본값이면 안 되나** — 가장 고치기 쉬워 보이기 때문에
> 모델이 그쪽으로 쏠린다. 그런데 실제로 `spec_gap`이었다면,
> 재구현이 추정으로 통과를 만들어 내고 **아무도 그 사실을 모른다.**

---

## 11. 게이트

상세·의사코드: [`gates.md`](gates.md).

| Gate ID | 언제 | 실패 시 |
|---------|------|---------|
| `G_MODEL_KNOWN` | 모든 spawn | GateReject → ToolMessage |
| `G_ANSWERS_HUMAN` | `POST /answers` | HTTP 400 |
| `G_OPENSPEC_VALID` | openspec 스킬 종료 후 | `openspec validate --strict` 실패 시 재spawn |
| `G_VERIFY` | ready-audit 후 | `spec.verify.failed` |
| `G_READY` | spawn implement | `ready_open_count > 0`이면 reject |
| `G_NO_IMPL_WITHOUT_VERIFY` | spawn implement/qa | GateReject |
| `G_SESSION_QA` | spawn qa | GateReject |
| `G_TEST_INTEGRITY` | implement/qa 산출물 검사 | 테스트 약화 시 거부 |
| `G_LOOP` | 재구현 spawn 전 | 루프 가드 5종 (§5.5.4) |
| `G_TRIAGE_FIRST` | `qa.failed` 이후 | e2e-triage 없이 implement spawn 시 거부 |
| `G_STORE_WRITE` | `remember` 호출 | 장기기억 오염 방지 |
| `G_BUDGET` | spawn 전 (선택) | GateReject |

**원칙 1:** 프롬프트가 "PASS라고 함" ≠ PASS. `verify_passed`는 `gates.verify_spec()`만 set한다.

**원칙 2 (v0.2 신규):** 게이트 거부는 **예외가 아니라 ToolMessage**다.
Main이 거부 사유를 읽고 **스스로 재계획**한다. fail-closed와 자율성이 동시에 성립한다.

**원칙 3:** `gates.py`는 **LangGraph를 import하지 않는다.** 순수 함수다.

### 11.1 Verify 6항

1. 인수조건마다 판별/계산/출처
2. 조건문·`또는`에 분기 조건
3. 절 간 모순 0
4. 추정 답 0
5. 잔여 차단 질문 0
6. 승인 토큰 + **spec hash**

MVP에서 1~5는 휴리스틱 스크립트 + 체크리스트로 시작 가능.
**6번(토큰+hash)과 answers 공란 검사, ready 1절 검사는 반드시 코드.**

---

## 12. 압축 · 영속화

### 12.1 압축 (compaction)

**부가기능이 아니라 하네스의 본체다.**

```text
if token_count(messages) > COMPACT_THRESHOLD:
    old, recent = split(messages, keep_last=N)
    summary = llm.summarize(old)
    messages = [SystemMessage(summary), *recent]
```

**압축해도 반드시 살아남을 것**

- `todos` (State 필드라 messages와 무관 — 이것이 Planning을 밖에 두는 이유)
- `sdd.spec_hash`, `sdd.verify_passed`, `sdd.ready_open_count`
- 최근 `gate_rejects`
- 마지막 사람 답변 요약

**버릴 것:** 오래된 spawn 요약 본문, 중복 파일 덤프.

### 12.2 체크포인터

| Phase | saver | 용도 |
|-------|-------|------|
| 1~2 | `InMemorySaver` | 개념 확인 |
| 3+ | `PostgresSaver` | 재시작 후 재개 |

```python
config = {"configurable": {"thread_id": tid}}
graph.invoke(state, config)              # 저장
graph.invoke(Command(resume=ans), config)  # 재개
```

**부르는 thread만 로드된다.** 전체 히스토리를 메모리에 올리지 않는다.

### 12.3 pgvector (v0.3 — 보류 철회, 채택)

Store(§5.4)를 넣는 순간 **의미 검색이 필수**가 된다.
`failures` / `verdicts`가 쌓이면 전부 컨텍스트에 넣을 수 없기 때문이다.

| 항목 | 결정 | 이유 |
|------|------|------|
| 확장 | `CREATE EXTENSION vector` | 별도 벡터DB 없이 Postgres 하나로 |
| 임베딩 모델 | **`text-embedding-3-small`** | $0.02/1M · 1536d |
| 차원 | **1536** | **pgvector HNSW 인덱스 상한(2000d)에 맞는다** |
| 인덱스 | HNSW (cosine) | |
| 대안 | `text-embedding-3-large` + `dimensions=1536` | 정확도가 부족하면. 기본 3072d는 **HNSW로 인덱싱 불가** |

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE memories (
  key          text PRIMARY KEY,
  namespace    text[] NOT NULL,
  kind         text   NOT NULL,
  text         text   NOT NULL,
  value        jsonb  NOT NULL,
  source_run_id text  NOT NULL,          -- §5.4.5-1: 없으면 저장 금지
  confidence   text   NOT NULL DEFAULT 'observed',
  embedding    vector(1536),
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ON memories USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON memories (namespace);
```

**임베딩 대상은 `text` 필드뿐이다.** 전체 문서를 임베딩하지 않는다.

> **여전히 유효한 주의:** 스펙·코드 **본문 검색**은 임베딩보다
> 구조적 탐색(grep·앵커·줄링크)이 낫다. 벡터는 **Store 회수 전용**이고,
> spec.md를 통째로 임베딩하지 않는다.

### 12.4 Store 구현 (v0.3 신규)

```python
from langgraph.store.postgres import PostgresStore

store = PostgresStore.from_conn_string(
    DATABASE_URL,
    index={"dims": 1536, "embed": "openai:text-embedding-3-small", "fields": ["text"]},
)
graph = builder.compile(checkpointer=saver, store=store)
```

**노드·툴에서 접근**

```python
def main_agent_node(state, *, store: BaseStore):
    hits = store.search(("org", org_id, "failures"), query=brief_of(state), limit=3)
    ...
```

**Main 전용 툴 2개**

| 툴 | 파라미터 | 규칙 |
|----|----------|------|
| `recall` | `namespace, query, limit` | 언제든 호출 가능 |
| `remember` | `kind, text, evidence, confidence` | **단계 9 이후에만.** `evidence` 없으면 거부 |

서브에이전트에게는 **둘 다 주지 않는다.** 회수 결과가 필요하면 Main이 `brief`에 실어 보낸다.

**`confirmed` 승격:** 같은 `kind`+유사 `text`가 **다른 `source_run_id`에서 2회 이상**
관측되면 `observed → confirmed`. 자동 주입 대상은 `confirmed`뿐이다 (§5.4.5-3).

---

## 13. 인수조건 · 비목표

### 13.1 이 오케스트레이터 자체의 인수조건

1. `POST /threads` → (사람 답 1회) → report까지 진행 가능
2. `verify_passed=false`일 때 implement spawn이 **GateReject ToolMessage**로 거부되고, Main이 재계획한다
3. `ready_open_count > 0`이면 implement spawn 거부
4. questions의 `답:`을 subagent가 채우면 `G_ANSWERS_HUMAN` 실패
5. `spawn_history`에 모든 spawn의 **skill + model + brief + 비용** 기록
6. **subagent messages가 부모 `messages`에 나타나지 않는다** (테스트로 검증)
7. `deepagents` import **없음** (CI grep)
8. 역할→모델 매핑 설정 파일 **없음**
9. Main 툴 목록에 `write_file` / `bash` / `git_commit` **없음**
10. `gates.py`에 `langgraph` import **없음** (CI grep)
11. 프로세스 kill 후 같은 `thread_id`로 재개 (Phase 3)
12. **서브에이전트가 Store에 쓰지 못한다** — `remember` 툴은 Main에만 있다
13. **`answers.md` 내용이 Store에 저장되지 않는다** (테스트)
14. `evidence` / `source_run_id` 없는 Store 레코드는 **저장 거부**
15. 서브에이전트 셸 호출이 **allowlist 밖이면 거부** (임의 셸 실행 불가)
16. **`qa.failed` 직후 `implement` spawn이 거부**된다 — `e2e-triage`를 거쳐야 한다
17. **`route == "spec_gap"`이면 `verify_passed`가 false로 내려간다** (테스트로 검증)
18. `implement`가 **테스트 파일을 수정하면 거부**된다
19. **통과 테스트 수가 줄면 루프가 중단**된다 (회귀 감지)
20. `iteration >= max_iterations`면 `waiting_human` — 무한 루프 없음
21. 트레이스·스크린샷이 State에 **경로로만** 담긴다 (내용 금지)

### 13.2 비목표

- PRD AI 단독 확정
- 사람 답 없이 spec PASS
- **implement를 병렬 서브에이전트로 쪼개기**
- `deepagents` 도입
- 모델 고정 배치표
- 배포 오케스트레이션

---

## 14. 구현 순서

| Step | 산출 | 완료 기준 |
|------|------|-----------|
| **S0** | 본 SPEC + concepts + gates + schemas | 문서 리뷰 ✅ |
| **S1** | `AgentState` + 2노드 그래프 + `write_todos` + `InMemorySaver` | 툴 루프가 돈다 |
| **S2** | `spawn` 툴 — 격리 서브그래프 + 요약 반환 | **부모 messages에 ToolMessage 1개만** (테스트) |
| **S3** | `gates.py` (순수) + GateReject ToolMessage | 거부 → Main 재계획 green |
| **S4** | `sdd_cli.py` (allowlist) + `spec-write` / `openspec` / `spec-kit` 병렬 spawn + `G_OPENSPEC_VALID` | `questions.md` 생성 · validate green |
| **S5** | `answer-triage` + `wait_human` → `interrupt()` + `/answers` resume | 사람 답 1회 왕복 |
| **S6** | `spec-rereview` + `ready-audit` + verify 6항 + `G_READY` | `verify_passed=true` |
| **S7** | `implement` (실제 디스크) + `qa` + Playwright + report | E2E 1건 |
| **S8** | **`e2e-triage` + 4종 라우팅 + 루프 가드 5종** | 일부러 `spec_gap`을 심고 **재질문으로 나가는지** 확인 |
| **S9** | `PostgresSaver` + 재시작 재개 + 압축 | kill 후 이어짐 |
| **S10** | **`PostgresStore` + pgvector + `recall`/`remember`** | 과거 FAIL 회수가 다음 run에 뜬다 |
| **S11** | Dockerfile + Compose + K8s + cloudflared | 외부 접속 |

**S8의 테스트가 S2 다음으로 중요하다.** `spec_gap`이 `impl_bug`로 오진되면
SDD 게이트 전체가 E2E 루프로 우회된다. 일부러 구멍 있는 스펙으로 한 바퀴 돌려본다.

각 Step마다 `tests/`에 게이트·격리 단위 테스트를 추가한다.

**S2의 테스트가 가장 중요하다.** 격리가 깨지면 나머지가 전부 조용히 망가진다.

---

## 15. 열린 질문

### 15.1 닫힌 것

| ID | 질문 | **결정** | 날짜 |
|----|------|---------|------|
| **Q1** | Main 모델은 env 고정인가? | **env `MAIN_MODEL` 고정.** 서브 모델은 매 spawn Main이 선택 — §0.2 「고정표 없음」과 모순되지 않는다 | 2026-09-23 |
| **Q4** | openspec / spec-kit을 CLI로 호출? | **CLI 호출.** 단 CLI는 LLM을 부르지 않는다 — scaffold·instructions·**validate**는 CLI, 구멍찾기는 서브에이전트 LLM (§10.2) | 2026-09-23 |
| **Q6** | 장기기억(Store)·벡터DB를 범위에 넣나? | **넣는다.** `PostgresStore` + pgvector (§5.4, §12.3~4). Checkpoint와 분리 | 2026-09-23 |

### 15.2 열린 것

| ID | 질문 | 기본 제안 (비구속) |
|----|------|-------------------|
| Q2 | `절 간 모순 0`(verify 3항)을 MVP에서 어떻게 판정? | `meta/human-crosscheck.ok` 수동 체크 파일. 없으면 failed |
| Q3 | `thread_id`와 `run_id`를 분리 유지? | MVP는 동일값. 재질문 루프가 새 thread를 쓰게 되면 분리 |
| Q5 | `files` 256KB 초과 시 자동 디스크 강등? | S2에서 경고만, S8에서 자동화 |
| Q7 | `org_id` / `user_id`를 어디서 받나? (Store 네임스페이스 키) | MVP는 env 단일 org. 멀티테넌시는 이후 |
| Q8 | `confirmed` 승격의 유사도 임계값 | 0.85 cosine에서 시작해 실측으로 조정 |
| Q9 | `max_iterations` 기본값 | **3.** 회차당 implement(비싼 모델)+qa+triage라 실비용이 크다 |
| Q10 | `flaky` 테스트를 실패로 셀 것인가 | 1회 재실행 후에도 불안정하면 `test_defect`로 분류 |
| Q11 | `respec`으로 나간 뒤 `iteration`을 리셋? | **리셋하지 않는다.** 스펙 수정 후 남은 예산을 알아야 한다 |

---

## 16. README와의 관계

- 정책·비전: README
- **구현 계약: 본 SPEC이 우선**
- 충돌 시: SPEC을 고치고 README를 맞춘다

---

## 변경 이력

| 버전 | 날짜 | 내용 |
|------|------|------|
| 0.1 | 2026-09-22 | 초안. 자체 tick · 파일 상태 · No LangGraph |
| 0.2 | 2026-09-23 | **LangGraph 채택.** 네 기둥 구조, spawn=툴 격리, 하이브리드 FS, checkpointer/interrupt, SDD 0~9 (ready 권한검수·표기 4종 흡수), 스킬 8종, 압축, Playwright, cloudflared |
| 0.3 | 2026-09-23 | **Store(장기기억) + pgvector 채택** (§5.4·§12.3~4), **Q4 CLI 호출 결정** (§10.2 — CLI는 LLM을 부르지 않음), `G_OPENSPEC_VALID` 신설, 셸 allowlist, OpenAI 모델·임베딩 고정, Q1 확정 |
| 0.4 | 2026-09-23 | **E2E 실패 정책.** 구현·검증 루프 상태를 State로 편입(§5.5), **실패 원인 4종 → 경로 4종**(`spec_gap`은 루프 이탈), `e2e-triage` 스킬 신설(9번째), 루프 가드 5종, `G_TEST_INTEGRITY`·`G_LOOP`·`G_TRIAGE_FIRST` |
