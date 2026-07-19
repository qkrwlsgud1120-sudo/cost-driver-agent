# 산출물 폴더

워크플로우가 자동으로 생성/갱신하는 파일들입니다. 직접 편집하지 마세요 (특히 `accounts_master.json`은 재개 로직의 기준이므로 수동 수정 시 상태가 깨질 수 있습니다).

| 파일 | 생성 시점 | 설명 |
|---|---|---|
| `accounts_master.json` | Phase 0 | 계정 인벤토리 (공통/특정 구분, 처리상태) |
| `batch_log.json` | ⑦ | 배치 처리 이력 |
| `batch_{id}_raw.json` | ① | 배치 원본 정규화 결과 |
| `batch_{id}_classified.json` | ③ | 4-type 분류 결과 |
| `batch_{id}_recommended.json` | ⑤ | 원가동인 추천 결과 |
| `batch_{id}_validated.json` | ⑥ | 자기검증 완료 결과 |
| `batch_{id}_final.xlsx` | ⑦ | 최종 부서별 결과 엑셀 (누적 append) |
