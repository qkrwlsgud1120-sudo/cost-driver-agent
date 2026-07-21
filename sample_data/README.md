# sample_data/

`input/departments/`의 가상 샘플 부서 데이터를 실제로(Claude Code 서브에이전트 + Anthropic
API 양쪽으로) Phase 0~1까지 끝까지 돌려서 나온 결과 스냅샷이다. `output/`과 달리 이 폴더는
`.gitignore` 대상이 아니다 — 배포된 Streamlit 앱이 API 키 없이도, 실행 없이도 "📦 샘플
데이터 불러오기" 버튼 한 번으로 실제 AI 판단 결과(비용 성격 설명, 원가동인 1~3순위, 근거
문장)를 곧바로 보여줄 수 있게 하기 위한 용도다.

- `accounts_master.json`, `account_segmentation.json`: 실행 시점 스냅샷. 실제 고객사 데이터가
  아니라 `input/departments/`의 가상 샘플 데이터를 처리한 결과다.
- 새로고침하려면(더 최신 처리 결과로 교체) 로컬에서 Phase 0~1을 다시 실행한 뒤
  `output/accounts_master.json` / `output/account_segmentation.json`을 이 폴더로 복사하면 된다.
- 이 스냅샷은 진행 중(WIP) 상태를 그대로 담고 있다 — 일부 대분류는 아직 회계사 확정 전이고,
  일부는 "추가판단 필요"로 남아있다. 이는 의도한 것이다: 이 도구의 핵심은 "AI가 다 끝내는 것"이
  아니라 "AI가 판단하고 사람이 확정하는 protocol"이므로, 100% 완료된 상태보다 실제 워크플로우
  중간 단계를 보여주는 편이 더 정직한 데모다.
