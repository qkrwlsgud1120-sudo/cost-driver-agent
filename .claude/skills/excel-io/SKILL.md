---
name: excel-io
description: 부서별 계정과목 원본 엑셀/CSV 파일을 읽어 정규화된 JSON으로 변환하고, 공통/특정계정 분리 결과(참고용 엑셀) 및 최종 원가동인 추천(1~3순위, 회계사 확정 UI 포함)을 엑셀로 저장·역읽기할 때 사용한다. 워크플로우 ①(입력 검증 전 원본 로드), Phase 0.5(공통/특정계정 시각화), ⑦(최종 산출물 출력), 피드백 재반영(회계사 확정 엑셀 역읽기) 단계에서 사용. 실제 확정 UI 자체는 `/streamlit_app/app.py`(Streamlit 앱)가 담당하며, 이 스킬의 `write_segmentation_excel.build_confirm_data()`를 그대로 import해서 재사용한다.
---

# excel-io

계정과목 원본/산출물 파일 입출력을 담당하는 코드 스킬. LLM 판단 없이 순수 변환/검증만 수행한다.

## 언제 사용하는가
- Phase 0 준비: `/input/departments/` 아래 부서 원본 파일들을 하나씩 정규화해야 할 때 → [scripts/read_accounts.py](scripts/read_accounts.py)
- Phase 0.5 산출물 시각화(읽기 전용 참고본): `account_segmentation.json` + `accounts_master.json`을 공통계정/특정계정(부서별 그룹) 2개 시트 엑셀로 변환 → [scripts/write_segmentation_excel.py](scripts/write_segmentation_excel.py). 이 모듈의 `build_confirm_data(seg, master)`는 실제 확정 화면(Streamlit 앱)과 공유하는 데이터 조립 함수이므로, 확정 UI 쪽 로직을 바꿀 때도 이 함수를 그대로 재사용한다.
- ⑦ 배치 병합 및 산출물 출력: 검증 통과한 결과를 회계사 확정 UI(드롭다운+수식+시트보호)가 포함된 최종 엑셀로 쓸 때 → [scripts/write_results.py](scripts/write_results.py)의 `write_workbook(records, output_xlsx, overwrite=False)`. 이미 확정을 거친 계정은 `확정여부`/`확정원가동인`/`확정순위`/`사람확정four_type`를 입력 레코드에 실어 보내면 "미확정" 기본값 대신 확정된 상태로 미리 채워진다. Streamlit 앱은 저장 시 `overwrite=True`로 호출해 부서별 최종 엑셀을 다시 만든다.
- 피드백 재반영: 회계사가 확정 컬럼을 채우거나 추가판단 필요 계정의 4-type/원가동인을 직접 기입해 재업로드한 최종 엑셀을 행 단위 JSON으로 파싱할 때 → [scripts/read_confirmations.py](scripts/read_confirmations.py). "새로 기입된 항목인지" 판정(accounts_master.json과의 대조)은 이 스크립트가 아니라 `batch-tracker` 스킬의 `track_batch.py confirm`이 한다

## 원본 파일 필수 컬럼
`계정코드`, `계정명`, `금액`, `부서명`. 없으면 ①입력 검증 실패로 처리하고 에스컬레이션한다 (누락 컬럼을 구체적으로 알려줄 것).

## 사용법

```bash
# 부서 원본 1개 파일 → 정규화 JSON
python scripts/read_accounts.py <input_file> <output_json>

# Phase 0.5: 공통/특정계정 분리 결과 + accounts_master.json → 읽기 전용 참고 엑셀 생성
python scripts/write_segmentation_excel.py <account_segmentation.json> <accounts_master.json> <output_xlsx>

# 검증 완료된 배치 JSON → 최종 엑셀 (기존 파일 있으면 같은 시트에 append)
python scripts/write_results.py <validated_json> <output_xlsx>

# 회계사가 확정/수정하거나 4-type·원가동인을 기입해 재업로드한 최종 엑셀 → 행 단위 원본 JSON
# (분류/반영은 track_batch.py confirm이 accounts_master.json과 대조해서 수행)
python scripts/read_confirmations.py <accountant_edited_final.xlsx> <output_rows_json>
```

실제 원가동인 확정은 엑셀을 직접 편집하는 방식(아래 "최종 산출물 엑셀" 경로) 또는 Streamlit 앱(`streamlit run streamlit_app/app.py`, "전체" 탭에서 공통/특정/추가판단 필요 계정을 확정하면 부서별 탭에 자동 반영)의 두 경로를 지원한다. 두 경로 모두 최종적으로 `accounts_master.json`에 수렴한다(CLAUDE.md §2 회계사 확정 피드백 루프 참조).

## 최종 산출물 엑셀(`batch_{id}_final.xlsx`) 규칙
- 컬럼(`write_results.py`의 `COLUMNS_EDITABLE`이 최종 소스): 대분류 / 계정코드 / 계정명 / 계정 설명 / 부서 / 4-type 분류 / **판단 경로** / 추천 1순위 / 근거 1순위 / 추천 2순위 / 근거 2순위 / 추천 3순위 / 근거 3순위 / 4-type 분류(사람 확정) / 확정 순위 / 원가동인 적용 방식 / 확정 여부 / 확정 원가동인 / 비고. 후보가 3개보다 적으면 해당 순위 컬럼은 비워둔다.
- **"판단 경로"**는 이 대분류의 four_type/원가동인이 누구/무엇의 판단인지 보여준다 — `AI 판단`(실제 account-classifier/driver-recommender 서브에이전트) \| `규칙 기반 폴백`(`phase1_apply.py`, 서브에이전트 없이 로컬 규칙으로 채운 PoC 대체 경로) \| `사람 직접 확정`(회계사가 Path 2 또는 예외지정으로 직접 확정). `accounts_master_schema.md`의 `판단경로` 필드 참조.
- **AI 추천 원가동인(최대 3순위)과 순위별 추천 근거를 그대로 노출한다** — 회계사가 승인/수정 판단을 하려면 AI가 무엇을 왜 추천했는지 봐야 하므로, 과거 버전의 "AI 판단 표시 금지" 원칙은 이 화면에 한해 적용하지 않는다 (CLAUDE.md §6 참조. AI 활용 사실을 감추지 않는 대신, 확정 전까지는 "추천"일 뿐 최종이 아님을 "확정 여부" 컬럼으로 명확히 구분한다).
- **명확 계정(추가판단 필요 아님)**: "확정 순위"는 `1순위`/`2순위`/`3순위`(존재하는 순위만)/`직접입력` 드롭다운. "확정 여부"는 `미확정`/`승인`/`수정` 드롭다운. 회계사가 반드시 1순위를 확정할 필요는 없다 — 원하는 순위를 고르고 "승인"하면 그 순위의 추천값이 확정된다. "확정 원가동인"은 승인 시 "확정 순위"에 해당하는 순위의 추천값을 자동 유지하는 중첩 IF 수식이 기본값이며, 직접 입력(수정)하면 수식을 덮어쓴다. "4-type 분류(사람 확정)"는 해당 없음(잠금).
- **추가판단 필요 계정(4-type 미확정)**: "4-type 분류"="추가판단 필요"(AI 산출물, 잠금), 추천 순위 컬럼은 모두 공란(추가판단이 필요한 사유는 "근거 1순위" 칸에 담는다). 아래 두 경로를 모두 지원한다 — 둘 다 "4-type 분류(사람 확정)" 컬럼(드롭다운: 직접귀속형/배부형/공통비형/기타)에 값을 입력하는 것으로 시작한다.
  - **Path 1** — "4-type 분류(사람 확정)"만 채우고 "확정 여부"는 `미확정`으로 둔다 → 원가동인 추천 재실행 대상(⑤~⑦).
  - **Path 2** — "4-type 분류(사람 확정)"을 채우고, "확정 여부"를 `확정`으로 선택하고, "확정 원가동인"에 직접 값을 입력한다 → AI 재추천 없이 확정값 그대로 반영, result-validator만 재실행.
  - 추가판단 필요 계정의 "확정 여부" 드롭다운은 명확 계정과 다르게 `미확정`/`확정` 2개 옵션만 가진다(승인/수정 개념이 적용되지 않으므로).
- 시트 보호가 걸려 있고 "확정 여부"·"확정 순위"·"확정 원가동인" 컬럼(+ 추가판단 필요 계정 행만 "4-type 분류(사람 확정)")만 잠금 해제되어 있다 — 나머지(AI 산출물)는 실수로 편집되지 않도록 잠긴다.
- 검토필요(추가판단 필요 또는 검증실패) 행만 배경색 강조(노란색).
- 같은 배치를 다시 쓸 때는 기존 시트를 덮어쓰지 않고 append한다(`write_workbook(..., overwrite=False)`). 컬럼 구조가 다른 구버전 파일에는 append할 수 없다(에러 발생). `overwrite=True`로 호출하면 기존 파일을 지우고 새로 만든다(Streamlit 앱의 재생성 시 사용).

## 회계사 확정 역읽기(`read_confirmations.py`)
엑셀을 계정코드 단위 행 그대로 파싱해 `{"대분류", "계정코드", "needs_review", "AI_4type", "사람확정4type", "확정여부", "확정원가동인", ...}` 배열을 반환한다 — 분류(무엇이 "새로 기입된" 항목인지)는 하지 않는다. `batch-tracker` 스킬의 `track_batch.py confirm`이 이 행들을 accounts_master.json 현재 상태와 대조해 "추가판단 필요" 대분류 중 아직 미해소인 것만 Path 1/Path 2로 반영한다(§ batch-tracker SKILL.md 참조). 명확 대분류의 승인/수정은 이 경로가 다루지 않는다 — Streamlit 확정 앱(경로 B)을 쓴다.

## Streamlit 확정 앱(`/streamlit_app/app.py`)
- 실행: `streamlit run streamlit_app/app.py` (프로젝트 루트 기준). `account_segmentation.json` + `accounts_master.json`을 읽어 `write_segmentation_excel.build_confirm_data()`로 조립한 데이터를 화면에 뿌린다.
- 탭 구조: **"전체"** 탭에서 공통계정·특정계정·추가판단 필요 계정을 확정한다(유일한 입력 지점). **부서별 탭**은 `account_segmentation.json`에서 실제로 인식된 부서 목록(`departments_scanned`)으로 그때그때 동적 생성되며(부서 개수·이름을 코드에 하드코딩하지 않음), 그 부서에 해당하는 확정 결과만 자동으로 모아 보여주는 **읽기 전용** 화면이다. 공통계정은 "전체" 탭에서 1번만 확정하면 `등장부서`에 포함된 모든 부서 탭에 동일하게 반영되므로, 부서마다 같은 계정을 중복 판단하는 문제가 구조적으로 발생하지 않는다.
- 진행률 계산(`st.progress`)은 AI 추천이 존재한다는 사실 자체가 아니라 실제 사람의 승인/직접입력 여부(`is_done()`)만 카운트한다 — AI 추천 존재 ≠ 확정.
- "전체 저장" 버튼을 누르면 (1) 화면상의 확정 초안을 `confirmed_results.json`으로 저장하고, (2) `track_batch.apply_browser_confirmations()`로 `accounts_master.json`에 반영하고, (3) 각 부서의 `batch_{id}_final.xlsx`를 `write_results.write_workbook(..., overwrite=True)`로 재생성한다(`batch_id`는 고정 문자열이 아니라 `account_segmentation.json`의 `generated_at` 날짜에서 매번 계산). 이 버튼을 누르기 전까지는 화면 상태(`st.session_state`)에만 존재하는 초안이다.
- 앱을 재시작해도 이전 확정 상태가 사라지지 않도록, 세션 시작 시 `accounts_master.json`의 `회계사확정`을 읽고, 아직 "전체 저장"하지 않은 초안은 `output/.streamlit_confirm_draft.json`에서 복원한다(저장이 완료되면 이 파일은 삭제된다).
- **사이드바 업로드**: `st.file_uploader(accept_multiple_files=True)`로 부서별 파일을 한 번에 여러 개 업로드할 수 있다. 부서명은 파일 내부 "부서명" 컬럼 값을 우선 인식하고(없으면 파일명으로 추정), 화면에서 텍스트 입력으로 고쳐 확정할 수 있다 — 고친 값은 저장 시 그 파일의 모든 행에 "부서명" 컬럼 값으로 그대로 반영되어 파일명이 아니라 실제 스캔 로직이 참조하는 값과 항상 일치한다. "분석 시작"을 누르면 기존 `output/`·`input/departments/`를 `output_backup_{timestamp}/`로 옮겨 보존한 뒤, `scan_accounts.scan()`(Phase 0) → `segment_accounts.analyze()`(Phase 0.5)를 그대로 재사용해 실행한다. Phase 0.5의 LLM 재확인과 Phase 1(4-type 분류·원가동인 추천)은 서브에이전트 호출이 필요해 Streamlit 프로세스 혼자서는 실행할 수 없으므로, 완료 후 화면에 Claude Code 세션으로 돌아가라는 안내를 표시한다.
