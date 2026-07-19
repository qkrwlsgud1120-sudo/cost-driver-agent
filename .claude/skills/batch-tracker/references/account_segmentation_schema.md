# account_segmentation.json 스키마

Phase 0.5(공통/특정 대분류 분리)에서 생성되고, Phase 1 시작 전 반드시 확정되어야 하는 파일이다. `accounts_master.json`이 "부서별로 어떤 계정이 등장했는가"를 담는다면, 이 파일은 "그 대분류를 공통 원가동인 대상으로 묶을지, 부서별로 따로 볼지"에 대한 **확정된 판정**을 담는다.

**대분류 도입(§0) 이후 구조**: 공통/특정 판단 단위는 계정코드가 아니라 대분류(하이픈 앞부분 등으로 계정명에서 도출한 카테고리)다. 각 대분류 아래 실제 세부계정 목록은 `sub_accounts`에 **계정코드 단위로 중복 없이** 담긴다 — 계정코드는 전사 공통 코드 체계라 여러 부서에 걸쳐 동일하게 등장하므로, 부서 조합이 아니라 계정코드 하나당 항목 하나로 집계하고 그 계정이 실제 등장한 부서 전체를 `등장부서` 배열에 담는다.

```json
{
  "generated_at": "2026-07-11T09:00:00+09:00",
  "departments_scanned": ["보상팀", "계약관리팀", "재보험팀", "언더라이팅팀"],
  "min_depts_for_common": 2,
  "divergence_ratio_threshold": 5.0,
  "divergence_share_threshold": 0.8,
  "divergence_check_enabled": true,
  "category_alias_map_used": {},
  "common_categories": [
    {
      "category": "임차료",
      "departments": ["보상팀", "계약관리팀", "재보험팀", "언더라이팅팀"],
      "부서범위": "전체",
      "확정방식": "규칙기반",
      "비고": "4개 부서 등장, 세부계정 2건",
      "sub_accounts": [
        {"계정코드": "51205", "계정명": "임차료-사무실", "등장부서": ["보상팀", "계약관리팀", "재보험팀", "언더라이팅팀"], "금액": 48000000},
        {"계정코드": "51206", "계정명": "임차료-창고", "등장부서": ["보상팀"], "금액": 6000000}
      ]
    }
  ],
  "department_specific_categories": [
    {
      "category": "계약체결비용",
      "department": "계약관리팀",
      "확정방식": "규칙기반",
      "비고": "1개 부서에만 등장",
      "sub_accounts": [
        {"계정코드": "62100", "계정명": "계약체결비용-인지세", "등장부서": ["계약관리팀"], "금액": 3200000}
      ]
    }
  ],
  "needs_llm_recheck": [
    {
      "대분류": "전산비",
      "등장부서": ["IT운영팀", "재무팀", "총무팀"],
      "부서별_금액": {"IT운영팀": 30600000, "총무팀": 520000, "재무팀": 500000},
      "규칙기반_1차판정": "공통후보",
      "플래그_사유": "세부계정 중 여러 부서 공통형과 1개 부서 전용형이 섞여 있어 원가 발생 구조가 이질적일 가능성 / 부서별 금액이 특정 부서에 쏠림(1위 부서 점유율 97%, 1위/2위 배율 58.8배)",
      "sub_accounts": [
        {"계정코드": "C2001", "계정명": "전산비-공통시스템", "등장부서": ["IT운영팀", "재무팀", "총무팀"], "금액": 1620000},
        {"계정코드": "C2002", "계정명": "전산비-네트웍운영", "등장부서": ["IT운영팀"], "금액": 30000000}
      ]
    }
  ],
  "llm_verdicts": []
}
```

## 필드 설명

| 필드 | 설명 |
|---|---|
| `min_depts_for_common` | 대분류가 몇 개 부서에 등장해야 "공통 후보"로 보는지 (기본 2, `accounts_master.json`의 `categories[대분류].구분` 판정 기준과 동일) |
| `divergence_ratio_threshold` / `divergence_share_threshold` | `needs_llm_recheck` 판정에 쓰는 두 임계치(기본 5.0 / 0.8). **금액 자체의 절대 편차가 아니라 상대적 쏠림(점유율/배율)을 본다** — 과거 버전은 절대 금액 편차를 그대로 썼다가 조직 규모 차이(인원 많은 부서가 원래 금액도 큼)로 오탐이 잦아 한 번 제거된 적이 있다. 재도입 시 이 실패를 반복하지 않도록 (1) 세부계정 등장부서 분포 이질성(구조적 신호, 금액 무관)과 (2) 부서별 금액 점유율/배율(상대적 쏠림) 두 신호를 함께 쓴다(`segment_accounts.py` 모듈 docstring 참조) |
| `divergence_check_enabled` | `analyze()` 호출 시 `ratio_threshold`/`share_threshold`가 둘 다 `None`이 아니면 `true`(기본값 기준 항상 true) |
| `common_categories` / `department_specific_categories` | **최종 확정된** 대분류 목록. 각 원소의 `sub_accounts`는 그 대분류에 속한 세부계정을 계정코드 단위로 중복 없이 담는다(부서별로 반복하지 않음). `needs_llm_recheck`에 남아있는 대분류는 판정 전까지 이 두 목록에 들어가지 않는다 |
| `확정방식` | `규칙기반` \| `LLM재확인` — 사람이 왜 이 대분류가 이 그룹에 들어갔는지 추적할 수 있게 함 |
| `needs_llm_recheck` | **공통 후보인데 규칙 기반만으로 확정하지 못해 account-classifier 재확인이 필요한 대분류 목록.** 이미 `카테고리분류상태: "분류완료"`인 대분류(Phase 1이 끝난 것)는 재확인 대상에서 제외한다 — Phase 0.5 재확인은 Phase 1 착수 전에만 의미가 있다. **이 목록이 비어있지 않으면 Phase 0.5는 미완료 상태다** |
| `llm_verdicts` | account-classifier의 Phase 0.5 재확인 모드 판정 결과. `공통유지` 또는 `특정전환`. `특정전환`이면 해당 대분류는 등장부서 수와 무관하게 부서별로 개별 판단(department_specific_categories에 부서마다 별도 항목 생성)하고, `accounts_master.json`의 해당 대분류 `구분` 필드도 `특정`으로 갱신한다 |

## Phase 0.5 완료 조건

- `needs_llm_recheck`가 빈 배열이어야 한다. 처음 `analyze()` 실행 시 이질성/쏠림 신호가 있는 대분류가 여기 담기고, `apply_llm()`으로 account-classifier 판정을 반영해야 비워진다.
- 완료되면 `accounts_master.json`의 모든 대분류 `구분` 필드가 이 파일의 최종 `common_categories`/`department_specific_categories` 배정과 일치해야 한다 — 불일치가 있으면 Phase 1 시작 전 반드시 동기화한다.

## 재확인(LLM) 판정 요청 시 account-classifier에게 전달할 정보

- 대분류명, 등장부서 목록, 부서별 금액, `플래그_사유`(왜 재확인 대상이 됐는지 — 이질성/쏠림 중 무엇 때문인지)
- "이 대분류가 여러 부서에 등장하지만 부서별 금액/업무 맥락이 실질적으로 같은 원가 성격을 공유하는지, 아니면 부서마다 발생 원인이 달라 통일된 원가동인 적용이 부적절한지" 판단 요청
- account-classifier는 `cost-driver-framework` 스킬 참조 문서를 근거로 삼아 `공통유지`/`특정전환`과 사유를 응답한다 (`account-classifier.md`의 "Phase 0.5 재확인 모드" 참조)
