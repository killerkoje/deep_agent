# Templates — SDD 자산

스킬이 런타임에 읽는 **작업 계약 원본**이다. 레포가 자립하도록 볼트에서 들여왔다.

**출처:** Obsidian 볼트 `wonjd/SDD/` (2026-09-23 복사)
볼트 쪽은 **학습 노트**이고, **여기가 런타임 진실**이다. 볼트를 고쳤으면 여기로 다시 가져온다.

| 파일 | 쓰는 스킬 | 역할 |
|------|-----------|------|
| [`spec-boilerplate.md`](spec-boilerplate.md) | `spec-write` | 스펙 뼈대. 표기 4종 · 세부태스크 5절(`처리·성공·실패·예외·출처`) |
| [`spec-review-boilerplate.md`](spec-review-boilerplate.md) | `openspec` · `spec-kit` | 구멍 찾기 REVIEW-PROMPT |
| [`ready-permission-checking-boilerplate.md`](ready-permission-checking-boilerplate.md) | `ready-audit` | 권한 검수 문서 틀 |
| [`sdd-workflow.md`](sdd-workflow.md) | (참조) | SDD 0~6 전체 흐름 · 실측 근거 |

---

## 핵심 규칙 세 줄

**표기 4종이 권한 검수를 가능하게 하는 유일한 장치다.**
`[코드]` (코드에서 확인) · `[답변]` (사람이 정함) · `[스펙 결정]` (문서가 처음 정함 — **검수 표적**) · 무표시 (출처 원문 근거)

**출처는 줄 단위로 건다.** `[라벨](파일.md#L숫자)` — 절 제목이 아니라 근거가 실제로 있는 줄.

**기록하지 않은 결정은 없는 결정이다.** 대화에서 정한 것을 `answers.md`에 남기지 않으면
`ready-audit`이 그것을 「무단 결정」으로 센다.

---

## 파이프라인 대응

| 볼트 SDD 단계 | 이 레포 단계 | 스킬 |
|---------------|-------------|------|
| 0 자료 모으기 | 0 | (사람) |
| 1 스펙 초안 | 1 | `spec-write` |
| 2 구멍 찾기 | 2 | `openspec` ∥ `spec-kit` |
| 3 답하기 (4갈래) | 3 | `answer-triage` |
| 4 기록하기 | 4 | (사람) — **빠뜨리기 쉽다** |
| 5 권한 검수 | 6 | `ready-audit` |
| 6 구현 | 7 | `implement` |
| — | 8 | `qa` + Playwright |

상세: [`../SPEC.md`](../SPEC.md) §10
