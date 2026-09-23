# Plan — 개념을 쌓으며 회사 규모까지

**문서 목적:** “무엇을 어떤 순서로 이해·구축할지” 로드맵.  
**구현 계약은** [`SPEC.md`](SPEC.md). 이 문서는 **학습·단계·기술 선택**이다.  
**상태:** Draft v0.1 · 2026-09-23  
**원칙:** 개념이 부족한 상태에서 한 방에 K8s+에이전트를 올리지 않는다. **이해하면서** 층을 올린다.

---

## 0. 한 줄 계획

> Deep Agent(자체 tick/spawn) → **PostgreSQL(+pgvector)에 run·대화 맥락 저장** → Compose로 서버 검증 → **K8s에 올리기**  
> LangGraph 안 씀. 상태·락·멱등은 **직접** 짠다.

---

## 1. 지금 부족한 것 (인정하고 순서 정함)

| 개념 | 왜 필요한가 | 이 플랜에서 다루는 단계 |
|------|-------------|------------------------|
| Deep Agent | Main이 스킬·모델을 고르는 루프 | Phase A |
| Run / 이벤트 / 게이트 | 파이프라인의 “상태머신” | Phase A |
| DB에 대화·상태 저장 | Pod 죽어도 이어짐, 회사 최소 조건 | Phase B |
| pgvector | 긴 맥락·스펙/로그 검색·유사 실패 회수 | Phase B·C |
| 프로세스 분리 (API≠Worker) | 스케일의 시작 | Phase C |
| K8s | 여러 노드·배포·Secret·재시작 | Phase D |

**하지 않는 착각:** K8s YAML만 짜면 Deep Agent가 된다.  
K8s는 **껍데기**. 본체는 Phase A·B다.

---

## 2. 기술 선택 (고정)

| 항목 | 선택 | 이유 |
|------|------|------|
| 언어·API | Python · FastAPI | SPEC과 동일 |
| 오케스트레이션 | 자체 `tick` / `spawn` | LangGraph **비사용** |
| DB | **PostgreSQL** | run 상태·이벤트·감사·대화 메시지 |
| 벡터 | **pgvector** (Postgres 확장) | 별도 벡터DB 없이 맥락 검색 |
| 로컬 묶음 | Docker Compose | api + worker + postgres |
| 회사 배포 | **Kubernetes** | Deployment / Service / Secret / (나중) HPA |
| 산출물 대용량 | 초기: DB bytea/텍스트 → 이후 MinIO/S3 | 처음부터 오브젝트스토리지 강제 안 함 |

### 2.1 Postgres에 넣을 것 (개념)

| 테이블(가칭) | 내용 |
|--------------|------|
| `runs` | RunState (status, verify_passed, spec_hash, …) |
| `events` | 이벤트 로그 (멱등 키 포함) |
| `spawns` | skill + model + brief + 결과 |
| `messages` | spawn/Main **대화 맥락** (role, content, token 메타) |
| `artifacts` | spec/questions 등 텍스트 또는 스토리지 포인터 |
| `embeddings` | pgvector — 메시지/스펙 청크 임베딩 (검색용) |

**대화 맥락** = LLM에 다시 넣을 messages 히스토리 + 요약.  
파일 `runs/`만으로는 다중 Pod에서 깨지므로 **Phase B부터 DB가 진실의 원천**.

### 2.2 pgvector를 쓰는 이유 (범위)

- “이 스펙과 비슷한 과거 FAIL” 검색  
- 긴 audit/로그에서 관련 구간 회수 → Main 컨텍스트 압축  
- **초기에는 필수가 아님.** Phase B는 테이블·messages 저장이 먼저, 임베딩은 B-후반/C.

---

## 3. 위상 (Phase) — 반드시 이 순서

```
A 개념+로컬 Deep Agent (디스크 state도 OK)
    ↓
B Postgres/pgvector — 상태·대화 맥락 DB화
    ↓
C API / Worker 분리 + Compose
    ↓
D Kubernetes 배포
    ↓
E 운영 (HPA, 관측, 비용 캡) — 회사 확장
```

각 Phase 끝에 **“내가 설명 가능한가?”** 체크를 둔다. 못하면 다음으로 가지 않는다.

---

### Phase A — Deep Agent 개념 · 최소 동작 (로컬)

**목표:** “파이프라인이 서버 프로세스의 tick이다”를 몸으로 이해.

| 할 일 | 완료 기준 |
|-------|-----------|
| [`SPEC.md`](SPEC.md) §1–§9 정독 | 용어를 자기 말로 설명 |
| FastAPI + `POST /runs` + state 파일 | run 생성 |
| 규칙 기반 또는 Main LLM 1회 tick | spawn 1회 성공 |
| 게이트  Stub (`verify` 없이 implement 거부) | 테스트 1개 green |

**배우기:**

- Main vs Worker  
- event vs 다음 단계 직접 호출 금지  
- 모델 배치표가 없는 이유  

**산출:** 로컬에서 PRD → (가짜라도) questions까지.

**설명 체크:** “Pod가 아니라 왜 프로세스가 파이프라인인가?”

---

### Phase B — PostgreSQL · 대화 맥락 저장

**목표:** 상태가 DB에 있어 프로세스 재시작 후에도 같은 `run_id`로 이어진다.

| 할 일 | 완료 기준 |
|-------|-----------|
| Postgres 기동 (Compose 또는 로컬) | 연결 성공 |
| `runs` / `events` / `spawns` / `messages` 마이그레이션 | 스키마 적용 |
| RunStore를 파일 → DB로 교체 | 재시작 후 status 유지 |
| spawn마다 messages append | 대화 맥락 조회 API 또는 SQL로 확인 |
| pgvector 확장 enable | `CREATE EXTENSION vector` |
| (후반) 스펙/메시지 청크 임베딩 1종 | 유사 검색 쿼리 1개 |

**배우기:**

- 트랜잭션 · `SELECT … FOR UPDATE` (같은 run 동시 tick 방지 예습)  
- 멱등: `event_id` / `spawn_id` unique  
- “컨텍스트 윈도우” vs “DB에 쌓인 전체 맥락” 차이 (요약 전략은 나중에)

**설명 체크:** “waiting_human인데 서버가 죽으면 뭐가 살아 있어야 답이 이어지나?”

---

### Phase C — 프로세스 분리 · Compose

**목표:** API Pod와 Worker가 나뉘어도 DB lock으로 run이 안전하다.

| 할 일 | 완료 기준 |
|-------|-----------|
| 프로세스: `api` (HTTP만) / `worker` (tick 루프) | 둘 다 Compose |
| Worker가 DB에서 `running` run lease | 동시 2 worker 테스트 |
| artifacts는 DB 또는 공유 볼륨 | E2E 1건 |

**배우기:**

- 왜 API에서 긴 spawn을 돌리면 스케일이 안 되나  
- lease / heartbeat 개념  

**설명 체크:** “워커 2대가 같은 run_id를 tick하면 무슨 일이 나고, 어떻게 막나?”

---

### Phase D — Kubernetes

**목표:** 회사처럼 클러스터에 올린다. **새 비즈니스 로직 없이** C와 동일한 구성.

| 할 일 | 완료 기준 |
|-------|-----------|
| Deployment: api, worker | Pod Running |
| Service + Ingress (또는 port-forward) | `/health` |
| Secret: DB URL, API 키, ORCH 토큰 | env로 주입 |
| Postgres: 관리형 or Helm or 별도 StatefulSet (학습용) | 앱이 접속 |
| 롤링 업데이트 1회 | 무중단 또는 짧은 끊김 이해 |

**배우기 (최소):**

- Pod / Deployment / Service / Secret / Namespace  
- 디스크: 빈Dir에 state 두지 않기 (DB가 진실)  
- 리소스 requests/limits (spawn 폭주 대비)

**아직 안 함 (E로 미룸):** HPA, Istio, 커스텀 CRD, 멀티 클러스터.

**설명 체크:** “K8s가 보관하는 건 설정·프로세스지, run의 verify_passed가 아니다.”

---

### Phase E — 회사 운영 확장 (이후)

- HPA (워커 복제)  
- 관측: 로그·메트릭·spawn 비용  
- budget 캡  
- MinIO/S3 artifacts  
- CI에서 게이트 테스트  
- (선택) 네임스페이스별 팀 격리  

---

## 4. 문서·코드와의 관계

| 문서 | 역할 |
|------|------|
| [`README.md`](../README.md) | 비전·정책 |
| [`SPEC.md`](SPEC.md) | 구현 계약 (API·tick·게이트) |
| **본 PLAN** | 학습·도입 순서 · DB/K8s |
| [`gates.md`](gates.md) | 게이트 상세 |

SPEC의 “MVP는 `runs/` 파일”은 **Phase A 학습용**으로 유지한다.  
Phase B 이후 **진실의 원천 = PostgreSQL** 로 업그레이드하고, SPEC §5/§15에 “DB store” 개정 노트를 남긴다 (구현 시).

---

## 5. 학습 루틴 (추천)

매주 한 Phase만. 체크리스트:

1. 관련 SPEC 절 다시 읽기 (30–60분)  
2. 손·코드로 최소 동작  
3. 장애 하나 재현 (서버 킬, 중복 이벤트, 워커 2개)  
4. 자기 말로 5줄 회고를 `docs/journal/` 또는 이슈에 기록  

개념이 안 잡힌 채 YAML만 복사하지 않는다.

---

## 6. 비목표 (이 플랜에서 명시적 제외)

- LangGraph / deepagents 패키지 도입  
- 역할→모델 고정표  
- 처음부터 완벽한 RAG 플랫폼  
- K8s 앞에서 DB 없는 “PVC에 state.json만” 다중화 (싸우기 나쁜 패턴)

---

## 7. 다음 액션 (바로)

1. Phase A: SPEC §2·§8 정독 + FastAPI skeleton  
2. Compose에 **Postgres + pgvector 이미지**만 먼저 올려 `psql`로 extension 확인 (B 예습)  
3. `messages` 테이블 초안 스케치를 SPEC 개정 전에 메모  

---

## 변경 이력

| 버전 | 날짜 | 내용 |
|------|------|------|
| 0.1 | 2026-09-23 | 초안. Deep Agent → Postgres/pgvector → Compose → K8s. 개념 우선 |
