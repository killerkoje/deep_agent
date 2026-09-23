# Plan — 개념을 쌓으며 회사 규모까지

**문서 목적:** "무엇을 어떤 순서로 **이해**하고 구축할지" 로드맵.
**구현 계약은** [`SPEC.md`](SPEC.md). 본 문서는 **학습 순서와 기술 도입**이다.
**상태:** Draft v0.4 · 2026-09-23
**원칙:** 개념이 부족한 상태에서 한 방에 K8s+에이전트를 올리지 않는다. **이해하면서** 층을 올린다.

---

## 0. 한 줄 계획

> **LangGraph로 Deep Agent 하네스(네 기둥)** → SDD 파이프라인 → **Postgres checkpointer**
> → 구현·Playwright·**실패 루프** → **Store + pgvector** → Compose → **K8s + cloudflared**
> 루프 **상태**는 LangGraph가, 루프 **제어**는 Main이 한다.

---

## 1. 지금 부족한 것 (인정하고 순서 정함)

| 개념 | 왜 필요한가 | 단계 |
|------|-------------|------|
| Agent vs Workflow | 노드를 단계별로 쪼개면 Agent가 아니게 된다 | **A** |
| State / Node / Edge / 리듀서 | LangGraph의 뼈대 | **A** |
| **컨텍스트 격리** | 서브에이전트를 쓰는 **유일한 이유** | **A** |
| 게이트 / 이벤트 | SDD의 상태머신 | **B** |
| Thread / Checkpoint | Pod가 죽어도 이어짐. **HITL의 전제** | **C** |
| 압축(compaction) | 긴 런에서 컨텍스트가 터지지 않게 | **C** |
| **실패 원인 분류** | E2E 실패는 원인이 4종, 대응이 4종 | **D** |
| Store / 장기기억 | run을 넘는 학습 | **E** |
| 프로세스 분리 (API ≠ Worker) | 스케일의 시작 | **F** |
| K8s | 여러 노드 · 배포 · Secret · 재시작 | **F** |

**하지 않는 착각**

- K8s YAML만 짜면 Deep Agent가 된다 → **K8s는 껍데기.** 본체는 A~E다
- 프레임워크를 쓰면 개념을 몰라도 된다 → **네 기둥은 우리가 짠다.** LangGraph는 실행 기반만 준다

---

## 2. 기술 선택 (고정)

| 항목 | 선택 | 이유 |
|------|------|------|
| 언어·API | Python 3.11+ · FastAPI | |
| **실행 엔진** | **LangGraph** | checkpointer · interrupt · thread 재개를 직접 짤 이유가 없다 |
| **미사용** | **`deepagents` 패키지** | 배우려는 네 기둥이 블랙박스가 된다. 소스는 레퍼런스로만 |
| 모델 | **OpenAI API** | Main은 env 고정, 서브는 Main이 매번 선택 |
| DB | **PostgreSQL** | **checkpoint(단기) + Store(장기)** 를 한 DB에 |
| 벡터 | **pgvector — 채택** | Store 회수용. **spec 본문은 임베딩하지 않는다** |
| 임베딩 | `text-embedding-3-small` (1536d) | HNSW 인덱스 상한 2000d에 맞음 |
| SDD 툴 | **openspec / spec-kit CLI** | scaffold · instructions · **결정적 validate** |
| E2E | **Playwright** | 단계 8 |
| 로컬 묶음 | Docker Compose | api + worker + postgres |
| 배포 | **K8s** + **cloudflared** | 외부 접속 먼저, 추후 AWS/GCP |
| 관측 | 트레이싱 + 토큰·비용 | **Phase A부터** |

**버전 핀 고정.** LangGraph는 `interrupt` / `Command` / prebuilt 계열이 빠르게 변해왔다.
튜토리얼을 가져올 때 **그 버전 문서인지** 확인한다.

### 2.1 Postgres에 무엇이 들어가나

| 용도 | 내용 | 누가 설계 | 시점 |
|------|------|----------|------|
| **checkpoints** | thread 단위 단기 상태 | **LangGraph `PostgresSaver`** (자동) | **Phase C** |
| `runs` | SDD 메타 — verify_passed, spec_hash, ready_open_count | 우리 | Phase C |
| `events` | 이벤트 로그 (멱등 키) | 우리 | Phase C |
| `spawns` | skill + model + brief + 토큰 + 비용 | 우리 | Phase C |
| **`memories`** | **장기 기억 + embedding(1536d)** | **우리 (`store.py`)** | **Phase E** |

> **checkpoints는 우리가 설계하지 않는다.** `PostgresSaver`가 만든다.
> `memories`는 **우리가 설계한다.** 질문이 다르기 때문이다 —
> "어디까지 했나"(checkpoint) vs "뭘 배웠나"(store). **둘을 섞지 않는다.**

### 2.2 pgvector — 채택 이유와 한계

**채택:** Store를 넣는 순간 필수가 된다. `failures`/`verdicts`가 쌓이면
전부 컨텍스트에 넣을 수 없으므로 의미 검색이 있어야 한다.

**한계 (지키기):** **spec.md를 통째로 임베딩하지 않는다.**
스펙·코드 본문 검색은 임베딩보다 구조적 탐색(grep·앵커·줄링크)이 낫다.
벡터는 **Store 레코드의 `text` 필드 전용**이다.

`PostgreSQL ≠ pgvector`. Postgres는 DB, pgvector는 **확장**이다.

---

## 3. 위상 (Phase) — 반드시 이 순서

```
A  LangGraph 최소 루프 + 격리          (InMemorySaver)
     ↓
B  SDD 파이프라인 + 게이트 + CLI        (스킬 9종 · openspec validate)
     ↓
C  Postgres checkpointer + 압축         (재시작 재개 — 단기)
     ↓
D  구현 · QA · Playwright               (E2E 1건)
     ↓
E  Store + pgvector                     (장기 기억 — run을 넘는 학습)
     ↓
F  Compose → K8s → cloudflared          (배포)
     ↓
G  운영 (HPA · 관측 · 비용 캡)
```

**E가 D 뒤인 이유** — 기억할 가치가 있는 것이 무엇인지는
**한 바퀴를 끝까지 돌려봐야** 안다. 먼저 만들면 안 쓰는 스키마가 된다.

각 Phase 끝에 **"내가 설명 가능한가?"** 체크를 둔다. 못하면 다음으로 가지 않는다.

---

### Phase A — LangGraph 최소 루프 · 격리

**목표:** "파이프라인이 그래프의 툴 루프다"를 몸으로 이해.

| 할 일 | 완료 기준 |
|-------|-----------|
| [`concepts.md`](concepts.md) 정독 | 용어를 자기 말로 설명 |
| `AgentState` TypedDict 선언 | `add_messages`가 왜 필요한지 설명 가능 |
| 노드 2개 + conditional edge | 툴 루프가 돈다 |
| `write_todos` 툴 | todos가 State에 남는다 |
| **`spawn` 툴 — 격리 서브그래프** | **부모 messages 증가분 == ToolMessage 1개** |
| 토큰·비용 기록 | 한 런의 비용이 보인다 |

**배우기**

- Workflow vs Agent — 다음 행동을 **누가** 정하나
- 리듀서: 왜 `add_messages` 없이는 대화가 덮어쓰기 되나
- **왜 서브에이전트가 노드가 아니라 툴인가**

**함정 (반드시 직접 재현해볼 것)**
서브에이전트를 **노드로** 만들어 보고, 부모 `messages`가 오염되는 것을 눈으로 확인한다.
그 다음 툴로 바꿔서 ToolMessage 1개만 남는 것을 확인한다.
**이 대조를 해보지 않으면 나중에 같은 실수를 한다.**

**설명 체크:** "서브에이전트를 쓰는 이유가 병렬처리가 아니라면 무엇인가?"

---

### Phase B — SDD 파이프라인 · 게이트

**목표:** 사람 자료 → 질문 → 답 → 검수까지 문서가 갈라져 나온다.

| 할 일 | 완료 기준 |
|-------|-----------|
| 스킬 `SKILL.md` 9종 | 프롬프트가 파일로 존재 |
| `spec-write` + 표기 4종 | `[스펙 결정]`이 스펙에 찍힌다 |
| `openspec` ∥ `spec-kit` 병렬 spawn | 질문지 2개 생성 |
| `answer-triage` 4갈래 | 사람 질문만 남는다 |
| **`wait_human` → `interrupt()`** | 그래프가 멈춘다 |
| `POST /answers` → `Command(resume=…)` | 같은 thread에서 이어진다 |
| `ready-audit` + `G_READY` | 1절이 비어야 통과 |
| verify 6항 스크립트 | `verify_passed` 토글 |

**배우기**

- `interrupt()`는 **프로세스를 띄워두는 게 아니다** — 저장하고 죽어도 된다
- 게이트 거부가 **ToolMessage**로 돌아가 Main이 재계획하는 흐름
- 단계 2(비어 있는 것)와 6(근거 없는 것)이 **겨누는 방향이 반대**인 이유

**설명 체크:** "답변 기록(4단계)을 건너뛰면 ready 검수에서 무슨 일이 나나?"

---

### Phase C — Postgres checkpointer · 압축

**목표:** 프로세스를 죽였다 켜도 같은 `thread_id`로 이어진다.

| 할 일 | 완료 기준 |
|-------|-----------|
| Postgres 기동 (Compose) | 연결 성공 |
| `InMemorySaver` → `PostgresSaver` 교체 | 코드 한 줄 수준으로 바뀌는 것 확인 |
| **프로세스 kill 후 재개** | `waiting_human` 상태가 살아 있다 |
| SDD 메타 테이블 (`runs`/`events`/`spawns`) | 감사 조회 가능 |
| `compaction.py` | 긴 런에서 컨텍스트가 안 터진다 |
| 압축 후에도 todos·spec_hash 보존 | 테스트 green |

**배우기**

- **thread 단위 로드** — 전체를 메모리에 올리지 않는다
- checkpoint와 SDD 메타는 **다른 테이블**이다 (섞지 않는다)
- 멱등: `spawn_id` / `event_id` unique
- 압축에서 **무엇을 버리고 무엇을 남기는가**

**설명 체크:** "`waiting_human`인데 서버가 죽으면 뭐가 살아 있어야 답이 이어지나?"

---

### Phase D — 구현 · QA · **실패 루프**

**목표:** 문서가 실제 코드가 되고, 검증되고, **실패했을 때 올바른 곳으로 되돌아간다.**

| 할 일 | 완료 기준 |
|-------|-----------|
| `implement` → **실제 디스크** | 브랜치/patch 생성 |
| 하이브리드 FS 경계 확인 | 코드가 State에 안 들어간다 |
| 구현 순서 (백엔드 계약 → 저장 → 화면) | 응답 타입이 먼저 확정된다 |
| `qa` — 조항↔증거 매핑 (`ac_ref`) | `qa-report.md` |
| **Playwright E2E** | 트레이스·스샷이 **경로로만** State에 |
| **`e2e-triage` + 4종 라우팅** | `qa.failed` → 진단 → 분기 |
| **루프 가드 5종** | 무한 루프 없음 |
| `G_TEST_INTEGRITY` | 테스트 약화가 거부된다 |
| 토탈 보고 | `report.md` |

**배우기**

- 왜 **구현은 병렬로 쪼개지 않는가** (일관성 vs 탐색)
- QA 세션 분리가 왜 모델 id 문제가 아닌가
- **실패 원인 4종이 왜 대응이 다른가**
- 루프 상태는 LangGraph가, **루프 제어는 Main이**

**함정 (반드시 재현)**
**구멍 있는 스펙을 일부러 넣고** 한 바퀴 돌린다.
`spec_gap`으로 진단되어 **단계 3(재질문)으로 나가는지** 확인한다.
`impl_bug`로 오진되어 재구현 루프를 도는 것이 **가장 위험한 실패**다 —
AI가 추정으로 메우고, 그 추정이 테스트를 통과하면 **아무도 모른다.**

**설명 체크:** "E2E가 실패했는데 스펙으로 되돌아가야 하는 경우는 언제인가?"

---

### Phase E — Store · pgvector (장기 기억)

**목표:** 이번 run에서 배운 것이 **다음 run에 뜬다.**

| 할 일 | 완료 기준 |
|-------|-----------|
| `CREATE EXTENSION vector` | psql로 확인 |
| `memories` 테이블 + HNSW(1536d) | 인덱스 생성 |
| `PostgresStore` + embed 설정 | `store.search()` 동작 |
| `recall` / `remember` 툴 — **Main 전용** | 서브가 못 쓰는 것 테스트 |
| 회수 지점 4곳 연결 | 구멍찾기 전 과거 FAIL이 brief에 실린다 |
| 오염 방지 3종 | `evidence` 없으면 거부 · `confirmed`만 자동 · **answers 저장 금지** |

**배우기**

- **Checkpoint ≠ Store** — 같은 DB, 다른 질문
- 왜 `answers.md`를 Store에 넣으면 안 되나
- 임베딩 차원과 **인덱스 상한**(HNSW 2000d)의 관계

**설명 체크:** "과거 답변을 기억해두면 편한데, 왜 저장하지 않나?"

---

### Phase F — Compose → K8s → cloudflared

**목표:** 회사처럼 클러스터에 올린다. **새 비즈니스 로직 없이** D와 동일 구성.

| 할 일 | 완료 기준 |
|-------|-----------|
| Compose: api + worker + postgres | 로컬 E2E |
| 프로세스 분리 (HTTP ≠ 그래프 실행) | 둘 다 기동 |
| Deployment: api, worker | Pod Running |
| Service (+ port-forward) | `/health` |
| Secret: DB URL · API 키 · ORCH 토큰 | env 주입 |
| Postgres: Helm 또는 관리형 | 앱이 접속 |
| **cloudflared** | 외부에서 접속 |
| 롤링 업데이트 1회 | 무중단 이해 |

**배우기 (최소)**

- Pod / Deployment / Service / Secret / Namespace
- **빈 디렉터리에 state를 두지 않는다** — DB가 진실
- 리소스 requests/limits (spawn 폭주 대비)

**아직 안 함:** HPA · Istio · CRD · 멀티 클러스터

**설명 체크:** "K8s가 보관하는 건 설정·프로세스지, `verify_passed`가 아니다 — 왜인가?"

---

### Phase G — 운영 확장

- HPA (워커 복제)
- 관측: 로그 · 메트릭 · **spawn 비용**
- budget 캡
- 대용량 artifacts → MinIO/S3
- CI에서 게이트 테스트
- Store 리뷰 루틴 — 잘못된 기억 폐기 (사람이 1회 훑기)
- (이후) AWS / GCP 이전

---

## 4. 문서 관계

| 문서 | 역할 |
|------|------|
| [`../README.md`](../README.md) | 비전 · 정책 · 파이프라인 |
| [`SPEC.md`](SPEC.md) | **구현 계약** (State · 그래프 · 게이트) |
| [`concepts.md`](concepts.md) | 용어 사전 — 막히면 여기 |
| **본 PLAN** | 학습 순서 · 인프라 도입 |
| [`gates.md`](gates.md) | 게이트 상세 |
| [`deep-review.md`](deep-review.md) | SDD 업계 레퍼런스 |

---

## 5. 학습 루틴

매주 한 Phase만.

1. 관련 SPEC·concepts 절 다시 읽기 (30–60분)
2. 손·코드로 최소 동작
3. **장애 하나 재현** (프로세스 킬 / 중복 이벤트 / 격리 깨뜨려보기)
4. 자기 말로 5줄 회고를 `docs/journal/`에 기록

개념이 안 잡힌 채 YAML만 복사하지 않는다.

---

## 6. 비목표

- `deepagents` 패키지 도입
- 역할→모델 고정표
- **구현을 병렬 서브에이전트로 쪼개기**
- 처음부터 완벽한 RAG 플랫폼
- DB 없이 PVC에 state 파일만 두고 K8s 다중화

---

## 7. 다음 액션 (바로)

1. **Phase A** — `concepts.md` 정독 후 `AgentState` + 2노드 그래프
2. **격리 대조 실험** — 서브를 노드로 / 툴로 각각 만들어 부모 messages 비교
3. Compose에 **Postgres만** 먼저 올려 연결 확인 (C 예습)

---

## 변경 이력

| 버전 | 날짜 | 내용 |
|------|------|------|
| 0.1 | 2026-09-23 | 초안. 자체 tick · No LangGraph |
| 0.2 | 2026-09-23 | **LangGraph 채택.** Phase 재편(A 격리 → B SDD → C checkpointer → D Playwright → E K8s+cloudflared), pgvector 보류 명시, checkpoint와 SDD 메타 분리 |
| 0.3 | 2026-09-23 | **Store + pgvector 채택** — Phase E 신설(D 뒤에 둔 이유 명시), Phase F/G로 밀림. OpenAI 모델·임베딩 고정, openspec CLI 검증 |
