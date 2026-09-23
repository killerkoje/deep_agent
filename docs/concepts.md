# Concepts — 개념 사전

**문서 목적:** 용어가 헷갈릴 때 여기부터 본다.
**상태:** v0.4 · 2026-09-23
**원칙:** 개념이 안 잡힌 채 YAML·코드를 복사하지 않는다.

---

## 0. 한 장 요약

```
                    Deep Agent  (하네스 — 우리가 만든다)
                         │
        ┌────────────┬───┴────┬────────────┐
        ▼            ▼        ▼            ▼
    Planning    Subagents  Filesystem   Harness
     todos       spawn툴     files     루프·게이트·압축
        │            │        │            │
        └────────────┴───┬────┴────────────┘
                         ▼
                    LangGraph  (실행 기반 — 가져다 쓴다)
                         │
            ┌────────┬───┴────┬─────────┬──────────┐
            ▼        ▼        ▼         ▼          ▼
          State    Node     Edge   Checkpointer  Store
                                        │          │
                                   (단기·thread) (장기·namespace)
                                        └────┬─────┘
                                             ▼
                                        PostgreSQL
                                        (+ pgvector)
```

**경계선 하나만 기억하면 된다.**
LangGraph는 **실행 구조**를 준다. Deep Agent의 **네 기둥은 우리가 짠다.**

---

## 1. Workflow와 Agent

가장 먼저 잡아야 할 구분이다. 기준은 **LLM이 있느냐가 아니다.**

> **다음 행동을 누가 결정하는가.**

| | 다음 행동 결정 | 예 |
|--|---------------|-----|
| **Workflow** | 개발자가 미리 고정 | 조회 → 계산 → 설명 생성 → 저장 → 끝 |
| **Agent** | LLM이 결과를 보고 선택 | 조회 → "지역별도 봐야겠다" → 조회 → … |

LLM이 들어가 있어도 순서가 고정이면 Workflow다.

**이 레포에 적용하면** — 그래프 노드를 단계별로 쪼개면(spec 노드 → impl 노드 → qa 노드)
그건 Workflow다. 우리는 **노드 2개(`main_agent` / `tools`)** 만 두고
다음 행동을 Main의 **툴 선택**으로 정한다. 그래야 Agent다.

---

## 2. Deep Agent

모델 이름이 아니다. **Agent harness**다.

Planning · Tool 사용 · Context 관리 · 파일 작업 · Sub-agent 위임 · 긴 작업 관리를
묶어서 제공하는 **고수준 구성**이다.

```
Deep Agent
    ↓  (네 기둥)
고수준 Agent 실행 구조
    ↓
LangGraph 실행 모델
    ↓
State / Node / Edge / Persistence
```

### 2.1 자율성의 범위

"알아서 한다"는 무제한이 아니다.

```
개발자가 제공          →   Agent가 결정
목표 / 툴 / 권한           다음 행동
State 구조 / 종료 조건     스킬 · 모델 · 타이밍
```

**제공된 목표·도구·권한·상태 안에서의 자율성.**
SDD 게이트가 그 울타리다.

---

## 3. LangGraph 4종

### 3.1 State

`성공/실패` 같은 단순 값이 아니다. **현재 실행에 필요한 전체 데이터 구조**다.

```python
class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    todos: list
    files: dict
    status: Literal["running", "waiting_human", "done", "error"]
```

Python **TypedDict**로 선언한다.

**`Annotated[list, add_messages]`가 무슨 뜻인가**
`add_messages`는 **리듀서(reducer)** 다. 노드가 `{"messages": [새메시지]}`를 반환하면
**덮어쓰지 않고 이어 붙인다.** 리듀서를 안 붙이면 덮어쓰기가 기본이다.

**리듀서는 직접 짜지 않는다.** `add_messages` / `MessagesState` 기본 제공분을 쓴다.

### 3.2 Node

State를 받아 작업하고 State 변경분을 반환하는 **실행 단위**.

> **Node ≠ Agent.** 노드 안에 단순 함수가 들어갈 수도, LLM 호출이 들어갈 수도 있다.

### 3.3 Edge

노드 사이 이동 관계.

| | 누가 정하나 |
|--|------------|
| 고정 Edge `A → B` | 개발자 |
| **Conditional Edge** | **현재 State를 보고 결정** |

에이전트 루프의 표준형:

```
LLM → 툴 호출 필요? ├ Yes → Tool → LLM
                    └ No  → END
```

### 3.4 Checkpointer

State 스냅샷을 **thread 단위로** 저장한다. 그래서 중단 후 이어갈 수 있다.

---

## 4. Thread / Checkpoint / Memory — 셋은 다르다

여기가 가장 많이 헷갈리는 지점이다.

| 개념 | 핵심 질문 | 성격 |
|------|-----------|------|
| **Thread** | 어느 작업/대화인가? | **식별자** |
| **Checkpoint** | 어디까지 했나? | **현재 작업의 기억** |
| **Memory** | 과거에 뭘 기억하나? | **여러 작업에 걸친 기억** |

### 4.1 Thread

```python
config = {"configurable": {"thread_id": "th_abc123"}}
graph.invoke(state, config)
```

> **`thread_id` 자체가 데이터를 저장하지 않는다.**
> ID는 식별자고, 실제 저장은 checkpointer가 한다.

**부르는 thread만 로드된다.** 전체를 메모리에 올리지 않는다.

### 4.2 Checkpoint

```
thread_id = th_abc123
 ├ Checkpoint 1 — 스펙 초안 전
 ├ Checkpoint 2 — 질문 추출 완료
 └ Checkpoint 3 — 사람 답변 대기 중   ← 여기서 프로세스가 죽어도 된다
```

쓰이는 곳: 대화 이어가기 · 장시간 작업 · 장애 복구 · **Human-in-the-loop** · 실행 재개

### 4.3 Long-term Memory (Store) — **범위 안** (v0.3)

Thread가 끝나도 남는 정보. **Checkpoint와 다른 저장소다.**

```
("org", org_id, "failures")     "AC에 산식 없이 '표시한다'만 쓰면 매번 구멍이 난다"
("org", org_id, "conventions")  "이 레포는 백엔드 계약을 먼저 확정한다"
("org", org_id, "verdicts")     "작업명세서는 제안이다. 결정권자 메일이 이긴다"
("user", user_id, "preferences") "보고는 표 위주로"
```

**왜 필요해졌나** — 이 파이프라인은 **같은 실수가 반복된다.**
구멍 taxonomy도, 권한 등급 판례도 run 하나를 넘어 재사용되어야 한다.
Checkpoint는 그걸 못 한다. thread가 끝나면 끝이기 때문이다.

**쓰기는 좁게 연다** ([`SPEC.md`](SPEC.md) §5.4.3)

| | 규칙 |
|--|------|
| 누가 | **Main만.** 서브에이전트는 못 쓴다 |
| 언제 | 단계 9(보고) 직후 |
| 무엇을 **안** 쓰나 | **`answers.md`의 사람 답변** |

> **사람 답변을 Store에 넣으면 안 되는 이유**
> 다음 run에서 과거 답을 **사람이 답한 것처럼** 재사용하면
> 「답은 사람이 한다」는 SDD 제약이 무력화된다.
> Store에 남기는 것은 「이런 구멍이 반복된다」는 **패턴**이지 **답**이 아니다.

**회수된 기억은 근거가 아니라 참고다.** `[스펙 결정]`으로 취급하며
`[답변]`으로 승격할 수 없다.

---

## 5. Deep Agent 네 기둥 — 왜 필요한가

### 5.1 Planning (`todos`)

계획을 **컨텍스트 밖(State 필드)** 에 두고, **매 턴 최근 컨텍스트에 재주입**한다.

**왜 재주입인가** — 시스템 프롬프트에 한 번 넣으면 대화가 길어질수록 멀어진다.
긴 런에서 **목표 표류(goal drift)** 가 나는 주된 원인이다.
매 턴 다시 읽히면 표류가 잡힌다. (recitation)

**부수 효과:** State 필드라 **압축해도 살아남는다.**

### 5.2 Subagents (`spawn` 툴)

**1차 목적은 병렬처리가 아니라 컨텍스트 격리다.**

서브가 자기 윈도우를 태워 탐색하고 **요약만** 돌려주므로,
Main 컨텍스트가 중간 산출물로 오염되지 않는다.

> ⚠️ **가장 흔한 실수**
> 서브에이전트를 **같은 그래프의 노드**로 만들면,
> `add_messages` 리듀서가 그 세션의 메시지를 **전부 부모에 병합한다.**
> 격리가 사라진다. **돌아가긴 하므로 한참 뒤에야 발견된다.**
>
> → **서브에이전트는 노드가 아니라 툴이다.** 툴 안에서 별도 그래프를 invoke한다.

### 5.3 Filesystem (`files` + 디스크)

컨텍스트 대신 **경로**를 기억한다. 컨텍스트는 유한하지만 파일시스템은 아니다.

**하이브리드인 이유** — State 안의 dict는 **git·pytest·Playwright가 읽지 못한다.**
반대로 코드를 State에 넣으면 **체크포인트가 매 스텝 비대해진다.**

| 대상 | 위치 |
|------|------|
| 문서 (spec·questions·answers·ready) | State `files` |
| 코드·테스트 산출물 | 실제 디스크 |

### 5.4 Harness

나머지 전부. 루프 · 게이트 · **압축** · 체크포인트 · 계측.

**압축은 부가기능이 아니다.** 긴 런에서 컨텍스트가 차면
요약하고 이어가야 하며, **무엇을 남기고 무엇을 버리는가**가 품질을 가른다.

---

## 6. Vector / pgvector — **채택** (v0.3)

Memory가 많아지면 전부 컨텍스트에 못 넣는다.
그래서 현재 상황과 **의미적으로 가까운 것만** 찾는다 — 그게 임베딩 + 벡터 검색이다.

| | 대상 | 목적 |
|--|------|------|
| **RAG** | 문서 | "우리 회사 환불 정책은?" |
| **Agent Memory** | 과거 기억 | "이 사용자 개발 환경에 맞춰" |

검색 기술은 비슷하지만 **대상과 목적이 다르다.** 우리가 쓰는 건 **후자**다.

**설정**

| 항목 | 값 | 이유 |
|------|-----|------|
| 임베딩 | `text-embedding-3-small` | $0.02/1M |
| 차원 | **1536** | pgvector **HNSW 인덱스 상한이 2000d**. 3072d는 인덱싱 불가 |
| 인덱스 | HNSW cosine | |

**임베딩 대상은 Store 레코드의 `text` 필드뿐이다.**

> **여전히 유효한 주의**
> **spec.md를 통째로 임베딩하지 않는다.** 스펙·코드 **본문 검색**은
> 임베딩보다 구조적 탐색(grep·앵커·줄링크)이 낫다.
> 벡터는 **Store 회수 전용**이다.

`PostgreSQL ≠ pgvector`. Postgres는 DB, pgvector는 **확장**이다.

### 6.1 한 DB 안의 두 저장소

```
PostgreSQL
 ├── checkpoints   ← LangGraph PostgresSaver가 만든다 (우리가 설계 안 함)
 │                    thread 단위 단기 상태
 └── memories      ← 우리가 설계한다 (store.py)
                      namespace 단위 장기 기억 + embedding
```

**둘을 섞지 않는다.** 질문이 다르기 때문이다 — "어디까지 했나" vs "뭘 배웠나".

---

## 7. 헷갈리지 말아야 할 것

| ① | **Thread ≠ Memory** | 실행 단위 vs 재사용할 정보 |
| ② | **Checkpoint ≠ Memory** | "어디까지 했지" vs "과거에 뭘 기억하지" |
| ③ | **PostgreSQL ≠ pgvector** | DB vs 벡터 확장 |
| ④ | **Vector Search ≠ Checkpoint 조회** | 의미 유사 검색 vs thread_id 정확 복구 |
| ⑤ | **Deep Agent ≠ LLM** | LLM + 상태 + 툴 + 루프 + 컨텍스트 관리 |
| ⑥ | **Node ≠ Agent** | 노드는 실행 단위일 뿐 |
| ⑦ | **LangGraph ≠ deepagents** | 실행 기반 vs 완성된 하네스 |

---

## 8. 이 레포의 용어 매핑

| 일반 (LangGraph) | 이 레포 | 비고 |
|------------------|---------|------|
| Graph 실행 | `main_agent ⇄ tools` 루프 | 노드 2개뿐 |
| State | `AgentState` | TypedDict |
| Node | `main_agent` / `tools` | **스킬은 노드가 아니다** |
| Conditional Edge | `tools_condition` | Main의 툴 선택이 실질 분기 |
| Thread | `thread_id` | MVP에선 `run_id`와 동일값 |
| Checkpoint | `PostgresSaver` | Phase 3 |
| interrupt / resume | `wait_human` 툴 / `POST /answers` | HITL |
| Subagent | `spawn` **툴** | 노드 아님 (§5.2) |
| Store / Memory | `PostgresStore` + `recall`/`remember` 툴 | **Main 전용.** 서브는 못 쓴다 |
| Vector search | pgvector HNSW 1536d | Store 회수 전용 |

---

## 9. 더 읽을 것

| 문서 | 내용 |
|------|------|
| [`../README.md`](../README.md) | 정책·비전·파이프라인 |
| [`SPEC.md`](SPEC.md) | 구현 계약 |
| [`gates.md`](gates.md) | 게이트 상세 |
| [`PLAN.md`](PLAN.md) | 학습·인프라 순서 |
| [`deep-review.md`](deep-review.md) | SDD 업계 레퍼런스 |
