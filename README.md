# Workflow Orchestration Agent

> AI 에이전트 멀티플렉서 — 스펙 기반 개발(SDD) 파이프라인을 단계별로 오케스트레이션한다.

**Status:** Draft / Research  
**Repo:** [heh139811-droid/work_flow_ochestration_agent](https://github.com/heh139811-droid/work_flow_ochestration_agent)  
**Local clone:** `C:\Users\PC\Documents\work_flow_ochestration_agent`

---

## 1. 한 줄 요약

사람이 **요구사항 수집·미팅·스펙 초안**까지 책임지고,  
에이전트 오케스트레이터(메인 에이전트)가 **스펙 검증 → Tech Plan → 구현 → QA**를  
완료 이벤트 기반으로 자동 이어 붙인다.

사람이 빈 칸을 남긴 스펙을 “추측으로 메우지” 않고,  
**게이트(Gate)를 통과한 뒤에만** 다음 단계 에이전트를 깨운다.

---

## 2. 배경 — 지금 어떻게 일하고 있는가

실제 업무 흐름은 대략 아래와 같다.

| # | 단계 | 주체 | 산출물 |
|---|------|------|--------|
| 1 | 요구사항 받기 | 사람 | 원시 요구사항, 티켓, 슬랙/메일 |
| 2 | 요구사항 구체화 미팅 (필요성 검토) | 사람 | 미팅 녹음, 결정사항, 범위 컷 |
| 3 | 요구사항 + 녹음본 분석 | 사람 (+보조 AI) | 정리된 요구사항 / 갭 목록 |
| 4 | 필요 내용 스펙화 | 사람 (초안) | PRD / Spec 초안 (**빈 공간 허용**) |
| 5 | 스펙 검증 | 시니어 + AI | 검수 질문·답변·수정된 스펙 |
| 6 | 구현 | 개발자 + AI | 코드 / PR |
| 7 | QA | QA + AI | 인수 조건 검증, 버그 리포트 |

현재는 **1~3을 바탕으로 SDD 스펙을 직접 작성**하고,  
이후 **검증 → 구현 → QA**를 개발자 루틴으로 돌리고 있다.

문제는 명확하다.

1. 스펙 초안은 사람이 최대한 쓰지만 **빈 공간이 반드시 남는다.**
2. 빈 공간을 AI가 마음대로 채우면 SDD의 의미가 죽는다. (가짜 완료)
3. 검증·구현·QA를 매번 손으로 붙이면 **핸드오프 비용**이 커진다.
4. 단계마다 쓰는 툴(스펙 킷, 탐색, PRD 설문, 구현 에이전트)이 달라서 **멀티플렉서**가 필요하다.

이 레포는 그 멀티플렉서를 만든다.

---

## 3. 목표 아키텍처 — 메인 에이전트 = 멀티플렉서

```
                    ┌─────────────────────────────┐
                    │      Main Orchestrator      │
                    │   (상태머신 / 이벤트 버스)   │
                    └─────────────┬───────────────┘
                                  │
     ┌────────────┬───────────────┼───────────────┬────────────┐
     ▼            ▼               ▼               ▼            ▼
 ┌────────┐  ┌────────┐    ┌──────────┐    ┌────────┐   ┌────────┐
 │ Spec   │→ │ Verify │ →  │Tech Plan │ →  │  Impl  │ → │   QA   │
 │ Draft  │  │        │    │(스택고정)│    │        │   │        │
 └────────┘  └────────┘    └──────────┘    └────────┘   └────────┘
```

### 3.1 핵심 원칙

| 원칙 | 설명 |
|------|------|
| **사람 선행** | 1~3단계(요구·미팅·분석)와 스펙 **초안**은 사람이 소유한다. |
| **빈 칸은 게이트** | AI가 빈 칸을 추정으로 채우지 않는다. 질문으로 올리고, 사람 답변을 기다린다. |
| **완료 시 트리거** | 단계 `done` 이벤트가 다음 단계를 깨운다. 폴링이 아니라 완료 계약. |
| **단계별 전용 에이전트** | 검증 ≠ Tech Plan ≠ 구현 ≠ QA. 역할 분리. |
| **스펙이 진실** | 구현·QA는 최신 승인 스펙만 본다. 스택은 tech-plan이 진실. |
| **되돌리기 가능** | 검증 FAIL / 플랜 미승인 / QA FAIL 시 이전 게이트로 되돌린다. |

### 3.2 목표 자동화 구간 (1차 스코프)

사람이 하고 싶은 **1차 자동화 플로우**:

```
PRD/스펙 초안 작성 끝
        │
        ▼  (완료 트리거)
   스펙 검증 에이전트
        │  (질문 산출 → 사람 답변 → 스펙 반영)
        ▼  (검증 PASS 트리거)
   Tech Plan 에이전트   ← 프레임워크·스택·경계 고정
        │  (사람 승인 게이트)
        ▼  (plan.passed 트리거)
   구현 에이전트
        │
        ▼  (구현 완료 / PR ready 트리거)
   QA 에이전트
```

**의도적으로 1차에서 빼는 것**

- 미팅 일정 잡기, 녹음 전사 파이프라인 전부 자동화
- 요구사항 “필요성” 자체를 AI가 판단해 컷하는 것
- 프로덕션 배포 / 롤백 오케스트레이션

→ 1~3은 **입력 파이프**, 4는 **사람+보조**, **5 → Tech Plan → 6 → 7**을 오케스트레이션 핵심으로 둔다.

---

## 4. 왜 이렇게 구현하려 하는가

### 4.1 “한 에이전트가 전부”가 안 되는 이유

하나의 긴 컨텍스트 에이전트에  
`스펙 써 → 검증해 → 코드 짜 → QA 해`를 시키면:

- 검증 단계에서 **구현 편향**이 생긴다 (이미 짠 코드에 스펙을 맞추려 함)
- 빈 칸을 **그럴듯하게 메워** 가짜 완료가 난다
- 실패 시 **어느 게이트에서 깨졌는지** 추적하기 어렵다
- 툴/프롬프트/스킬셋이 단계마다 다른데 한 프롬프트에 억지로 합쳐진다

그래서 **멀티플렉서(라우터) + 단계 전용 워커** 구조가 맞다.

### 4.2 SDD를 지키는 이유

Spec-Driven Development의 가치는 “문서가 있다”가 아니라:

1. **착수 전 계약** (API, 권한, 인수조건)
2. **빈 칸을 드러내는 검수**
3. **구현·QA가 같은 계약으로 판정**

이미 CRM 지표 작업에서 Spec Kit / OpenSpec / AWS Requirements를  
시니어 검수 모드로 돌려 본 경험이 있다.  
공통 교훈:

- 질문은 AI가 뽑아도 된다
- **답은 사람이 채운다**
- “Recommended”는 보조일 뿐, 자동 확정이 아니다

오케스트레이터도 이 계약을 코드로 고정한다.

### 4.3 이벤트/게이트 기반인 이유

폴링(`매 N분마다 스펙 있나?`)보다:

- `spec.draft.ready`
- `spec.verify.questions_ready`
- `spec.verify.passed`
- `tech.plan.ready` / `tech.plan.passed`
- `impl.pr.ready`
- `qa.passed` / `qa.failed`

같은 **명시적 완료 이벤트**가:

- 재실행·멱등성
- 감사 로그
- 사람 승인 삽입점

을 만들기 쉽다.

---

## 5. 단계별 입출력 계약 (초안)

### Gate A — Spec Draft Ready

**입력 (사람)**

- 요구사항 정리본
- 미팅 노트 / 녹음 요약 (있으면)
- 스펙 초안 마크다운 (빈 칸/`TBD`/`답:` 허용)

**완료 조건**

- `SPEC_STATUS=draft_ready` 마킹
- 필수 섹션 골격 존재 (문제 / 범위 / 비범위 / 인수조건 자리)

**트리거** → Verify Agent

---

### Gate B — Spec Verify

**입력**

- draft 스펙
- (선택) 코드베이스 / 기존 API 사실

**구멍 taxonomy (찾을 것)**

- 인수조건에 판별·계산 규칙 없음
- `~라면` / `또는`에 분기 조건 없음
- 한 절이 구분하라 한 것을 다른 절이 뭉뚱그림
- `표시한다`만 있고 산출·출처 없음
- 화면은 정했는데 데이터 출처 없음

**묻지 말 것**

- 누가 사인·언제 MENU 부여 등 프로세스
- 이미 결정된 것 / 명시적 TBD 절
- 컴포넌트 구조·로딩 UI 등 구현 재량

**동작**

1. 모호성·차단 이슈만 질문으로 추출 (추정 답 금지; 선택지+추천안)
2. 질문마다 “답이 없으면 AI가 무엇을 잘못 만드는가” 한 줄
3. 사람 답변 대기 → 스펙 반영
4. 재스캔 + **사람 교차규칙 1패스** (툴 사각)
5. 아래 PASS 조건 전부 충족 시에만 통과

**Verify PASS (Impl 허용 — 전부 필수)**

1. 인수조건마다 판별/계산/출처가 문서에 있음  
2. 조건문·`또는`에 분기 조건이 있음  
3. 절 간 모순 0  
4. 추정 답 0 (“이미 결정”은 인용만)  
5. 사람 교차규칙 스캔 1회  
6. 승인 토큰 + **spec hash** 기록 (파일 존재 ≠ PASS)

상세·레퍼런스: [`docs/deep-review.md`](docs/deep-review.md)

**산출**

- 질문지 / 답변 반영 스펙 (`SPEC_STATUS=verified`)
- 변경 로그 + spec hash
- 이벤트 `spec.verify.passed`

**FAIL** → 사람 재작성 / 추가 미팅 요청  
**PASS 트리거** → Tech Plan Agent (Gate C)

> 제품 요구(무엇을/왜/인수)만 닫는다.  
> **프레임워크·라이브러리·폴더 구조는 여기서 강제하지 않는다** — 다음 게이트.
> 구현 중 새 구멍이 열리면 Impl이 메우지 말고 **이 게이트로 에스컬레이션** (living spec).

---

### Gate C — Tech Plan (구현 전에 스택 고정)

코딩 에이전트가 프레임워크를 **임의로 고르지 않게** 하는 게이트.  
스펙 Verify와 Implement 사이에 둔다.

**입력**

- verified 스펙
- 대상 레포 현황 (이미 React인지, 그린필드인지)
- (선택) 팀 표준 / ADR / `package.json`·기존 패턴

**반드시 정리·고정할 것 (계약)**

| 항목 | 예시 | 비고 |
|------|------|------|
| 런타임 / 언어 | Node 20, TypeScript 5 | 기존 레포면 “현행 유지”로 명시 |
| UI / 앱 프레임워크 | React 18 + Vite / Next App Router | **신규일 때만 선택지 제시** |
| 상태·데이터 | React Query, Zustand, 서버 컴포넌트만 등 | 기존 패턴 우선 |
| 스타일 / 디자인 시스템 | 기존 CSS 모듈, shadcn, 사내 테마 | 새 디자인 시스템 도입은 별도 ADR |
| API 경계 | REST 경로, OpenAPI, BFF 유무 | 스펙 API와 일치해야 함 |
| 테스트 스택 | Vitest, Playwright, RTL | QA 게이트가 같은 도구를 씀 |
| 패키지 경계 | monorepo 패키지, FE/BE 레포 분리 | Impl 워커 라우팅에 필요 |
| 비목표(구현 재량) | 로딩 스피너 디테일, 사소한 컴포넌트 쪼개기 | AI가 물어보지 말 것 / 지어내도 됨 |

**동작**

1. 기존 코드베이스면 → **스택 변경 제안 금지**, `plan = adopt_existing` + 사실만 문서화  
2. 그린필드 / 선택이 필요하면 → 후보 2~3개 + 트레이드오프 + **Recommended**  
3. 사람은 옵션을 고르거나 Recommended를 승인 (`답:` 공란 유지 규칙과 동일)  
4. 승인된 내용을 `tech-plan.md` (또는 Spec Kit `plan.md`)에 고정, 해시 기록

**산출**

- `TECH_PLAN_STATUS=passed`
- `tech-plan.md` + (선택) `tasks.md` 골격
- 이벤트 `tech.plan.passed` (spec hash + plan hash)

**FAIL / wait_human** → 스택 미결정이면 Impl 시작 금지  
**PASS 트리거** → Impl Agent

**왜 분리하나**

- 제품 스펙 검증에 “Next vs Vite?”를 섞으면 검수가 산만해진다.
- 반대로 코딩 중에 스택을 고르면 AI가 **익숙한 쪽으로 지어낸다.**
- Spec Kit의 `clarify → plan → tasks → implement`와 같은 자리: **Verify 다음 = Plan**.

---

### Gate D — Implement

**입력**

- verified 스펙 + **passed tech-plan** (둘 다 필수)
- 레포 / 브랜치 컨텍스트

**동작**

- tech-plan 밖의 스택 도입 금지 (새 프레임워크 “슬쩍” 추가 금지)
- 스펙 → (tasks 있으면 따라) → 구현 → PR
- 스펙 밖 기능 추가 금지
- 스펙/플랜과 충돌 시 멈추고 Verify 또는 Tech Plan으로 에스컬레이션

**산출**

- 브랜치 / PR
- `IMPL_STATUS=ready_for_qa`

**트리거** → QA Agent

**구현 재량 vs 금지 (한 줄)**

| AI가 알아서 해도 됨 | AI가 정하면 안 됨 |
|--------------------|-------------------|
| 컴포넌트 파일 쪼개기, 로딩 UI 디테일 | 프레임워크·상태관리·테스트러너 교체 |
| 기존 패턴 카피 | 스펙에 없는 API 계약 |
| 네이밍·폴더 미세 조정 (레포 컨벤션 안) | 권한·산식·인수조건 해석 |

---

### Gate E — QA

**입력**

- verified 스펙의 인수조건 (조항 ID)
- passed tech-plan의 테스트 스택
- PR / 빌드 산출물

**동작**

- 인수조건 조항 → 체크/테스트 **증거** 매핑 (화면 “괜찮아 보임”만으로 PASS 금지)
- 권한·필터·엣지 케이스 회귀
- FAIL 시 재현 절차 + 스펙 조항 링크
- Writer(Impl)와 분리된 컨텍스트에서 판정

**PASS** → 완료 / 머지 후보  
**FAIL** → Impl로 되돌리기 (스펙 구멍이면 Verify로)

---

## 6. 툴·스택 후보 (왜 고르려 하는지)

> 아래는 **확정 스펙이 아니라 초안 후보**. 실험 후 고정한다.

### 6.1 오케스트레이션 런타임

| 후보 | 역할 | 선택 이유 / 트레이드오프 |
|------|------|--------------------------|
| **Cursor Agent + Hooks / Skills** | 로컬 개발자 워크플로와 밀착 | 이미 Spec Kit / OpenSpec skill을 Cursor에서 돌린 경험. IDE 안에서 게이트 UX가 자연스러움. 단, 장기 서버 오케스트레이션엔 약함. |
| **GitHub Actions + Issues/PR events** | `draft ready` 라벨 → 검증 워크플로 | 감사 로그·권한·리뷰어 연동이 쉽고, 레포가 진실 저장소가 됨. 대화형 질문-답변 UX는 Issue 폼으로 보완 필요. |
| **Temporal / Inngest / Trigger.dev** | 장기 실행 워크플로, 재시도, 타이머 | 사람 답변 대기(며칠) 같은 **human-in-the-loop**에 강함. 인프라 비용·복잡도↑. |
| **커스텀 Node/TS 상태머신** | 가벼운 멀티플렉서 | 1차 MVP에 적합. 이벤트 스키마만 잘 정하면 나중에 Temporal로 이전 가능. |

**1차 제안:**  
로컬/개인 실험은 **Cursor Skills + 레포 내 상태 파일(YAML/JSON) + GitHub PR/Issue 트리거**.  
사람 대기·재시도가 커지면 **Inngest/Temporal**로 승격.

### 6.2 스펙 / SDD 툴 (이미 비교 실험한 축)

CRM 지표 건에서 같은 입력으로 돌려본 축:

| 툴 | 강점 | 이 오케스트레이터에서의 자리 |
|----|------|------------------------------|
| **GitHub Spec Kit** (`/speckit-clarify` 등) | 스펙 → clarify → plan → tasks 파이프가 뚜렷 | Verify 단계의 질문 추출 / 태스크 분해 |
| **OpenSpec** (`/opsx:explore` 등) | 탐색·change 단위, 코드와 대조하는 검수에 좋음 | Verify의 “차단 이슈만” 모드 |
| **AWS Requirements `/prd`** | PRD 설문·게이트 섹션이 강함 | Draft 보강 / 시니어 검수 설문 템플릿 |

**오케스트레이터 입장에서의 결정:**  
툴을 하나로 강제하지 않고, **메인 에이전트가 단계별로 어떤 skill/command를 호출할지 라우팅**한다.  
즉 툴은 플러그인, 오케스트레이터는 버스.

### 6.3 에이전트 실행면

| 후보 | 용도 |
|------|------|
| Cursor Agent (로컬) | 스펙 검수·구현·리뷰 — 개발자 옆자리 |
| Claude Code / Codex CLI | 헤드리스 구현·배치 작업 |
| GitHub Copilot Coding Agent | Issue→PR 자동화 실험 |
| 자체 워커 (SDK) | 이벤트 수신 후 샌드박스에서 실행 |

**1차:** Cursor 중심.  
**2차:** “검증 PASS면 자동으로 구현 브랜치 파기”를 CI/에이전트 SDK로 확장.

### 6.4 산출물 저장

```
/
├── README.md                 # 이 문서
├── docs/
│   ├── architecture.md       # 상태머신·이벤트 스키마
│   ├── gates.md              # 게이트 계약
│   └── adr/                  # 결정 기록
├── schemas/
│   ├── events.schema.json    # done 트리거 페이로드
│   └── run-state.schema.json # 파이프라인 런 상태
├── skills/                   # 단계별 에이전트 스킬 (또는 심볼릭 링크)
│   ├── verify/
│   ├── tech-plan/
│   ├── implement/
│   └── qa/
├── workflows/                # GH Actions / Inngest 정의
└── examples/
    └── sample-feature/       # 샘플 스펙 → 검증 → QA 데모
```

스펙 본체는 **대상 서비스 레포**에 두고,  
이 레포는 **오케스트레이션 정의·스킬·런타임**만 둔다.  
(멀티플렉서가 여러 프로덕트 레포를 바라보는 형태)

### 6.5 미팅/요구사항 입력 (나중)

| 후보 | 메모 |
|------|------|
| 녹음 전사 (Whisper / 사내 STT) | 3번 단계 보조. 1차 자동화 밖. |
| 요약 → 결정/미결 추출 | Spec Draft 입력을 풍부하게 함. |
| Linear / Jira / GitHub Issues | 요구사항 티켓을 Gate A 입력으로 |

---

## 7. 메인 에이전트 책임 (멀티플렉서 API 초안)

메인 에이전트는 **일을 직접 많이 하지 않는다.**  
라우팅·게이트·감사만 한다.

```text
capabilities:
  - register_run(feature_id, inputs)
  - on_event(event)
  - route(stage) -> worker_skill
  - enforce_gate(stage) -> pass | wait_human | fail
  - write_audit(run_id, event)
  - escalate(run_id, reason)
```

**하지 않는 것**

- 스펙 빈 칸 추정 작성
- 검증 없이 구현 시작
- QA 없이 머지 승인 가장

---

## 8. 이벤트 스키마 스케치

```json
{
  "event": "spec.verify.passed",
  "run_id": "run_20260909_crm_metrics",
  "feature_id": "crm-metrics",
  "spec_ref": "docs/crm-metrics-spec.md@sha",
  "actor": "verify-agent",
  "ts": "2026-09-09T01:00:00Z",
  "artifacts": {
    "questions": "docs/.../verify-questions.md",
    "answers": "docs/.../verify-answers.md"
  }
}
```

규칙:

- 모든 `*.passed`는 **스펙 해시**를 포함한다.  
  이후 스펙이 바뀌면 하위 단계 무효 → 재검증.
- `wait_human`은 타임아웃·리마인더만 두고, 답을 생성하지 않는다.

---

## 9. MVP 로드맵

### Phase 0 — 문서/계약 (지금)

- [x] 레포 생성
- [x] README 초안 (본 문서)
- [x] 심층 검토 [`docs/deep-review.md`](docs/deep-review.md)
- [ ] 게이트 계약 (`gates.md`)
- [ ] 이벤트 스키마 초안
- [ ] ADR: 왜 멀티 에이전트인가 / 왜 빈 칸 추정 금지인가

### Phase 1 — 수동 트리거 MVP (Verify 품질 우선)

- [ ] 구멍 taxonomy + REVIEW-PROMPT 규칙을 Verify skill에 고정
- [ ] OpenSpec 스캔 → Spec Kit 형식 닫기 라우팅 (수동)
- [ ] PASS 조건 fail-closed 검사 (`답:` 공란, hash, 승인 토큰)
- [ ] `SPEC_STATUS` / 라벨로 Gate A 표시
- [ ] Tech Plan skill → Impl → QA 순차 수동 실행

### Phase 2 — 완료 시 자동 트리거

- [ ] `verify.passed` → Tech Plan 워커 자동 기동
- [ ] `tech.plan.passed` → Impl 워커 자동 기동
- [ ] `impl.ready` → QA 워커 자동 기동
- [ ] FAIL 시 이전 게이트로 라우팅
- [ ] 감사 로그 + 런 대시보드(간단한 markdown/JSON이면 충분)

### Phase 3 — 입력 파이프 확장

- [ ] 미팅 녹음 → 요약 → Draft 보강 제안 (자동 확정 금지)
- [ ] 이슈 트래커 연동
- [ ] 다중 레포 라우팅 (프론트/백엔드 각각 Impl 워커)

---

## 10. 성공 기준 (이 프로젝트가 “됐다”고 말할 조건)

1. **빈 칸 추정 0건** — 검증 로그에 “에이전트가 답을 채운” 흔적이 없다.
2. **게이트 점프 불가** — verified 스펙·passed tech-plan 없이 Impl이 시작되지 않는다.
3. **스택 임의 선택 0건** — 코딩 중 새 프레임워크/상태관리 도입이 플랜 승인 없이 없다.
4. **한 번의 런으로 추적 가능** — `run_id`로 질문→답→스펙해시→플랜해시→PR→QA가 이어진다.
5. **툴 교체 가능** — Spec Kit ↔ OpenSpec 등을 라우팅 설정만으로 바꿀 수 있다.
6. **실제 업무 1건 통과** — 예: CRM 지표 같은 실스펙으로 E2E 데모.

---

## 11. 비목표 (Non-goals)

- AGI급 “요구사항만 주면 전부 알아서” 제품
- 사람 승인 없는 프로덕션 배포
- 모든 SDD 툴을 다시 만드는 것 (우리는 **오케스트레이션**이 제품)
- 미팅 필요성 판단의 완전 자동화

---

## 12. 리스크와 대응

| 리스크 | 대응 |
|--------|------|
| AI가 빈 칸을 메움 | 프롬프트/스키마에 `answers_must_be_human` 강제, CI에서 `답:` 비어있는지 검사 |
| 코딩 중 스택 슬쩍 변경 | Impl 전 tech-plan 필수, PR 체크에서 새 메이저 의존성 ↔ plan diff |
| 스펙 드리프트 | 이벤트에 spec hash, 변경 시 하위 단계 invalidate |
| 툴 종속 | 워커를 skill 어댑터로 추상화 |
| 사람 대기 병목 | `wait_human` + 리마인더, 타임아웃 시 escalate |
| 과자동화로 검수 스킵 | PASS = taxonomy 6항 전부. 질문 0개 ≠ PASS. 교차규칙 사람 패스 필수 |
| 프롬프트만으로 게이트 | hook/스크립트 fail-closed (라벨·스킬 문구만 믿지 않음) |

---

## 13. 바로 다음에 할 일

1. [`docs/deep-review.md`](docs/deep-review.md) 합의 여부 확인
2. `docs/gates.md` + `schemas/events.schema.json` 초안 (PASS 6항을 스키마로)
3. Phase 1: OpenSpec / Spec Kit을 **Verify Worker**로 감싸는 어댑터
4. 샘플 피처 하나(`examples/sample-feature`)로 수동 트리거 데모

---

## 14. 참고 — 현재 업무와의 매핑

| 업무 단계 | 오케스트레이터 위치 |
|-----------|-------------------|
| 1 요구사항 받기 | 입력 (외부) |
| 2 구체화 미팅 | 입력 (외부) |
| 3 녹음·요구 분석 | 입력 보조 (Phase 3) |
| 4 스펙화 | Gate A (사람 초안 + 선택적 Draft 보조) |
| 5 스펙 검증 | Gate B ★ 자동화 핵심 (제품 계약) |
| 5.5 Tech Plan | Gate C ★ 프레임워크·스택·테스트 고정 |
| 6 구현 | Gate D ★ tech-plan 준수 코딩 |
| 7 QA | Gate E ★ |

**한 문장으로:**  
사람은 현실을 스펙 초안으로 옮기고,  
메인 에이전트는 그 스펙이 **검증되고 · 스택이 고정되고 · 구현되고 · 검증된 구현인지**만 파이프로 잇는다.
)
