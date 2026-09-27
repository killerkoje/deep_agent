# Deep Agent — SDD 오케스트레이터

> PRD를 던지면 **스펙 작성 → 구멍 찾기 → 결정 → 권한 검수 → 구현 → E2E**까지 스스로 수행한다.
> 사람은 **자료만** 준다. 빈칸은 AI가 채우되 **근거를 남긴다.**
> 단 **권한·보안 / 돈·과금** 두 가지는 사람이 정한다 — 틀리면 되돌릴 수 없어서다.
> 구조는 **Planning + Subagents + Filesystem + Harness** 네 기둥 (+ 장기기억 Store)이다.
> **런타임:** Python 3.11+ · **LangGraph** · PostgreSQL (checkpointer + pgvector Store) · FastAPI

**Status:** Draft / Spec'd (v0.5 — 무정지 자율 + 권한·과금 정지)
**구현 계약:** [`docs/SPEC.md`](docs/SPEC.md) ← 개발은 이 문서 기준
**개념 사전:** [`docs/concepts.md`](docs/concepts.md) ← 용어가 헷갈리면 여기부터
**학습·도입 플랜:** [`docs/PLAN.md`](docs/PLAN.md) ← Deep Agent → Postgres → K8s
**게이트:** [`docs/gates.md`](docs/gates.md)
**Repo:** [killerkoje/deep_agent](https://github.com/killerkoje/deep_agent)

---

## 실행

```bash
uv sync
cp .env.example .env          # OPENAI_API_KEY 채우기

uv run uvicorn deep_agent.app:app --reload     # 서버
uv run pytest -q                                # 테스트 (API 키 불필요)
```

**CLI** — 서버에 붙는 얇은 클라이언트입니다. 그래프를 직접 import하지 않으므로
터미널에서 쓰는 경로와 서버가 도는 경로가 갈라지지 않습니다.

```bash
deep-agent run --feature crm docs/prd.md docs/mail.md
deep-agent status  <thread>
deep-agent gate    <thread>                       # 권한·과금 질문 확인
deep-agent gate    <thread> -a D-013="① 마스킹"    # 답하고 재개
deep-agent files   <thread> [spec.md]
deep-agent report  <thread>
```

> **Windows에서 `deep-agent`가 Application Control에 막히면**
> `uv run python -m deep_agent …` 로 동일하게 동작합니다.

`/health`가 두 가지를 알려줍니다 — `model_credentials`(키가 있나)와
**`durable`**(재시작해도 스레드가 살아남나). `durable: false`면 `DATABASE_URL`이
없다는 뜻이고, 그 상태에서는 "타임아웃 없는 `waiting_human`"이 성립하지 않습니다.

---

## 0. Deep Agent란 — 네 기둥

Deep Agent는 모델 이름이 아니라 **하네스(harness)** 다.
shallow 툴루프와 갈리는 지점은 다음 넷이며, 이 레포는 이 넷을 직접 구현한다.

| 기둥 | State 필드 | 하는 일 | 없으면 |
|------|-----------|---------|--------|
| **Planning** | `todos` | 목표를 쪼개 컨텍스트 **밖**에 두고 매 턴 재주입 | 긴 런에서 목표가 표류한다 |
| **Subagents** | (`spawn` 툴) | **컨텍스트 격리** — 서브가 자기 윈도우를 태우고 **요약만** 반환 | Main 컨텍스트가 중간 쓰레기로 오염된다 |
| **Filesystem** | `files` + 실제 디스크 | 컨텍스트 대신 **경로**를 기억 | 윈도우 한계가 곧 작업 한계가 된다 |
| **Harness** | 그래프 전체 | 루프 · 게이트 · 압축 · 체크포인트 · 계측 | 나머지 셋이 돌아갈 자리가 없다 |

여기에 **Memory(Store)** 가 하나 더 붙는다 — 네 기둥이 한 run 안의 문제를 푼다면,
Store는 **run을 넘는** 문제를 푼다. 같은 구멍·같은 판례가 매번 반복되기 때문이다.
Checkpoint("어디까지 했나")와 **다른 저장소**다. → [`docs/concepts.md`](docs/concepts.md) §4

**Deep이 아닌 것:** 사람이 "Docs=Opus, Impl=Sol" 표를 정해 두고 모델만 교체하는 것.
**Deep인 것:** Main이 상태를 읽고 **어떤 작업(스킬)을, 어떤 모델로, 지금** 돌릴지 **스스로 정해** 서브 세션을 띄우는 것.

### 0.1 자율성의 정확한 범위

Deep Agent가 "알아서 한다"는 말은 무제한이라는 뜻이 아니다.

```
개발자가 제공          →   Agent가 결정
─────────────────          ─────────────
목표 (PRD)                 다음 행동
사용 가능한 툴             어떤 스킬을
권한·게이트                어떤 모델로
State 구조                 지금 돌릴지
종료 조건                  실패 시 재계획
```

즉 **제공된 목표·도구·권한·상태 안에서의 자율성**이다.

### 0.2 빈칸은 AI가 채운다 — 단 두 가지만 빼고

**기본은 무정지다.** 스펙에 빈칸이 있으면 AI가 결정하고 진행한다.
사람에게 물어보려고 파이프라인을 세우지 않는다.

**근거:** 실측에서 질문 51건 중 **44건**이 코드 확인 / 이미 정한 것에서 유도 /
기술 판단으로 풀렸다. 사람이 꼭 필요했던 건 **4건**이다.
86%를 위해 전체를 멈추는 것은 자율 에이전트가 아니다.

**대신 절대 양보하지 않는 것: 추정을 추정이라고 표시한다.**

```
[AI 결정] 신규 고객 = 이번 달 첫 구매한 고객
          근거: 3쪽 "구매 이력 기준"에서 유도
          대안: 가입 기준 (채택 안 함 — 7쪽 집계와 불일치)
          확신도: 중
```

메우는 건 괜찮다. **메운 걸 사실인 것처럼 적으면** 6개월 뒤에
아무도 누가 정했는지 모른다. 근거·대안·확신도가 없는 결정은 코드가 거부한다.

### 0.3 무조건 멈추는 두 가지

| 종류 | 왜 | 틀리면 |
|------|-----|--------|
| **권한 · 보안** | 조회 범위 · 역할 · 노출 대상 | 데이터가 **이미 샌 뒤**다 |
| **돈 · 과금** | 금액 · 정산 · 할인 · 수수료 | 정산이 **이미 나간 뒤**다 |

나머지(화면·용어·집계 방식)는 틀려도 코드를 고치면 된다. 이 둘은 안 된다.

**정지 판단을 AI가 풀 수 없다 — 이중 검사**

```
① AI가 결정마다 분류를 단다        ("권한" / "과금" / "일반")
② 게이트가 독립적으로 다시 스캔한다  (스펙 절 위치 + 키워드)
   → 둘 중 하나라도 걸리면 정지
```

AI는 정지를 **걸 수는 있어도 풀 수 없다.** 오탐으로 불필요하게 멈추는 건 감수한다.
반대 방향 실수가 훨씬 비싸다.

**정지 방식**

- **런당 한 번, 모아서** 묻는다. 5번 따로 멈추면 못 쓴다
- 파이프라인에서 **가장 늦은 지점**(권한 검수 뒤)에 둔다 — 그래야 전부 모인다
- `interrupt()` → 체크포인트 저장 → 프로세스 죽어도 됨
- **타임아웃 없음.** 답이 올 때까지 진행하지 않는다

### 0.4 모델 정책 — 고정표 없음

- **역할→모델 배치표를 두지 않는다.** 실험 정책으로도 두지 않는다.
- Main은 가용 모델 목록 안에서 **비용·난이도·실패 이력**을 보고 매번 고른다.
- 같은 스킬이라도 턴마다 다른 모델을 쓸 수 있다.
- 감사 로그에 `skill + model + brief`를 남긴다.

`schemas/models.available.schema.json`이 `default_for_skill` / `role` / `skills` 필드를
**스키마 차원에서 금지**한다. 표를 만들 수 없게 막아둔 것이다.

---

## 1. 아키텍처 — LangGraph 위에 직접 짠 하네스

### 1.1 무엇을 쓰고 무엇을 안 쓰는가

| | 정체 | 결정 | 이유 |
|--|------|------|------|
| **LangGraph** | 실행 프레임워크 (State / Node / Edge / Checkpointer) | **사용** | thread_id 재개 · HITL interrupt · Postgres 영속화를 직접 짤 이유가 없다 |
| **`deepagents` 패키지** | 완성된 Deep Agent 하네스 | **미사용** | 배우려는 네 기둥이 통째로 블랙박스가 된다. 소스는 레퍼런스로만 읽는다 |
| Temporal / Inngest | 워크플로 엔진 | 미사용 | checkpointer로 충분하다 |

**이식성 조건:** 게이트와 스킬 로직은 LangGraph 타입에 의존하지 않는 **순수 함수**로 짜고,
노드·툴에서 호출만 한다. 그래야 엔진을 갈아도 핵심이 남는다.

### 1.2 그래프 모양 — 단순 루프 하나

```
START → main_agent (LLM + tools) → 조건부 엣지
                                     ├ 툴 호출 있음 → tools → main_agent (반복)
                                     └ 없음        → END
```

**노드를 단계별로 쪼개지 않는다.** (spec 노드 → impl 노드 → qa 노드 …)
그건 워크플로지 에이전트가 아니다. **다음 행동은 Main이 툴 선택으로 정한다.**

### 1.3 서브에이전트는 노드가 아니라 툴이다

`messages`에 `add_messages` 리듀서가 붙어 있으므로,
서브에이전트를 **같은 그래프의 노드**로 두면 그 세션의 중간 메시지가
**전부 부모 `messages`에 병합된다.** 서브에이전트를 쓰는 유일한 이유인
**컨텍스트 격리가 그 순간 사라진다.**

올바른 형태:

```
spawn 툴 내부에서
  ① 별도 compiled graph를 자체 state로 invoke
  ② 부모 messages는 넘기지 않는다 (brief 텍스트만)
  ③ 끝나면 요약 문자열 하나만 반환
  → 부모 messages에 남는 것은 ToolMessage 1개
```

**부수 효과 — 게이트가 우아해진다.**
`spawn` 툴 안에서 게이트를 검사하고, 거부되면 예외 대신
`GateReject: G_NO_IMPL_WITHOUT_VERIFY — verify_passed=false` 를 ToolMessage로 돌려준다.
Main이 그걸 읽고 **스스로 재계획**한다. fail-closed와 자율 재계획이 동시에 성립한다.

### 1.4 Filesystem — 하이브리드

| 대상 | 위치 | 이유 |
|------|------|------|
| spec.md · questions.md · decisions.md · ready.md · report.md | **State `files`** | 체크포인트에 같이 실린다. 작고, 에이전트만 읽는다 |
| 대상 레포 코드 · 테스트 산출물 | **실제 디스크** (`target_repo_path`) | git · pytest · **Playwright가 실행해야 한다** |

State 안의 dict는 외부 툴이 읽지 못한다.
반대로 **코드를 State에 넣으면 체크포인트가 매 스텝 비대해진다.** 절대 넣지 않는다.

### 1.5 Human-in-the-loop = interrupt + resume

사람 답을 기다리려고 프로세스를 며칠 띄워두지 않는다.

```
questions.ready
   → interrupt()        # 체크포인터가 저장, 프로세스는 죽어도 된다
   → (사람이 답변 POST)
   → Command(resume=…)  # 같은 thread_id에서 재개
```

**Postgres checkpointer를 쓰는 1순위 이유가 이것이다.** 장애 복구는 2순위다.

---

## 2. 파이프라인 — SDD 0~9

> Main이 보통 따르는 기본 경로. 매 전이의 **결정(스킬·모델)은 Main**이고,
> 표시된 게이트는 **코드**가 막는다.

| # | 단계 | 주체 | 산출물 | 게이트 |
|---|------|------|--------|--------|
| 0 | **자료 수집** | 사람 | PRD · 원본문서 · 메일 → `sources/` | |
| 1 | **스펙 초안** | spawn | `spec.md` (표기 4종) | |
| 2 | **구멍 찾기** | spawn **×2 병렬** | `questions.md` | |
| 3 | **결정** | spawn `decide` | **AI가 전부 답함** + 근거·대안·확신도 | `G_DECISION_LOGGED` |
| 4 | **스펙 반영** | spawn `spec-rereview` | 갱신된 `spec.md` | |
| 5 | **교차스캔** | spawn `crosscheck` | 절끼리 충돌 → 결정 또는 정지 후보 | |
| 6 | **권한 검수 (ready)** | spawn `ready-audit` | `ready.md` + 권한·과금 분류 | `G_VERIFY` · `G_READY` |
| 6b | **사람 정지** | **사람** | 권한·과금 답변만 | `G_HUMAN_GATE` · `G_ANSWERS_HUMAN` |
| 7 | **구현** | spawn (**단일**) | 브랜치 / patch | `G_NO_IMPL_WITHOUT_VERIFY` |
| 8 | **QA + E2E** | spawn (**세션 분리**) | `qa-report.md` + Playwright | `G_SESSION_QA` |
| 8b | **실패 진단** | spawn `e2e-triage` | `FailureAnalysis` | `G_TRIAGE_FIRST` · `G_LOOP` |
| 9 | **토탈 보고** | **Main** | `report.md` | |

```
[사람] 자료 수집 (PRD · 원본 · 메일)
   │ sources.ready
   ▼
[Main] spawn(spec-write)                     보일러플레이트 기반 초안
   │ spec.draft.ready
   ▼
[Main] spawn(openspec) ∥ spawn(spec-kit)     ← 병렬 · 독립 서브에이전트
   │ questions.ready
   ▼
[Main] spawn(decide)                         AI가 빈칸을 채움 + 근거 기록
   │ decisions.ready                          ← 멈추지 않는다
   ▼
[Main] spawn(spec-rereview)                  결정 반영
   │ spec.updated
   ▼
[Main] spawn(crosscheck)                     절끼리 충돌 스캔
   │ crosscheck.ready
   ▼
[Main] spawn(ready-audit)                    권한 검수 + 권한·과금 분류
   │ ready.audit.ready
   ▼
[게이트] 권한·과금 독립 재스캔 (AI 분류와 합집합)
   ├ 해당 없음 ──────────────────────► verify
   └ 있음 → interrupt() ──► [사람] 모아서 한 번에 답변
                              │ human.decided
                              ▼         (타임아웃 없음)
   ▼ spec.verify.passed
[Main] spawn(implement)                      백엔드계약 → 저장 → 화면
   │ impl.ready
   ▼
[Main] spawn(qa)                             조항↔증거 + Playwright E2E
   │ qa.passed ─────────────────────────► 토탈 보고
   │ qa.failed
   ▼
[Main] spawn(e2e-triage)                     ← 직접 재구현 금지
   │ triage.diagnosed
   ▼
   root_cause 4종 분기
   ├ impl_bug     → spawn(implement)   iteration++   (루프)
   ├ spec_gap     → verify_passed=false → 단계 3 재질문  ★루프 이탈
   ├ test_defect  → spawn(qa) 테스트만 수정
   └ environment  → 1회 재시도 → escalate
```

### 2.0 E2E 실패 정책 — 원인이 4종이라 대응도 4종이다

`qa.failed` → `implement` 재시도로 직행하는 **단순 루프가 가장 위험하다.**

| 원인 | 무엇 | 경로 | `iteration++` |
|------|------|------|--------------|
| `impl_bug` | 스펙은 맞는데 코드가 틀림 | 재구현 | **✓** |
| **`spec_gap`** | **구현자가 해석해야 했던 빈칸** | **스펙으로 복귀 · 재질문** | **✗ 이탈** |
| `test_defect` | 셀렉터·대기·flaky | 테스트만 수정 | ✗ |
| `environment` | 서버 미기동·DB 없음 | 재시도 1회 | ✗ |

> **`spec_gap`에서 재구현하면 안 되는 이유**
> AI가 빈칸을 **추정으로 메우고**, 그 추정이 테스트를 통과하는 순간
> **추정이 사실상의 스펙이 된다.** SDD 게이트 전체가 E2E 루프로 우회되는 경로다.

**루프 가드 5종** — ① 예산 3회 ② **같은 진단 2연속이면 진단이 틀린 것** ③ 루프 중 스펙 변경 ④ **통과 수 감소 = 회귀** ⑤ budget

**테스트 약화 금지** — `implement`는 테스트 파일을 못 건드린다. `fix_test`에서도 AC 매핑·테스트 수·assertion 수가 줄면 거부한다 (`G_TEST_INTEGRITY`).

상세: [`docs/SPEC.md`](docs/SPEC.md) §5.5

**AI 시작점 = 단계 1.** 사람은 **0(자료)** 과 **6b(권한·과금 답변)** 만 한다.
Main은 스펙/코드 **본문을 직접 쓰지 않고**, 세션을 띄워 시킨다.

### 2.1 단계 2가 병렬인 이유 — 그리고 7이 단일인 이유

업계 통념이 갈리는 지점이라 명시해 둔다.

| 작업 성격 | 형태 | 이 레포 |
|-----------|------|---------|
| 탐색·조사·리뷰 (읽기 중심, 결과가 **수렴**) | 병렬 서브에이전트 ○ | **단계 2** — openspec ∥ spec-kit |
| 구현 (쓰기 중심, **일관성** 필요) | 단일 스레드 권장 | **단계 7** — implement는 하나 |

병렬 서브에이전트가 **하나의 코드베이스**를 동시에 쓰면
서로 충돌하는 암묵적 결정을 내리고 합칠 때 일관성이 깨진다.
**구현은 쪼개지 않는다.**

### 2.2 단계 2 — 도구 둘을 다 쓰는 근거

| | 질문 | 좋은 질문 | 단독 발견 | 성격 |
|---|---|---|---|---|
| **openspec** | 21 | 19 (90%) | **5** | 절을 겹쳐 읽어야 나오는 **산식·계약 구멍** |
| **spec-kit** | 20 | 16 (80%) | 3 | **권한·역할 축**. 선택지+추천이라 그 자리에서 닫힌다 |

하나만 쓴다면 openspec. 다만 spec-kit 단독 발견 중 **데이터 모델을 바꾼 건**이 있었으므로 둘 다 돌린다.

### 2.3 단계 2와 6은 겨누는 방향이 반대다

- **2 (구멍 찾기)** — **비어 있는 것**을 찾는다
- **6 (권한 검수)** — **채워졌는데 근거 없는 것**을 찾는다

그래서 둘 다 필요하고, 순서가 이게 맞다.
그리고 **6은 반드시 4 뒤에 온다.** 답변 기록이 없으면 ready가 정당한 결정까지
전부 「무단 결정」으로 세어 오탐이 쏟아진다.

---

## 3. 표기 4종 — 권한 체계

**단계 6을 가능하게 하는 유일한 장치다.** 스펙의 모든 줄은 출처 등급을 갖는다.

| 표시 | 뜻 | 권한 | 빈도 |
|------|-----|------|------|
| `[코드]` | 현재 코드에서 확인한 사실 | code | |
| **`[AI 결정]`** | **AI가 정함** — 근거·대안·확신도 **필수** | 없음 — 검수 표적 | **대부분** |
| `[답변]` | 사람이 직접 정한 것 — **권한·과금만** | **user** | 런당 0~3건 |
| 표시 없음 | 출처 링크 원문에 근거 있음 | 원본 문서 | |

`[AI 결정]`에 근거·대안·확신도가 없으면 **코드가 거부한다** (`G_DECISION_LOGGED`).
메우는 건 괜찮다. **메운 걸 사실처럼 적는 것**이 문제다.

### 3.1 자료 권한 등급 — 자세함도 최신도 권위가 아니다

| 자료 | 권한 |
|------|------|
| 결정 기록 (`decisions.md`) 중 **사람 답** | **user** |
| 결정 기록 중 **`[AI 결정]`** | 없음 — 검수 표적 |
| 요구사항 중 **결정권자 발신** | **user** |
| 요구사항 중 **개발팀 발신** | — (선택지를 낸 쪽은 결정이 아니다) |
| 작업명세서·기획서 | spec(초안) |
| 코드 | code |
| **스펙** | **없음 — 검토 대상** |

> 실측: 나중에 나온 명세서가 "기준은 X"라 적었고 스펙이 따랐는데,
> 앞선 메일에서 결정권자가 이미 "Y로 한다"고 확정한 상태였다.
> **확정된 것을 미확정 제안으로 덮은 것이다.** 날짜만 보면 명세서가 최신이라 옳아 보인다.
> **등급표가 없으면 못 잡는다.**

### 3.2 기록하지 않은 결정은 없는 결정이다

**모든 결정은 `decisions.md`에 원문으로 남는다.** AI 결정이든 사람 답이든 같다.
스펙에는 표기와 링크만 달고, **정본은 `decisions.md`** 다.

기록을 빼먹으면 `ready-audit`이 그 결정을 **「근거 없는 단정」으로 센다.**

> 실측: ready가 잡은 「무단 결정」 6건 중 **3건이 기록 누락 때문에 오탐**이었다.

사람 답변을 없앴다고 기록까지 없애면 안 된다. **오히려 더 중요해진다** —
이제 결정의 대부분을 AI가 내리므로, 근거가 없으면 추적할 방법이 아예 사라진다.

---

## 4. Main ↔ Subagent

```
              ┌────────────────────────────┐
              │  Main (최상위 LLM)          │
              │  todos · spawn · 게이트     │
              │  압축 · 감사 · 토탈 보고    │
              │  ✗ 스펙·코드 본문 작성 금지 │
              └─────────────┬──────────────┘
                            │ spawn(skill, model, brief)  ← 툴 호출
   ┌──────────┬─────────────┼──────────┬──────────┬──────────┐
   ▼          ▼             ▼          ▼          ▼          ▼
spec-write  openspec ∥   answer-   spec-      ready-    implement → qa
            spec-kit     triage    rereview   audit
   └──────── 전부 독립 세션 · 요약만 반환 ────────┘
```

| | Main | Subagent |
|--|------|----------|
| 역할 | **스킬·모델·타이밍** 판단, 게이트, 압축, 감사, 보고 | 자기 스킬 산출물만 |
| 컨텍스트 | 요약 + todos + 최근 실패 | 자기 세션만 (부모 messages 없음) |
| 트리거 | 매 턴 스스로 | Main의 spawn |
| 종료 | 이벤트 수신 → 재판단 | **요약 반환. 다음 단계 직접 호출 금지** |
| 모델 | 매 spawn마다 Main이 지정 | 지정받은 모델로만 |

**Main에 주지 않는 툴:** `write_file`, `apply_patch`, `git_commit`
→ Main이 일을 가로채는 것을 **툴 부재로** 막는다.

---

## 5. State 스키마

```python
from typing import TypedDict, Annotated, Literal
from langgraph.graph.message import add_messages

class AgentState(TypedDict):
    # 대화
    messages: Annotated[list, add_messages]
    thread_id: str          # 세션 ID (user_id보다 이게 핵심)
    user_id: str

    # 딥에이전트 핵심
    todos: list             # 계획/진행 상태          ← Planning
    files: dict             # 작업공간 {path: content} ← Filesystem

    # 실행 제어
    status: Literal["running", "waiting_human", "done", "error"]
    next_action: str | None # plan | spawn | wait | finish 힌트

    # 구현·검증 루프
    implementation: dict    # files, changes, branch, spec_hash_at_impl
    e2e: dict               # status, tests[], screenshots[], traces[]  ← 경로만
    analysis: dict | None   # root_cause, route, rationale
    iteration: int          # impl_bug 재시도만 센다
    max_iterations: int     # 기본 3
```

**루프 상태는 LangGraph가, 루프 제어는 Main이.**

| | LangGraph | Main |
|--|-----------|------|
| 상태 (`e2e`·`analysis`·`iteration`) | **State 필드 ✓** 체크포인트에 실림 | |
| 다음 행동 | | **툴 선택 ✓** |

고정 엣지로 `implement → e2e → analyze → implement`를 배선하면
그건 Workflow지 Agent가 아니다 (§1.2).

- **리듀서는 직접 짜지 않는다.** `add_messages` / `MessagesState` 기본 제공분을 쓴다.
- `todos`는 **Main만** 쓴다. 서브는 `brief`를 받지 todo 목록을 받지 않는다.
- **매 턴 `todos`를 최근 컨텍스트에 재주입한다**(recitation). 시스템 프롬프트에 한 번 박는 것과 결과가 다르다.

SDD 전용 필드(`spec_hash`, `verify_passed`, `spawn_history` …)는 [`docs/SPEC.md`](docs/SPEC.md) §5 참조.

---

## 6. 이벤트

이벤트는 Main의 **입력 신호**다. 이벤트가 다음 워커를 직접 부르지 않는다.

| 이벤트 | 발행 | Main 동작 (전형) |
|--------|------|------------------|
| `sources.ready` | 사람 | spawn(spec-write) |
| `spec.draft.ready` | spec-write | spawn(openspec) ∥ spawn(spec-kit) |
| `questions.ready` | 구멍찾기 | spawn(`decide`) |
| `decisions.ready` | decide | spawn(spec-rereview) — **멈추지 않음** |
| `spec.updated` | spec-rereview | spawn(`crosscheck`) |
| `crosscheck.ready` | crosscheck | spawn(ready-audit) |
| `ready.audit.ready` | ready-audit | **게이트** 권한·과금 재스캔 |
| `human.gate.raised` | gate | **interrupt()** — 모아서 한 번 |
| `human.decided` | 사람 | verify 실행 |
| `spec.verify.passed` | 게이트 | spawn(implement) 허용 |
| `spec.verify.failed` | 게이트 | 재계획 / 재질문 루프 |
| `impl.ready` | implement | spawn(qa) — 새 세션 |
| `qa.passed` / `qa.failed` | qa | 재계획 또는 **토탈 보고** |
| `run.report.ready` | Main | 런 종료 |

페이로드에는 항상 `thread_id` + (해당 시) **spec hash**.
**스펙이 바뀌면 하위 단계를 invalidate한다.**

---

## 7. 게이트 — 코드다

모델이 "PASS"라고 말해도 **파일이/플래그가 없으면 통과가 아니다.**

| Gate | 언제 | 막는 것 |
|------|------|---------|
| `G_MODEL_KNOWN` | 모든 spawn | 카탈로그에 없는 모델 |
| **`G_DECISION_LOGGED`** | decide / rereview 산출물 | 근거·대안·확신도 없는 `[AI 결정]` |
| **`G_HUMAN_GATE`** | verify 직전 | 권한·과금 결정이 **미답인 채** 진행 |
| `G_ANSWERS_HUMAN` | 권한·과금 답변 수신 | **AI가 그 답을 채우는 것** |
| `G_VERIFY` | ready-audit 후 | 6항 미충족 스펙 |
| `G_READY` | implement 전 | ready 1절(남은 문제)이 안 비었는데 진행 |
| `G_NO_IMPL_WITHOUT_VERIFY` | spawn implement/qa | 미검증 스펙으로 구현 |
| `G_SESSION_QA` | spawn qa | implement 세션 이어받기 |
| `G_BUDGET` | spawn 전 (선택) | 비용 상한 초과 |

**Deep이 되어도 안 푸는 SDD 제약**

- **추정은 하되 추정이라고 표시** — 근거 없는 결정은 거부
- **권한·보안 / 돈·과금은 사람** — AI가 정지를 풀 수 없다
- `verify_passed` 없이 implement 불가
- QA ≠ Writer (세션 분리)
- 서브는 다음 단계를 직접 호출하지 않음 — **요약 반환만**

상세: [`docs/gates.md`](docs/gates.md)

---

## 8. 스택

| 항목 | 결정 |
|------|------|
| 언어 | Python 3.11+ |
| 실행 엔진 | **LangGraph** (State / Node / Edge / Checkpointer / Store) |
| API | FastAPI |
| **모델** | **OpenAI API.** Main = env `MAIN_MODEL` 고정, 서브 = 매 spawn Main이 선택 |
| 단기 상태 | **PostgreSQL** — `PostgresSaver`, **thread 단위** |
| **장기 기억** | **`PostgresStore` + pgvector** — thread를 넘어 재사용 |
| 임베딩 | `text-embedding-3-small` (1536d — HNSW 인덱스 상한에 맞음) |
| SDD 툴 | **openspec / spec-kit CLI** — scaffold · instructions · **validate** |
| E2E 테스트 | **Playwright** |
| 로컬 묶음 | Docker Compose (api + worker + postgres) |
| 배포 | **Kubernetes** + **cloudflared** (외부 접속) → 추후 AWS/GCP |
| 관측 | 트레이싱 + 토큰·비용 기록 (**Phase 1부터**) |
| **미사용** | `deepagents` 패키지 · Temporal / Inngest |

**버전 핀 고정.** LangGraph는 `interrupt` / `Command` / prebuilt 계열 API가 빠르게 변해왔다.
튜토리얼을 가져올 때 **그 버전 문서인지** 확인한다.

---

## 9. 로드맵

### Phase 0 — 문서 ✅

- [x] README / SPEC / gates / schemas / 보일러플레이트
- [x] **v0.2 — LangGraph 채택, 네 기둥 구조, 볼트 SDD 워크플로 흡수**
- [x] **v0.3 — Store(장기기억) + pgvector, openspec/spec-kit CLI, OpenAI 모델 고정**
- [x] **v0.4 — E2E 실패 정책 (원인 4종 → 경로 4종), 루프 가드 5종, `e2e-triage`**
- [x] **v0.5 — 무정지 자율 결정 + 권한·과금만 정지, `[AI 결정]` 표기, `crosscheck`**

### Phase 1 — 최소 루프 ✅

- [x] `AgentState` + main_agent 노드 + tools 노드 + 조건부 엣지 — **노드 2개**
- [x] `write_todos` 툴 + **매 턴 재주입**(recitation)
- [x] `spawn` 툴 — **별도 graph invoke, 요약만 반환**
- [x] 게이트 거부가 ToolMessage로 돌아가 Main이 재계획
- [x] 토큰·비용 계측

### Phase 2 — SDD 파이프라인 ✅

- [x] 스킬 계약 10종 (프롬프트는 아직 `skills_loader.py` 인라인 — 파일 분리는 미완)
- [x] `G_DECISION_LOGGED` — 근거가 **인용**이 아니면 거부
- [x] 권한·과금 **이중 검사** (AI 분류 ∪ 게이트 독립 스캔)
- [x] `interrupt()` 정지 + `Command(resume=…)` 재개
- [x] verify 6항 + `G_READY` + `G_OPENSPEC_VALID`
- [x] `openspec` / `specify` CLI 래퍼 (allowlist · `shell=False`)

### Phase 3 — 영속화 (단기) 🔶

- [x] `checkpoint.py` — `DATABASE_URL` 있으면 `PostgresSaver`, 없으면 `InMemorySaver`
- [x] thread 단위 저장·로드
- [ ] **Postgres로 실제 재시작 재개 검증** (지금은 in-memory로만 확인)
- [ ] 압축(compaction) — **문서엔 "하네스의 본체"인데 미구현**

### Phase 4 — 구현·QA·실패 루프 🔶

- [x] `e2e-triage` + **4종 라우팅** — `spec_gap`은 루프 이탈
- [x] 루프 가드 5종 (`G_LOOP`) + `G_TRIAGE_FIRST`
- [x] `G_TEST_INTEGRITY` — 테스트 약화로 통과 조작 차단
- [x] `record_e2e` — 트레이스·스샷은 **경로만**
- [x] 토탈 보고 (`report.md`) — 확신도 `하`를 맨 위로
- [ ] `implement` 실물 — 디스크 쓰기 · git 브랜치
- [ ] Playwright 실제 실행

### Phase 5 — 장기 기억 (Store) ⬜

- [ ] `PostgresStore` + `CREATE EXTENSION vector` / `memories` + HNSW
- [ ] `recall` / `remember` — **Main 전용**
- [ ] 회수 지점 4곳 · 오염 방지 3종

### Phase 6 — 배포 ⬜

- [ ] Docker Compose / K8s / cloudflared

---

## 9.1 지금 되는 것 · 안 되는 것

**되는 것** — 자료 투입부터 보고까지 한 바퀴. 게이트 13종. HTTP API 9개 + CLI.

```
sources → spec-write → openspec ∥ spec-kit → decide → spec-rereview
        → crosscheck → ready-audit → verify → [권한·과금 정지] → report
```

**안 되는 것**

| | |
|---|---|
| **실제 LLM 호출** | **0회.** 테스트 114개가 전부 스크립트 모델. `gpt-6-*`가 우리 툴 스키마를 제대로 부르는지 미검증 |
| 실제 코드 구현 | `implement`가 디스크에 못 씀 — 계약과 게이트만 있음 |
| Playwright | 미실행 |
| Postgres | 코드 경로는 있으나 실제로 안 붙여봄 |
| Store / 배포 | 미착수 |

**코드 ~2,400줄 · 테스트 114개 (API 키 불필요).**

### 검증된 것 중 중요한 셋

1. **격리** — 서브에이전트가 내부에서 9개 메시지를 쓸 때 Main에는 **ToolMessage 1개**만 들어옵니다. 일부러 노드로 만든 대조 실험도 테스트로 남겨뒀습니다(그쪽은 4개 전부 오염).
2. **정지를 AI가 못 풉니다** — 결정을 `category="일반"`으로 분류해도 게이트가 독립 스캔으로 잡아 `G_HUMAN_GATE`를 겁니다.
3. **`spec_gap`이 루프를 빠져나갑니다** — 재구현으로 돌리면 AI가 빈칸을 추정으로 메우고 그 추정이 테스트를 통과합니다. 대신 `verify_passed`를 내려 스펙 단계로 되돌립니다.

### 설계 주장이 실측으로 확인된 것

`max_iterations=3`은 상한이지 목표가 아니라고 문서에 적었는데, 예산 소진 테스트를 짜니 **가드 2(같은 진단 2연속)가 2회차에서 먼저 걸렸습니다.** 두 테스트를 다 남겼습니다.

---

## 10. 성공 기준

1. **모든 `[AI 결정]`에 근거·대안·확신도가 있다** — 없으면 코드가 거부
2. **권한·과금 결정이 미답이면 verify가 통과하지 않는다**
3. **AI가 자기 결정을 「일반」으로 분류해도 게이트가 독립적으로 잡는다**
4. `verify_passed` 없이 implement spawn을 **코드가 거부**
5. 2→3→4→5→6이 **파일로 분리**되어 추적 가능
4. ready 검수 **1절이 비어야** 구현으로 넘어간다
5. `thread_id`로 자료→질문→답변→스펙해시→구현→QA→보고 연결
6. **프로세스를 죽였다 켜도** 같은 `thread_id`로 이어진다
7. Main이 스펙/코드를 직접 쓰지 않고 **spawn만** 한다
8. 서브에이전트 messages가 **부모 컨텍스트에 병합되지 않는다**
9. **역할→모델 고정표가 코드/문서에 없다**
10. 실무 1건 E2E (Playwright 포함)

---

## 11. 비목표

- PRD까지 AI가 단독 확정
- **권한·과금을 AI가 단독 확정**
- 근거 없는 `[AI 결정]`을 통과시키는 것
- **구현을 병렬 서브에이전트로 쪼개기**
- `deepagents` 패키지 도입
- 역할별 모델 고정 배치표
- 처음부터 완벽한 RAG 플랫폼
- 배포/롤백 오케스트레이션

---

## 12. 리스크

| 리스크 | 대응 |
|--------|------|
| **서브 messages가 부모에 병합됨** | spawn은 **툴**로. 별도 graph invoke. 요약만 반환 |
| **체크포인트 비대화** | 코드는 State에 넣지 않는다 (하이브리드 FS) |
| **AI가 권한·과금까지 결정** | 게이트 **독립 재스캔** — AI 분류를 믿지 않는다 |
| **AI가 근거 없이 단정** | `G_DECISION_LOGGED` — 근거·대안·확신도 필수 |
| 결정을 기록 안 함 | `decisions.md`가 정본. 없으면 ready 오탐 폭증 |
| 정지가 너무 잦아 못 씀 | **런당 한 번, 가장 늦은 지점에서 모아서** |
| 스펙 드리프트 | spec hash + 하위 invalidate |
| 프롬프트만 게이트 | fail-closed 스크립트 |
| Main이 일을 가로챔 | write 툴을 **주지 않는다** |
| 긴 런에서 목표 표류 | todos **재주입**(recitation) |
| 컨텍스트 폭발 | 압축 전략 (Phase 3) |
| **Store 오염 — 틀린 기억이 다음 run을 망침** | `evidence` 필수 · `confirmed`(2회 재현)만 자동 주입 · org 격리 |
| **AI 결정을 사람 답인 척 재사용** | 권한·과금 **사람 답만** Store 저장 금지. `[AI 결정]`은 저장해 일관성 유지 |
| LLM이 셸을 임의 실행 | `sdd_cli.py` **allowlist** — openspec/specify/playwright/git만 |
| **E2E 실패를 추정으로 덮음** (`spec_gap` 오진) | `G_TRIAGE_FIRST` · triage 프롬프트에서 `impl_bug` 기본분류 금지 |
| **테스트를 약화시켜 통과** | `G_TEST_INTEGRITY` — AC매핑·테스트수·assertion수 감소 거부 |
| 재구현 무한 루프 | `G_LOOP` 5종 — 예산·같은진단 2연속·스펙변경·회귀·budget |
| 트레이스로 체크포인트 폭발 | 스크린샷·trace.zip은 **경로만** State에 |
| 모델 남발·비용 폭증 | budget 상한 + 계측. **배치표로 해결하지 않는다** |
| LangGraph API 변경 | 버전 핀 고정 · 게이트/스킬은 순수 함수로 |

---

## 13. 다음 할 일

1. [`docs/SPEC.md`](docs/SPEC.md) §14 **S1** — LangGraph 최소 루프
2. 스킬 `SKILL.md` 초안 (볼트 SDD 프롬프트 바인딩)
3. 게이트 단위 테스트
4. E2E 샘플 런 1건 (사람 답 1회 포함)
