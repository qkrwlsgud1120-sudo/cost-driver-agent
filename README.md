# 원가동인(Cost Driver) 추천 멀티 에이전트 시스템

손해보험사 관리회계 비용실사에서, 부서별 비용 계정을 업로드하면 **공통/특정 대분류 자동 분리 → 4-type 분류 → 원가동인(cost driver) 1~3순위 추천 → 회계사 검토·확정 → 엑셀 산출**까지 이어지는 워크플로우 도구입니다. 판단은 Claude 서브에이전트가 근거와 함께 추천하고, 최종 확정은 항상 회계사가 합니다.

> **배포:** [cost-driver-agent-kfemdr33ywdghm76v87qza.streamlit.app](https://cost-driver-agent-kfemdr33ywdghm76v87qza.streamlit.app) (Streamlit Community Cloud). 배포 인스턴스는 `output/`이 git 추적 제외 대상이라 초기 상태(업로드 대기 화면)로 뜹니다 — Phase 1(4-type·원가동인 추천)은 Claude Code 서브에이전트 호출이 필요해 클라우드 단독으로는 재현할 수 없으니, 처리된 상태의 화면은 아래 스크린샷과 로컬 실행([실행 방법](#실행-방법))으로 확인해주세요.

---

## 프로젝트 개요

관리회계 비용실사에서 각 비용 계정에 원가동인을 설정하는 작업은 두 가지 문제를 안고 있습니다. 하나는 **도메인 지식 병목**입니다 — 담당 회계사가 보험업 특유의 계정 성격(재보험비 항목, 보험 계약 관련 비용 구조 등)을 파악하는 데 시간이 걸려, 가장 상류 단계인 계정 분류부터 지연되고 이후 전체 파이프라인이 늦어집니다. 다른 하나는 **부서 단위 중복 판단**입니다 — 계정과목을 부서별로 순차 처리하면, 같은 이름·성격의 계정이 부서마다 다르게 분류되어 실사 결과의 일관성이 깨질 위험이 있습니다.

이 프로젝트는 두 문제를 구조적으로 풀어냅니다. 먼저 전체 부서 파일을 사전에 스캔해 계정 인벤토리를 만들고, 계정코드가 아니라 **대분류**(계정명에서 도출한 카테고리) 단위로 공통/특정 여부를 먼저 확정합니다(Phase 0 → Phase 0.5). 그다음 `account-classifier`(4-type 분류) → `driver-recommender`(원가동인 1~3순위 추천) → `result-validator`(자기검증) 세 개의 전문화된 Claude 서브에이전트가 보험업 도메인 지식과 ABC 원가계산 프레임워크를 참조해 판단과 근거를 생성합니다.

AI는 추천과 근거 제시까지만 담당하고, 최종 확정은 항상 회계사가 Streamlit 화면에서 승인·수정·직접입력으로 결정합니다(human-in-the-loop). 대분류를 한 번 확정하면 그 아래 세부계정 전체에 "원칙 준용"으로 자동 상속되어, 부서마다 같은 계정을 반복 판단하는 문제 자체가 구조적으로 생기지 않습니다.

---

## 스크린샷

`[스크린샷 삽입 위치: 요약 대시보드]`
![요약 대시보드](docs/screenshots/dashboard.png)

`[스크린샷 삽입 위치: 대분류 확정 카드 — 비용 성격 설명 + 원가동인 추천 + 확정 버튼]`
![대분류 확정 카드](docs/screenshots/category_card.png)

`[스크린샷 삽입 위치: 부서별 탭 — 확인 전용 테이블]`
![부서별 탭](docs/screenshots/department_tab.png)

`[스크린샷 삽입 위치: 최종 확정 엑셀 — 판단 경로/4-type/원가동인 컬럼]`
![최종 확정 엑셀](docs/screenshots/final_excel.png)

---

## 아키텍처 요약

메인 세션은 오케스트레이터로만 동작합니다 — 계정 분류·원가동인 추천·결과 검증을 직접 판단하지 않고, 반드시 아래 서브에이전트에 위임합니다. 서브에이전트끼리는 서로 호출할 수 없습니다(플랫폼 제약).

| 서브에이전트 | 모델 | 판단 범위 |
|---|---|---|
| [`account-classifier`](.claude/agents/account-classifier.md) | opus | 대분류를 4-type(직접귀속형/배부형/공통비형/기타) 중 하나로 분류 + 근거 작성 + 자기신뢰도(0~100). Phase 0.5에서는 "공통/특정 재확인 모드"로도 호출됨 |
| [`driver-recommender`](.claude/agents/driver-recommender.md) | opus | 원가동인 1~3순위 추천 + 순위별 근거 |
| [`result-validator`](.claude/agents/result-validator.md) | sonnet | 형식·논리·참조 문서 인용 진위 검증 (생성과 검증을 분리해 자기검증 편향을 줄임) |

판단의 참조 자료(ABC 원가계산 원칙, 4-type 분류체계, 보험회계 일반 지식)는 [`.claude/skills/cost-driver-framework`](.claude/skills/cost-driver-framework)에, 계정 인벤토리 스캔·배치 이력 관리·회계사 확정 반영 같은 규칙 기반 로직은 [`.claude/skills/batch-tracker`](.claude/skills/batch-tracker)와 [`.claude/skills/excel-io`](.claude/skills/excel-io)에 있습니다. 전체 워크플로우와 오케스트레이션 규칙은 [`CLAUDE.md`](CLAUDE.md)에 정의되어 있습니다.

전체 파이프라인(Phase 0~1, 서브에이전트별 실제 프롬프트 발췌, 한계점, 설계 결정, 버전 로그 등)은 [`docs/project_summary.md`](docs/project_summary.md)에 자세히 정리되어 있습니다.

---

## 기술 스택

- **Claude Code** — 서브에이전트(Task) 오케스트레이션, `.claude/agents`·`.claude/skills` 구조
- **Python** — 백엔드 로직(계정 스캔, 대분류 분리, 엑셀 입출력)
- **[Streamlit](https://streamlit.io/)** (`>=1.38`) — 회계사 확정 UI
- **[pandas](https://pandas.pydata.org/)** (`>=2.0`) — 계정과목 원본 파일 파싱·집계
- **[openpyxl](https://openpyxl.readthedocs.io/)** (`>=3.1`) — 확정용/보고서용 엑셀 생성·읽기(드롭다운, 수식, 시트 보호 포함)

정확한 버전 범위는 [`requirements.txt`](requirements.txt)를 참조하세요.

---

## 폴더 구조

```
cost-driver-agent/
├── README.md                  # 이 파일
├── CLAUDE.md                  # 오케스트레이터 지침 — 전체 워크플로우·에스컬레이션 규칙
├── DESIGN.md                  # IBM Carbon 기반 디자인 시스템 분석본 (Streamlit UI 레퍼런스)
├── requirements.txt
├── .gitignore
├── .streamlit/
│   └── config.toml            # Streamlit 테마 설정
├── .claude/
│   ├── agents/                # account-classifier / driver-recommender / result-validator
│   └── skills/                # batch-tracker / cost-driver-framework / excel-io
├── streamlit_app/
│   ├── app.py                 # Streamlit 진입점 (회계사 확정 UI)
│   ├── cost_nature.py         # 비용 성격 규칙·로컬 원가동인 추천(Phase 1 미실행 시 폴백)
│   └── phase1_apply.py        # 서브에이전트 없이 돌리는 규칙 기반 Phase 1 PoC 스크립트
├── input/
│   └── departments/           # 부서별 비용 계정 원본 CSV — 가상/샘플 데이터
├── docs/
│   ├── project_summary.md     # 프로젝트 상세 문서 (로드맵/한계점/설계 결정/버전 로그)
│   ├── screenshots/           # 화면 캡처 이미지
│   └── archive/               # 이전(대분류 리팩터 이전) 스냅샷 문서 — 참고용 보관
└── output/                    # 실행 시 생성되는 산출물 (git 추적 제외, README만 유지)
```

---

## 실행 방법

```bash
pip install -r requirements.txt
streamlit run streamlit_app/app.py --server.port 8642
```

브라우저에서 `http://localhost:8642`로 접속합니다. 사이드바에서 `input/departments/`의 샘플 부서 파일(또는 직접 업로드한 파일)로 Phase 0(계정 스캔) + Phase 0.5(공통/특정 분리)를 실행할 수 있습니다.

**Phase 1(4-type 분류·원가동인 추천)은 서브에이전트 호출이 필요해 Streamlit만으로는 실행할 수 없습니다** — Claude Code 세션으로 돌아가 "이 배치 처리해줘"라고 요청해야 합니다(자세한 워크플로우는 [`CLAUDE.md`](CLAUDE.md) 참조). 서브에이전트 없이 화면만 빠르게 확인해보고 싶다면 `python streamlit_app/phase1_apply.py`로 규칙 기반 PoC 결과를 대신 채울 수 있습니다.

---

## 더 자세한 내용

Phase 1~3 개발 로드맵, 서브에이전트 실제 프롬프트 발췌, 실제 실행 이력, 한계점, 설계 결정(Trade-off), 버전 로그는 **[`docs/project_summary.md`](docs/project_summary.md)**에서 확인할 수 있습니다.

---

## 주의사항

`input/departments/`의 샘플 데이터는 실제 고객사 데이터가 아닌, 일반화된 가상 데이터입니다.
