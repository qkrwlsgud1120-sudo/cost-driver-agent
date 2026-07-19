---
name: batch-tracker
description: 계정 인벤토리 사전 스캔, 공통/특정계정 분리 확정(Phase 0.5), 배치(부서) 이력 관리, 신규 vs 기존 배치 판별, 회계사 확정 반영, accounts_master.json/batch_log.json 갱신을 담당한다. 워크플로우 ⓪, Phase 0.5, ②, ⑦, 피드백 재반영 단계에서 사용. LLM 판단 없이 규칙 기반으로만 동작한다 (단, Phase 0.5의 추가판단 필요 사례는 account-classifier 재확인 결과를 입력으로만 받아 반영한다).
---

# batch-tracker

부서 간 계정 분류 일관성과 중단 후 재개(resume)를 보장하는 중앙 상태 관리 코드 스킬.

## 언제 사용하는가
- ⓪ Phase 0 계정 인벤토리 스캔 (프로젝트당 1회, 전체 부서 파일이 `/input/departments/`에 모두 준비된 뒤에만 실행) → [scripts/scan_accounts.py](scripts/scan_accounts.py)
- Phase 0.5 공통/특정계정 분리 확정 (Phase 0 직후, Phase 1 시작 전 반드시 완료) → [scripts/segment_accounts.py](scripts/segment_accounts.py)
- ② 배치 식별, ⑦ 배치 병합 후 로그/마스터 갱신 → [scripts/track_batch.py](scripts/track_batch.py)
- 피드백 재반영 (엑셀 경로 — 경로 A): 회계사가 편집해 재업로드한 `batch_{id}-{부서}_final.xlsx`에서 "추가판단 필요" 대분류의 Path 1/Path 2 해소만 감지해 accounts_master에 반영하고 재작업 대상을 판별 → [scripts/track_batch.py](scripts/track_batch.py) `confirm` 서브커맨드. 명확 대분류의 확정은 이 경로의 대상이 아니다(경로 B만 지원)
- 피드백 재반영 (Streamlit 확정 앱 경로): `/streamlit_app/app.py`의 "전체" 탭에서 "전체 저장" 시 생성되는 `confirmed_results.json`을 accounts_master에 반영. 공통계정은 부서명을 지정하지 않고 `등장부서` 전체에 동일하게 자동 전파된다 → [scripts/track_batch.py](scripts/track_batch.py) `apply-browser-confirmations` 서브커맨드(Streamlit 앱에서는 함수로 직접 호출)

## 스키마 참조
- 계정 마스터 구조: [references/accounts_master_schema.md](references/accounts_master_schema.md)
- 공통/특정계정 분리 구조: [references/account_segmentation_schema.md](references/account_segmentation_schema.md)
- 배치 로그 구조: [references/batch_log_schema.md](references/batch_log_schema.md)

## 사용법

```bash
# Phase 0: 전체 부서 파일 스캔 → accounts_master.json 생성
python scripts/scan_accounts.py <input_departments_dir> <output/accounts_master.json>

# Phase 0.5-1차: 규칙 기반 공통/특정 분리 (이질성/쏠림 신호가 있으면 needs_llm_recheck에 남김)
python scripts/segment_accounts.py analyze \
  <input_departments_dir> <output/accounts_master.json> <output/account_segmentation.json>

# Phase 0.5-2차: account-classifier 재확인 판정(llm_verdicts.json)을 반영해 최종 확정
python scripts/segment_accounts.py apply-llm \
  <output/account_segmentation.json> <llm_verdicts.json> \
  <output/accounts_master.json> <output/account_segmentation.json>

# ② 배치 식별: 특정 부서가 신규/기존인지 확인
python scripts/track_batch.py identify <output/accounts_master.json> <부서명>

# ⑦ 배치 완료 기록: batch_log.json에 이력 추가 + accounts_master 처리상태 갱신
python scripts/track_batch.py complete \
  <output/accounts_master.json> <output/batch_log.json> \
  <batch_id> <부서명> <batch_{id}_validated.json>

# 회계사 확정 반영 (엑셀 경로 — 경로 A): 재업로드된 최종 엑셀에서 "추가판단 필요" 대분류의
# Path 1/2 해소만 감지해 반영. 아직 미해소이거나 이미 다른 경로로 해소된 항목은 건드리지 않는다.
python scripts/track_batch.py confirm \
  <output/accounts_master.json> <accountant_edited_final.xlsx>

# 회계사 확정 반영 (Streamlit 확정 앱 경로 — 경로 B): confirmed_results.json을 accounts_master에 반영
# (공통계정은 등장부서 전체에 자동 전파, 부서명 인자 불필요)
python scripts/track_batch.py apply-browser-confirmations \
  <output/accounts_master.json> <confirmed_results.json>
```

## 핵심 규칙
- 계정코드가 2개 이상 부서에 등장하면 "공통" 후보, 1개 부서에만 등장하면 "특정"으로 분류한다 (규칙 기반 1차 판별).
- 공통 후보라도 (1) 세부계정 등장부서 분포가 이질적(여러 부서 공통형과 1개 부서 전용형이 섞임)이거나 (2) 부서별 금액이 특정 부서에 쏠려 있으면(점유율/배율 임계치 초과 — 절대금액이 아니라 상대적 쏠림을 본다) 규칙만으로 확정하지 않고 account-classifier의 재확인(Phase 0.5 재확인 모드)을 거친다. **`account_segmentation.json`의 `needs_llm_recheck`가 비어있어야 Phase 0.5가 완료된 것이고, 그 전까지 Phase 1을 시작하지 않는다.**
- `accounts_master.json`의 처리상태(`처리완료`)는 계정 단위로 즉시 갱신한다 (AI 추천 파이프라인 재개 로직의 유일한 기준). 회계사 확정 여부(`회계사확정`)는 별도 필드로, 처리완료와 독립적으로 추적한다.
- 스캔 시 `/input/departments/` 내 파일이 하나라도 없거나 읽기 실패하면 즉시 중단하고 어떤 부서/파일이 문제인지 보고한다 (임의로 일부만 스캔하지 않음).
- `track_batch.py confirm`(경로 A)은 재업로드된 엑셀을 `read_confirmations.py`로 행 단위 파싱한 뒤, "추가판단 필요" 대분류 중 **accounts_master.json 기준으로 아직 `카테고리분류상태: "추가판단필요(검토대기)"`인 것만** 대상으로 삼는다. 같은 대분류의 여러 계정코드 행에 값이 서로 다르면 반영하지 않고 '이상'으로 보고한다. 4-type과 원가동인을 함께 채우면(Path 2) `apply_category_item()`으로 한 번에 반영하고 result-validator만 재실행 대상으로 표시하며, 4-type만 채우면(Path 1) driver-recommender 재호출부터 다시 필요하다는 뜻으로 재실행 목록에 남긴다. 명확 대분류(추가판단 필요가 아닌)의 확정은 이 경로가 다루지 않는다 — 경로 B(Streamlit)를 쓴다.
- `apply_category_item`/`apply_subaccount_item`이 four_type·원가동인을 채울 때는 `판단경로`도 함께 기록한다 — `track_batch.py complete()`(실제 서브에이전트 배치)는 `"AI 판단"`, `phase1_apply.py`(로컬 규칙 폴백)는 `"규칙 기반 폴백"`, 회계사가 직접 확정(Path 2/예외지정)하면 `"사람 직접 확정"`. 최종 엑셀의 "판단 경로" 컬럼과 데모 요약 리포트 통계가 이 값을 그대로 쓴다.
- `track_batch.py apply-browser-confirmations`는 같은 `apply_category_item`/`apply_subaccount_item`을 재사용하되, 경로 A(추가판단 필요만)와 달리 **명확 대분류의 승인/수정 확정도 함께 다룬다** — 입력(`confirmed_results.json`)이 `categories`/`sub_accounts` 두 배열로 온다. **대분류 확정은 부서 무관 단일 결정이라 그 대분류의 `등장부서` 전체에 자동 전파**된다(부서마다 반복 확정할 필요가 없다) — 이것이 "한 번 확정하면 모든 관련 부서에 자동 반영"의 실제 구현이다. 세부계정을 개별적으로 다르게 하고 싶으면 `sub_accounts` 배열로 "예외 지정"을 전달한다.
