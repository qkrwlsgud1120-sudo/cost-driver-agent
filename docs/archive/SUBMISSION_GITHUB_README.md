# 원가동인(Cost Driver) 추천 및 회계사 확정 PoC

> **GitHub 제출용 README 초안** — repo 루트의 `README.md`로 복사해 사용하세요.  
> Notion 제출본: [Notion 페이지 링크](YOUR_NOTION_URL)

손해보험사 부서별 비용 데이터를 업로드하면 **공통/특정 계정 분리 → 비용 성격 설명 → 원가동인 1~3순위 추천 → 회계사 확정 → 엑셀 산출**까지 이어지는 PoC입니다.

---

## 제출 요약 (Reviewer Quick Start)

| 항목 | 링크 |
|------|------|
| **Notion 제출 페이지** (권장) | [YOUR_NOTION_URL](YOUR_NOTION_URL) |
| **데모 영상** (3~5분) | [demo_streamlit.mp4](docs/demo_streamlit.mp4) 또는 Releases |
| **샘플 산출물** | [`output/`](output/) · [`docs/samples/`](docs/samples/) |

### 3분 검증 방법

1. **데모 영상** 시청 — 업로드 → 확정 → 저장 → 엑셀 확인
2. **`output/account_segmentation.json`** — 공통 10 / 특정 71 / 부서 9개
3. **`output/accounts_master.json`** — 계정별 추천·확정 필드 구조 확인
4. **(선택)** 아래 로컬 실행으로 직접 조작

> `localhost` URL은 제출하지 않습니다. 구동 증명은 **영상 + 산출물 + (선택) 배포 URL**로 합니다.

---

## PoC 결과 스냅샷 (2026-07-09 기준)

| 지표 | 값 |
|------|-----|
| 스캔 부서 | 9개 |
| 전체 계정 | 81개 |
| 공통계정 | 10개 |
| 특정계정 | 71개 |
| Phase 0.5 재확인 대기 | 0건 |

**스캔 부서:** IT개발팀, 기업영업1팀, 마케팅팀, 법무팀, 상품개발팀, 인사팀, 자동차보상센터, 재무회계팀, 총무팀

---

## 저장소 구성

본 repo(`cost-driver-agent`)는 **실무 확정 Workbench**입니다.  
스토리텔링 6단계 데모(Vite)는 별도 repo 또는 `cost-allocation-project/` 서브폴더로 함께 제출할 수 있습니다.

```
cost-driver-agent/
├── streamlit_app/
│   ├── app.py              # 회계사 확정 UI (Streamlit)
│   └── cost_nature.py      # 비용 성격·자동 동인 규칙
├── .claude/skills/
│   ├── batch-tracker/      # Phase 0, 0.5 (scan, segment)
│   └── excel-io/           # 엑셀 입출력, build_confirm_data()
├── input/departments/      # 부서별 CSV (샘플)
├── output/                 # JSON·엑셀 산출물
├── docs/
│   ├── SUBMISSION_NOTION.md    # Notion 붙여넣기 초안
│   └── SUBMISSION_GITHUB_README.md  # 이 파일
├── requirements.txt
└── CLAUDE.md               # 전체 파이프라인 설계
```

---

## 워크플로우

```
Phase 0   계정 인벤토리 스캔          → accounts_master.json
Phase 0.5 공통/특정 분리              → account_segmentation.json
Phase 1   4-type + 동인 1~3순위 추천  → (AI 또는 PoC 자동 추천)
확정      회계사 승인/수정/직접입력    → Streamlit UI
출력      마스터 + 부서별 엑셀         → batch_*_final.xlsx
```

**Phase 0.5 규칙 (현재 PoC):**
- 2개 이상 부서 등장 → **공통계정**
- 1개 부서만 등장 → **특정계정**
- 부서별 금액 편차는 판단 기준에서 **제외** (조직 규모 차이 반영)

---

## Streamlit 확정 도구

### 실행

```bash
pip install -r requirements.txt
streamlit run streamlit_app/app.py --server.port 8642
```

브라우저: http://localhost:8642

### 주요 기능

- 부서 CSV/XLSX **다중 업로드** → Phase 0 + 0.5 자동 실행
- **비용 성격 설명** — 계정명 규칙 20+ (퇴직급여·명예퇴직 등 우선순위 규칙 포함)
- **원가동인 1~3순위** — AI 추천 또는 PoC 자동 추천(Phase 1 전)
- **1순위 승인** / 2·3순위 popover / **직접 입력**
- **「전체 저장」** — `accounts_master.json` + `batch_{id}-{부서}_final.xlsx`

---

## Vite 스토리 데모 (별도 프로젝트)

6단계 wizard: 업로드 → AI → **회계사 검토** → **회사 협의** → 가중치 → **배분·왜곡 분석**

```bash
cd cost-allocation-project
npm install
npm run dev   # http://localhost:5173
```

---

## 산출물

| 파일 | 설명 |
|------|------|
| `output/accounts_master.json` | 계정 마스터 (등장부서, four_type, 원가동인, 회계사확정) |
| `output/account_segmentation.json` | 공통/특정 분리 결과 |
| `output/batch_{date}-{부서}_final.xlsx` | 부서별 최종 확정 엑셀 (저장 후 생성) |
| `output/confirmed_results.json` | Streamlit 「전체 저장」 시 확정 payload |

샘플은 [`output/`](output/) 및 GitHub Releases `sample-data`에 포함하세요.

---

## 설계 원칙

1. **AI는 추천만** — 최종 확정은 회계사
2. **공통계정 1회 확정 → 등장 부서 전체 반영**
3. **추가판단 필요 계정** — 4-type·동인을 사람이 직접 기입
4. **백엔드 재사용** — Streamlit은 UI만, `scan_accounts` / `segment_accounts` / `track_batch` 스크립트 그대로 사용

---

## PoC 범위 · 한계

**포함:** CSV/XLSX 업로드, 공통/특정 분리, 비용 설명, 동인 추천, 확정 UI, 엑셀 출력

**미포함:** ERP 연동, SaaS, 법적 최종 판단, 전 계정 AI-only 분류

---

## 커밋 이력 가이드 (제출용)

리뷰어가 커밋만으로도 진행 과정을 보려면, 아래처럼 **기능 단위**로 커밋 메시지를 정리해 두세요.

```
feat: Streamlit 확정 UI — Phase 파이프라인·비용 성격·자동 동인 추천
feat: Phase 0.5 — 금액 편차 기준 제거, 등장 부서 수만으로 공통/특정 분리
feat: cost_nature — 비용 성격 규칙 및 로컬 동인 1~3순위
fix: Streamlit import 및 Phase 0.5 재확인 UI 정리
docs: 회계법인 제출용 Notion/README 초안
```

---

## (선택) 공개 배포

| 도구 | 플랫폼 | Main entry |
|------|--------|------------|
| Streamlit | [Streamlit Community Cloud](https://share.streamlit.io) | `streamlit_app/app.py` |
| Vite | [Vercel](https://vercel.com) | `npm run build` |

배포 URL을 Notion/README에 추가하면 제출자 PC 없이도 직접 조작 가능합니다.

---

## 라이선스 · 데이터

- 코드: [MIT / 제출 과제용 — 필요 시 수정]
- 데이터: **샘플·마스킹** — 실제 회사 원본 미포함

---

## Contact

[이름] · [이메일]
