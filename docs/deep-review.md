# Deep Review — Spec-first 오케스트레이션

작성일: 2026-09-09  
대상: 이 레포의 핵심 테제 (*스펙을 빈틈없이 보강·검증한 뒤 개발·QA*)

---

## 1. 판결

방향은 업계 Spec-first / Spec-Driven Development와 일치한다.  
다만 **“빈틈없이”** 를 문서 완전성으로 잡으면 실패하고, **AI가 지어낼 구멍을 막는 계약 완전성**으로 잡으면 성공한다.

품질을 가장 싸게 사는 지점은 코드 리뷰가 아니라 **착수 전 Verify 게이트**다.  
오케스트레이터(멀티플렉서)보다 **Verify 품질·fail-closed 게이트**에 투자하는 편이 ROI가 크다.

---

## 2. “빈틈없이” 운영 정의

| 잘못된 정의 | 올바른 정의 |
|-------------|-------------|
| 사인·MENU 부여 시점·호스팅 예산까지 닫기 | 인수조건마다 **판별·산식·출처**가 있음 |
| 모든 NFR·프로세스 질문 닫기 | `또는` / `~라면`에 **분기 조건**이 있음 |
| UI 컴포넌트 구조까지 고정 | **표시 vs 산출**, **화면 vs 데이터 출처** 연결 |
| 질문 개수 = 성공 | “답이 없으면 AI가 **무엇을 잘못 만드는가**”가 적히는 질문만 |

CRM 지표 실험(`crm_frontend/docs/spec-answer-test/REVIEW-PROMPT.md`) 실측:

- 세 툴이 겹친 질문 = 강한 신호
- 툴이 전부 놓친 층 = **두 규칙이 만나는 교차점** → 사람 1패스 필수
- “시니어 검수” 프레이밍 → 프로세스 질문 소음 (aws-prd)

---

## 3. Verify PASS (Impl 허용 조건)

아래를 **전부** 만족할 때만 `spec.verify.passed` 발행:

1. 인수조건마다 판별/계산/출처가 문서에 있다  
2. 조건문·`또는`에 분기 조건이 있다  
3. 절 간 모순(구분하라 vs 뭉뚱그림)이 0이다  
4. “이미 결정”은 인용만, 추정 답 0  
5. 사람 **교차규칙 스캔** 1회 (툴 사각)  
6. 승인 토큰 + **spec hash** 기록 (파일 존재 ≠ PASS)

구현 중 새 구멍이 열리면 Impl이 조용히 메우지 말고 Verify로 에스컬레이션 → 스펙 갱신 → 하위 단계 invalidate.  
스펙은 **한 번 얼린 백과사전**이 아니라 **living contract**다.

---

## 4. 업계 레퍼런스 맵

| 레퍼런스 | 가져올 것 | URL |
|----------|-----------|-----|
| GitHub Spec Kit | clarify → plan → tasks → implement 아티팩트 체인 | https://github.github.com/spec-kit/ |
| OpenSpec OPSX | brownfield delta, explore, **verify(구현↔아티팩트)** | https://openspec.dev/docs/opsx |
| Addy Osmani | Plan Mode로 코딩 잠금, 스펙=실행 아티팩트 | https://addyosmani.com/blog/good-spec/ |
| aiArch Spec-first | What/How 분리, **프롬프트 게이트는 깨짐 → hook fail-closed** | https://aiarch.dev/workflows/spec-first-development |
| MAQA | Coordinator → Feature → QA, CI green 전 In Review 금지 | https://github.com/GenieRobot/spec-kit-maqa-ext |
| prd-pipeline | 라이브 코드 심볼 대조(CoVe), brownfield 환각 차단 | https://github.com/rashee1997/prd-pipeline |
| pi-sdd-kit | `.status` 토큰만 게이트 (파일 존재 ≠ 승인) | https://github.com/felipefontoura/pi-sdd-kit |
| Amazon Working Backwards | 고객 결과 역산, FAQ로 hard question을 코드 전에 노출 | https://workingbackwards.com/concepts/working-backwards-pr-faq-process/ |

Brownfield(기존 CRM 등): OpenSpec으로 구멍 스캔 → Spec Kit 형식(선택지+추천)으로 답을 닫는 조합이 실측과 맞다.  
비교: https://codemyspec.com/blog/openspec-vs-spec-kit

---

## 5. 이 레포 설계에 대한 코멘트

캐노니컬 파이프라인은 README §1 기준:

`PRD(사람) → SPEC(AI+보일러) → 질문툴(AI) → 답(사람) → 재검토(AI) → Impl(AI) → QA(AI) → 메인 토탈 보고`

별도 Tech Plan 게이트는 두지 않는다. 스택은 기존 레포 컨벤션 / SPEC·PRD에 이미 고정.

**유지**

- 질문 추출 · 사람 답 · 재검토 분리 (3→4→5)  
- 빈 칸 사람 답변, AI 추정 금지  
- Writer ≠ Reviewer (QA 별 워커)  
- 메인 = 라우팅 + 최종 보고

**투자 우선**

1. 구멍 taxonomy + REVIEW-PROMPT + fail-closed PASS  
2. 질문지 md 계약  
3. QA 조항↔증거 + 토탈 보고  
4. 이벤트 버스는 그 다음

---

## 6. 권장 Verify 툴 라우팅 (실무)

1. **OpenSpec explore** — 산식·계약 구멍 스캔  
2. **Spec Kit clarify 형식** — 선택지+추천으로 그 자리에서 닫기  
3. **aws-prd** — 그린필드 첫 PRD에만 (기존 앱 얹기에는 비추)  
4. **사람** — 교차규칙 1패스  
5. (선택) **코드 CoVe** — 스펙이 인용한 심볼/경로가 레포에 실재하는지

프롬프트 핵심 한 줄:  
*구현자는 AI다. 모르면 묻고, 지어내지 마라. 질문마다 “답이 없으면 AI가 무엇을 잘못 만드는가”를 붙여라.*
