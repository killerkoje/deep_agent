# Gates — fail-closed

본 문서는 [`SPEC.md`](SPEC.md) §11의 구현 상세다.  
**게이트는 코드다.** 모델이 “PASS”라고 말해도 파일이/플래그가 없으면 통과가 아니다.

---

## 1. 공통

```text
class GateReject(Exception):
    code: str
    message: str
```

- spawn 직전·이벤트 수신 직후 호출.  
- 거부는 `audit.jsonl`에 `{type:"gate_reject", code, skill?, run_id}` 기록.  
- Main 컨텍스트에 최근 reject를 넣어 재계획하게 한다.

---

## 2. G_MODEL_KNOWN

**When:** 모든 `spawn`  
**Check:** `model in list_models()`  
**Fail:** `GateReject("G_MODEL_KNOWN")`

---

## 3. G_NO_IMPL_WITHOUT_VERIFY

**When:** `skill in {implement, qa}`  
**Check:** `state.verify_passed is True` AND `meta/verify-token` 존재 AND `meta/spec.sha256` == hash(artifacts/spec.md)  
**Fail:** `GateReject("G_NO_IMPL_WITHOUT_VERIFY")`

스펙이 바뀌었으면 hash 불일치 → verify_passed를 false로 내리고 거부.

---

## 4. G_ANSWERS_HUMAN

**When:** `POST /answers` 또는 `answers.ready`  
**Check:**

1. `questions.md`에 있던 각 `답:` 중, 제출본에서 **비어 있지 않은 답**만 허용 대상으로 본다.  
2. 제출 주체가 human API일 것 (worker spawn이 answers를 쓰면 거부).  
3. (권장) `답:` 값이 질문 추출 직후와 동일한지 — worker가 미리 채운 흔적 있으면 거부.

**MVP 최소 구현:**

- answers 파일에 `답:` 뒤에 내용이 있는 줄 ≥ 1  
- 해당 write 경로가 `human_answers` 엔드포인트일 것  
- `actor` 필드가 worker skill이면 거부

---

## 5. G_VERIFY_SIX

**When:** `spec-rereview` worker 성공 직후, 이벤트를 `passed`로 올리기 전  
**Writer:** `gates.verify_spec(run_id) -> passed|failed`

### 5.1 항목

| # | 이름 | MVP 검사 방법 |
|---|------|----------------|
| 1 | 인수조건 판별/계산/출처 | `spec.md`에 `### AC-` 또는 `AC-` 목록이 있고, 각 항목 아래 `판별:`/`산식:`/`출처:` 중 2개 이상 헤더/키 존재 (정규식) |
| 2 | 분기 조건 | 본문에 `또는`/`라면`이 있으면 같은 절에 `조건:` 또는 `branch:` 표기 요구 — 위반 리스트 출력 |
| 3 | 절 간 모순 | MVP: 수동 체크리스트 파일 `meta/human-crosscheck.ok` 존재 여부 **또는** TBD로 남기고 failed로 두는 정책 중 하나. **기본: 파일이 없으면 항목 5와 묶여 재질문 루프** |
| 4 | 추정 답 0 | `questions.md`/`answers.md`에 `답: (추정)` / `답: TODO-AI` 패턴 금지 |
| 5 | 잔여 차단 질문 0 | `questions.md`에 `차단: true` 또는 `[BLOCKING]` 중 미답 있으면 fail → 다시 question 루프 |
| 6 | 토큰 + hash | `sha256(spec.md)` 기록 + `meta/verify-token`에 `VERIFY_OK:{run_id}:{hash}` 기록 |

### 5.2 PASS 시

```text
state.verify_passed = True
state.spec_hash = hash
write meta/spec.sha256
write meta/verify-token
emit spec.verify.passed
```

### 5.3 FAIL 시

```text
state.verify_passed = False
delete meta/verify-token if exists
emit spec.verify.failed  (payload.reasons = [...])
```

---

## 6. G_SESSION_QA

**When:** spawn `qa`  
**Check:** 새 `spawn_id`. implement worker의 raw transcript path를 messages에 넣지 말 것.  
**Fail:** 구현이 implement summary 외 conversation을 주입하려 하면 `GateReject("G_SESSION_QA")`

---

## 7. G_BUDGET (optional MVP+)

**When:** spawn 전  
**Check:** `state.budget.max_usd` 없으면 skip. 있으면 `spent + estimate <= max`  
**Fail:** `GateReject("G_BUDGET")`

---

## 8. 테스트 목록 (필수)

| 테스트 | 기대 |
|--------|------|
| verify false + spawn implement | reject |
| hash mismatch after edit | reject + verify_passed false |
| worker posts answers | reject |
| unknown model | reject |
| verify six token missing | failed event |
| qa without new spawn_id | reject |

---

## 9. 구현 스케치 (Python)

```python
def allow_spawn(state: RunState, skill: str, model: str, catalog: set[str]) -> None:
    if model not in catalog:
        raise GateReject("G_MODEL_KNOWN", model)
    if skill in {"implement", "qa"} and not state.verify_passed:
        raise GateReject("G_NO_IMPL_WITHOUT_VERIFY", skill)
    if skill == "qa":
        # session separation enforced by SpawnExecutor, not by model id
        pass
```
