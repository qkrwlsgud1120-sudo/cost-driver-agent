"""원가동인 확정 Streamlit 앱.

기존 정적 HTML 확정 도구를 대체한다. 백엔드 로직(공통/특정 대분류 분리, 계정분류,
원가동인 1~3순위 추천, result-validator 검증)은 전혀 건드리지 않고, 화면단(확정 UI)만
Streamlit으로 재구현했다. 데이터 조립은 `write_segmentation_excel.build_confirm_data()`를
그대로 재사용하고, accounts_master.json 반영은 `track_batch.apply_browser_confirmations()`를
그대로 재사용하며, 최종 엑셀 생성은 `write_results.write_workbook()`을 그대로 재사용한다.

**대분류 도입 이후 구조**: 확정 단위는 계정코드가 아니라 대분류다. 대분류를 확정하면
"원칙"이 되어 그 아래 세부계정 전체에 자동 상속되고, 특정 세부계정만 다르게 하고 싶으면
"예외 지정"으로 개별 확정한다. 세부계정(계정코드)은 전사 공통 코드 체계라 여러 부서에
걸쳐 동일하게 등장해도 부서 무관 단일 레코드로 다룬다 — 예외 지정하면 그 계정이 등장하는
모든 부서에 동일하게 적용된다. 세션 상태 키는 `category:{대분류}`(대분류 확정)와
`account:{계정코드}`(세부계정 적용방식·예외 확정, 계정코드 단위) 두 네임스페이스로 나뉜다.

실행:
    streamlit run streamlit_app/app.py
"""
import json
import html
import shutil
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

BASE_DIR = Path(__file__).resolve().parent.parent
SAMPLE_DATA_DIR = BASE_DIR / "sample_data"
OUTPUT_DIR = BASE_DIR / "output"
DEPARTMENTS_DIR = BASE_DIR / "input" / "departments"
SEG_PATH = OUTPUT_DIR / "account_segmentation.json"
MASTER_PATH = OUTPUT_DIR / "accounts_master.json"
CONFIRMED_PATH = OUTPUT_DIR / "confirmed_results.json"
DRAFT_PATH = OUTPUT_DIR / ".streamlit_confirm_draft.json"
VERDICTS_PATH = OUTPUT_DIR / "llm_verdicts.json"
LOG_PATH = OUTPUT_DIR / "batch_log.json"
SEGMENT_REQUIRED_COLUMNS = ["계정코드", "계정명", "금액"]  # 부서명은 업로드 단계에서 직접 채워 넣으므로 제외

sys.path.insert(0, str(BASE_DIR / ".claude" / "skills" / "batch-tracker" / "scripts"))
sys.path.insert(0, str(BASE_DIR / ".claude" / "skills" / "excel-io" / "scripts"))

import scan_accounts  # noqa: E402  (skills 스크립트, 백엔드 로직 재사용)
import segment_accounts  # noqa: E402
import track_batch  # noqa: E402
import write_results  # noqa: E402
import write_segmentation_excel as seg_mod  # noqa: E402
from category_utils import resolve_account_effective  # noqa: E402

STREAMLIT_APP_DIR = Path(__file__).resolve().parent
if str(STREAMLIT_APP_DIR) not in sys.path:
    sys.path.insert(0, str(STREAMLIT_APP_DIR))

from cost_nature import (  # noqa: E402
    build_local_driver_recommendations,
    resolve_display_nature,
    suggest_local_driver,
)
import ai_pipeline  # noqa: E402  (Claude Code 개입 없이 Anthropic API 직접 호출용)

KST = timezone(timedelta(hours=9))
FOUR_TYPES = ["직접귀속형", "배부형", "공통비형", "기타"]

st.set_page_config(page_title="원가동인 확정 도구", layout="wide", page_icon="📊")

st.markdown(
    """
    <style>
    /* ------------------------------------------------------------------
       전체 글씨체 — Pretendard 통일. 처음에 전체 선택자(*)로 강제 적용했더니
       파일 업로더 드래그앤드롭 영역의 아이콘(SVG)까지 폰트가 강제되면서 안내
       문구와 아이콘이 겹쳐 보이는 부작용이 있었다 — svg는 명시적으로 제외하고
       텍스트를 실제로 담는 요소 목록으로 범위를 좁혔다. 그런데도 파일 업로더는
       숨겨진 네이티브 input[type=file] 버튼 위에 Streamlit이 커스텀 버튼을
       덧그리는 이중 레이어 구조라, 폰트가 바뀌면서 두 레이어의 텍스트 폭이
       달라져 "upload"/"Upload"가 겹쳐 보였다 — 파일 업로더 영역 전체를
       예외 처리해 원래 폰트 그대로 둔다. JSON/코드 표시 요소도 모노스페이스
       예외로 남긴다(가독성).
       ------------------------------------------------------------------ */
    @import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css');

    html, body, [class*="css"], [class*="st-"],
    h1, h2, h3, h4, h5, h6, p, span, div, label, li, a,
    button, input, textarea, select, option, table, th, td, small, strong, em {
        font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
    }
    svg, svg * {
        font-family: initial !important;
    }
    code, pre, kbd, samp, .stCode, .stJson, [data-testid="stJson"], [data-testid="stCode"] {
        font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace !important;
    }
    /* 파일 업로더는 여러 후보 셀렉터로 중복 예외 처리한다(Streamlit/BaseWeb 내부 구조가
       버전마다 달라 정확한 testid를 확신할 수 없어 폭넓게 잡는다). 숨겨진 네이티브
       input[type=file]은 커스텀 버튼과 겹쳐 그려지는 레이어라 폰트 문제와 무관하게
       완전히 안 보이도록 강제로 투명·비활성 처리해서 겹쳐 보이는 원인 자체를 없앤다. */
    [data-testid*="FileUploader" i], [data-testid*="FileUploader" i] *,
    [data-baseweb*="file" i], [data-baseweb*="file" i] *,
    [class*="uploadDropzone" i], [class*="uploadDropzone" i] *,
    [class*="FileUploader" i], [class*="FileUploader" i] * {
        font-family: initial !important;
    }
    input[type="file"] {
        font-family: initial !important;
        opacity: 0 !important;
    }
    /* Streamlit 최신 버전은 아이콘을 SVG가 아니라 Material Symbols 리게처 폰트로 그린다
       (예: "expand_more", "arrow_right", "upload" 같은 텍스트를 아이콘 폰트가 글리프로
       치환). 전역 Pretendard 규칙이 이 요소의 font-family까지 덮어쓰면 리게처 치환이
       깨져 원본 텍스트가 그대로 노출되고 주변 라벨과 겹쳐 보인다 — 아이콘 요소는
       원래 폰트(Material Symbols)를 쓰도록 명시적으로 예외 처리한다. */
    [data-testid="stIconMaterial"], [data-testid="stIconMaterial"] *,
    [data-testid*="Icon" i], [data-testid*="Icon" i] *,
    [class*="material-icons" i], [class*="material-icons" i] *,
    [class*="material-symbols" i], [class*="material-symbols" i] *,
    i.material-icons, span.material-icons {
        font-family: 'Material Symbols Rounded', 'Material Symbols Outlined', 'Material Icons' !important;
    }

    /* ------------------------------------------------------------------
       색상 팔레트 — 프로젝트 루트 DESIGN.md(IBM Carbon Design System 분석본) 기준.
       primary(#0f62fe, IBM Blue)를 단일 포인트 컬러로 쓰고, ink/surface/hairline은
       DESIGN.md에 정의된 값을 그대로 사용한다. 폰트 패밀리만 Pretendard로 예외
       처리했고(위 참조), font-size/weight/line-height/letter-spacing 등 타입
       스케일은 DESIGN.md typography 토큰을 그대로 따른다. 기존 변수명(--navy-*,
       --grey-*, --bg-* 등)은 재사용 지점이 많아(약 30곳) 그대로 두고 값만 Carbon
       토큰으로 교체했다 — 렌더링 결과만 바뀌고 아래 컴포넌트 규칙과의 매핑은 동일.
       success/error 텍스트 색은 DESIGN.md가 solid 값 하나만 정의하고 있어(뱃지처럼
       옅은 배경 위에 얹는 용도가 아님), 옅은 배경(-10) 위에서도 대비가 나오도록
       Carbon 공식 Tag 컴포넌트가 쓰는 짙은 변형을 텍스트색으로 파생시켰다 — 기존
       RED(미확정)/GREEN(확정)이 뜻하던 상태 의미는 그대로 유지되고 색상 값만
       Carbon 팔레트로 치환된다. warning(#f1c21b)은 그 자체로는 밝아서 텍스트 대비가
       나오지 않으므로 옅은 배경 + 짙은 골드 텍스트 조합으로 가독성을 확보했다.
       ------------------------------------------------------------------ */
    :root {
        --navy-dark: #0f62fe; --navy-mid: #0f62fe; --navy-light: #0050e6; --navy-text: #525252;
        --ink: #161616; --grey-text: #525252; --grey-muted: #8c8c8c;
        --border: #e0e0e0; --border-soft: #e0e0e0;
        --bg-page: #f4f4f4; --bg-card: #fff; --bg-neutral: #f4f4f4; --bg-neutral-2: #e0e0e0;
        --ok-bg: #defbe6; --ok-text: #0e6027;
        --bad-bg: #fff1f1; --bad-text: #a2191f;
        --warn-bg: #fcf4d6; --warn-text: #684e00;
        --primary: #0f62fe; --on-primary: #ffffff;
    }
    .block-container { padding-top: 1.4rem; max-width: 1180px; }
    .hero-wrap {
        background: var(--primary); color: var(--on-primary); padding: 24px 24px 20px;
        border-radius: 0; margin-bottom: 18px; box-shadow: none;
    }
    .hero-title { font-size: 32px; font-weight: 400; line-height: 1.25; margin: 0 0 6px; letter-spacing: 0; }
    .hero-sub { color: rgba(255,255,255,.85); font-size: 14px; font-weight: 400; line-height: 1.29;
        letter-spacing: .16px; margin: 0; }
    .hero-chip {
        display: inline-block; margin: 12px 8px 0 0; padding: 4px 11px; border-radius: 2px;
        background: rgba(255,255,255,.12); border: 1px solid rgba(255,255,255,.24);
        font-size: 12px; font-weight: 400; line-height: 1.33; letter-spacing: .32px;
    }
    .section-title { font-size: 14px; font-weight: 600; line-height: 1.29; letter-spacing: .16px;
        color: var(--ink); margin: 4px 0 2px; }
    .pipeline-bar {
        display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 16px;
    }
    .pipe-step {
        flex: 1; min-width: 140px; padding: 10px 12px; border-radius: 0;
        border: 1px solid var(--border-soft); background: var(--bg-card); font-size: 14px;
    }
    .pipe-step strong { display: block; font-size: 14px; font-weight: 600; margin-bottom: 2px; color: var(--ink); }
    .pipe-step span { color: var(--grey-text); font-size: 12px; }
    .pipe-step--done { border-color: var(--ok-text); background: var(--ok-bg); }
    .pipe-step--warn { border-color: var(--warn-text); background: var(--warn-bg); }
    .pipe-step--idle { border-color: var(--border-soft); background: var(--bg-page); }
    div[data-testid="stMetric"] {
        background: var(--bg-card); border: 1px solid var(--border); border-radius: 0;
        padding: 10px 12px 6px; box-shadow: none;
    }
    /* 상태 뱃지 3종 — 색상+이모지+텍스트 3중 표기(색맹 접근성·흑백 인쇄 대응) 유지,
       모서리만 Carbon의 flat-square 원칙에 맞춰 pill(999px)에서 rounded.xs(2px)로 축소 */
    .badge-confirmed { background:var(--ok-bg); color:var(--ok-text); padding:2px 10px; border-radius:2px;
                  font-size:12px; font-weight:600; letter-spacing:.32px; }
    .badge-pending { background:var(--bad-bg); color:var(--bad-text); padding:2px 10px; border-radius:2px;
                     font-size:12px; font-weight:600; letter-spacing:.32px; }
    .badge-needs-review { background:var(--warn-bg); color:var(--warn-text); padding:2px 10px; border-radius:2px;
                     font-size:12px; font-weight:600; letter-spacing:.32px; margin-right:6px; }
    /* 정보성 뱃지 — 채도를 낮춘 그레이 계열로 통일, 텍스트로만 구분(파랑은 CTA·링크 전용으로 절제) */
    .badge-scope-common { background:var(--bg-neutral); color:var(--navy-text); padding:2px 10px; border-radius:2px;
                     font-size:12px; font-weight:400; letter-spacing:.32px; margin-right:6px; }
    .badge-scope-specific { background:var(--bg-neutral-2); color:var(--grey-text); padding:2px 10px; border-radius:2px;
                     font-size:12px; font-weight:400; letter-spacing:.32px; margin-right:6px; }
    .badge-rec-ai { background:var(--bg-neutral); color:var(--navy-text); padding:2px 8px; border-radius:2px;
                     font-size:12px; font-weight:400; letter-spacing:.32px; margin-right:6px; }
    .badge-rec-auto { background:var(--bg-neutral-2); color:var(--grey-text); padding:2px 8px; border-radius:2px;
                      font-size:12px; font-weight:400; letter-spacing:.32px; margin-right:6px; }
    .badge-principle { background:var(--bg-neutral); color:var(--grey-text); padding:1px 8px; border-radius:2px;
                      font-size:12px; font-weight:400; letter-spacing:.32px; margin-right:6px; }
    .badge-exception { background:var(--bg-neutral); color:var(--navy-text); padding:1px 8px; border-radius:2px;
                      font-size:12px; font-weight:600; letter-spacing:.32px; margin-right:6px; border:1px solid var(--border); }
    .acct-title { font-weight:600; font-size:14px; letter-spacing:.16px; }
    .acct-code { color:var(--grey-muted); font-size:12px; letter-spacing:.32px; }
    .nature-box { color:var(--ink); font-size:14px; font-weight:400; line-height:1.5; letter-spacing:.16px;
                  padding:12px 14px; background:var(--bg-neutral); border-left:3px solid var(--primary);
                  border-radius:0; margin:10px 0 12px 0; }
    .driver-box {
        background: var(--bg-page); border: 1px solid var(--border); border-radius: 0;
        padding: 12px 14px; margin-top: 4px;
    }
    /* 대시보드 전용 — 강조 카드는 cta-banner와 같은 방식(단색 primary + 흰 텍스트)으로
       flat하게 표현한다. 그라데이션·드롭섀도우는 Carbon "no atmospheric depth" 원칙에
       따라 제거했고, 강조는 색을 늘리는 대신(제2의 브랜드 컬러 금지) 굵기로만 준다. */
    .efficiency-box {
        background: var(--primary); color: var(--on-primary); padding: 20px 22px;
        border-radius: 0; margin: 8px 0 18px; box-shadow: none;
    }
    .efficiency-box .eff-label { font-size: 14px; font-weight: 400; letter-spacing: .16px;
        color: rgba(255,255,255,.8); margin-bottom: 6px; }
    .efficiency-box .eff-main { font-size: 18px; font-weight: 400; line-height: 1.5; letter-spacing: 0; }
    .efficiency-box .eff-main b { font-weight: 600; color: var(--on-primary); }
    .empty-state-box {
        background: var(--bg-card); border: 1px dashed var(--border-soft); border-radius: 0;
        padding: 18px 20px; margin: 14px 0; color: var(--grey-text); font-size: 14px;
        font-weight: 400; line-height: 1.5; letter-spacing: .16px;
    }

    /* ------------------------------------------------------------------
       네이티브 Streamlit 컴포넌트 — 버튼/입력/탭/컨테이너/데이터프레임을
       Carbon의 flat-square(모서리 0px, 그림자 없음, 얇은 hairline 테두리)로 맞춘다.
       testid는 버전마다 조금씩 달라질 수 있어(예: baseButton-primary vs
       stBaseButton-primary) 위 파일업로더/아이콘 처리와 같은 방어적 다중 셀렉터
       패턴을 그대로 따른다. 색상(primary 등)은 .streamlit/config.toml의 [theme]도
       함께 Carbon 값으로 맞춰뒀다.
       ------------------------------------------------------------------ */
    .stButton button, .stDownloadButton button, .stFormSubmitButton button,
    [data-testid*="Button" i] button, button[kind] {
        border-radius: 0 !important; box-shadow: none !important;
    }
    .stTextInput input, .stTextArea textarea, .stNumberInput input,
    [data-baseweb="select"] > div, [data-baseweb="input"] {
        border-radius: 0 !important; box-shadow: none !important;
        background-color: var(--bg-page) !important;
    }
    [data-testid="stVerticalBlockBorderWrapper"], [data-testid*="stExpander" i],
    [data-testid="stDataFrame"], [data-testid="stDataEditor"] {
        border-radius: 0 !important; box-shadow: none !important; border-color: var(--border) !important;
    }
    .stTabs [data-baseweb="tab-list"] { border-bottom: 1px solid var(--border); gap: 4px; }
    .stTabs [data-baseweb="tab"] {
        border-radius: 0 !important; font-size: 14px; font-weight: 400; letter-spacing: .16px;
        color: var(--grey-text);
    }
    .stTabs [aria-selected="true"] { font-weight: 600 !important; color: var(--ink) !important; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# 데이터 로드 & 세션 초기화
# ---------------------------------------------------------------------------

def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def compute_batch_id(data: dict) -> str:
    """부서 구성이 매번 달라질 수 있으므로 batch_id를 고정 문자열로 하드코딩하지 않고,
    이번 Phase 0.5 실행 시각(account_segmentation.json의 generated_at)에서 날짜를 뽑아 쓴다."""
    generated_at = data.get("generated_at") or ""
    try:
        return datetime.fromisoformat(generated_at).strftime("%Y%m%d")
    except ValueError:
        return datetime.now(KST).strftime("%Y%m%d")


def load_all():
    seg = load_json(SEG_PATH)
    master = load_json(MASTER_PATH)
    data = seg_mod.build_confirm_data(seg, master)
    amount_by_dept, amount_by_code = build_amount_lookup()
    return seg, master, data, amount_by_dept, amount_by_code


def build_amount_lookup() -> tuple[dict[tuple[str, str], float], dict[str, float]]:
    """부서 CSV 원본에서 (계정코드, 부서명)별·계정코드 전사 합계 금액을 집계한다."""
    by_dept: dict[tuple[str, str], float] = {}
    by_code: dict[str, float] = {}
    if not DEPARTMENTS_DIR.exists():
        return by_dept, by_code

    for path in sorted(DEPARTMENTS_DIR.glob("*.csv")):
        try:
            df = pd.read_csv(path, dtype={"계정코드": str})
        except (OSError, ValueError):
            continue
        if "금액" not in df.columns:
            continue
        default_dept = path.stem
        for _, row in df.iterrows():
            code = str(row.get("계정코드", "")).strip()
            if not code:
                continue
            dept = str(row.get("부서명", default_dept)).strip() or default_dept
            amt = pd.to_numeric(row.get("금액"), errors="coerce")
            amt = float(amt) if pd.notna(amt) else 0.0
            by_dept[(code, dept)] = by_dept.get((code, dept), 0.0) + amt
            by_code[code] = by_code.get(code, 0.0) + amt
    return by_dept, by_code


def category_amount(cat_item: dict, amount_by_dept: dict) -> float:
    return sum(
        amount_by_dept.get((sa["code"], dept), 0.0)
        for sa in cat_item["subAccounts"] for dept in sa.get("departments", [])
    )


def category_dept_label(cat_item: dict) -> str:
    depts = cat_item.get("departments") or []
    if len(depts) == 1:
        return depts[0]
    if len(depts) > 1:
        return f"{len(depts)}개 부서"
    return "전사"


def category_to_nature_input(cat_item: dict, amount_by_dept: dict) -> dict:
    return {
        "account": cat_item["category"],
        "accountCode": cat_item["category"],
        "dept": category_dept_label(cat_item),
        "amount": category_amount(cat_item, amount_by_dept),
        "major": "",
        "minor": "",
        "memo": "",
    }


def render_nature_block(nature_input: dict, reason: str):
    text, source = resolve_display_nature(nature_input, reason)
    tag = render_badge("ai_rec" if source == "AI 분류근거" else "auto_rec", text=source)
    safe_text = html.escape(text)
    st.markdown(
        f'<div>{tag}<strong>비용 성격</strong></div>'
        f'<div class="nature-box">{safe_text}</div>',
        unsafe_allow_html=True,
    )


def resolve_item_drivers(item: dict, nature_input: dict) -> tuple[list[dict], str]:
    """Phase 1 AI 추천이 있으면 사용, 없으면 로컬 자동 추천을 대신 사용."""
    if item.get("drivers"):
        return item["drivers"], "ai"
    if item.get("needsReview"):
        return [], "none"
    return build_local_driver_recommendations(nature_input), "local"


def render_driver_section(item: dict, conf: dict, key_prefix: str, nature_input: dict):
    drivers, source = resolve_item_drivers(item, nature_input)
    if not drivers:
        st.warning("원가동인 추천을 생성할 수 없습니다. 직접 입력해주세요.")
        return

    rec_tag = render_badge("ai_rec" if source == "ai" else "auto_rec")
    rank1 = drivers[0]

    st.markdown(
        f'<div class="driver-box">{rec_tag}'
        f'<strong>1순위: {html.escape(rank1["driver"])}</strong></div>',
        unsafe_allow_html=True,
    )
    st.caption(rank1.get("reason") or "—")
    if source == "local":
        st.caption("Phase 1 미실행 — 대분류명·비용 성격 규칙 기반 참고 추천입니다. 회계사 검토 후 확정하세요.")

    col_a, col_b, col_c = st.columns([1, 1, 2])
    with col_a:
        if st.button("1순위 승인", key=f"approve1_{key_prefix}", type="primary", use_container_width=True):
            conf["confirmStatus"] = "승인"
            conf["confirmedDriver"] = rank1["driver"]
            conf["confirmedRank"] = 1
            persist_draft()
            st.rerun()

    if len(drivers) > 1:
        with col_b:
            with st.popover("다른 추천안 보기"):
                for d in drivers[1:]:
                    st.markdown(f"**{d['rank']}순위: {d['driver']}**")
                    st.caption(d.get("reason") or "—")
                    if st.button(f"{d['rank']}순위로 확정", key=f"approve_{d['rank']}_{key_prefix}"):
                        conf["confirmStatus"] = "승인"
                        conf["confirmedDriver"] = d["driver"]
                        conf["confirmedRank"] = d["rank"]
                        persist_draft()
                        st.rerun()
                    st.divider()

    with col_c:
        default_custom = conf["confirmedDriver"] if conf["confirmStatus"] == "수정" else rank1["driver"]
        custom_val = st.text_input(
            "직접 입력", value=default_custom, key=f"custom_input_{key_prefix}", label_visibility="collapsed",
            placeholder="원가동인 직접 입력",
        )
        if st.button("직접 입력으로 확정", key=f"custom_save_{key_prefix}"):
            conf["confirmStatus"] = "수정"
            conf["confirmedDriver"] = custom_val.strip()
            conf["confirmedRank"] = None
            persist_draft()
            st.rerun()


def current_phase_label() -> str:
    """사이드바에 표시할 현재 처리 단계 한 줄 요약. main()에서 ensure_state() 이후에만 호출한다."""
    data = st.session_state.data
    categories = data["categories"]
    recheck = len(data.get("needs_llm_recheck") or [])
    if recheck:
        return f"Phase 0.5 재확인 대기 {recheck}건"
    needs_review = sum(1 for c in categories if c["needsReview"])
    ai_rec = sum(1 for c in categories if not c["needsReview"] and c.get("drivers"))
    if needs_review:
        return f"Phase 1 진행 중 · 추가판단 필요 {needs_review}건 대기"
    if ai_rec < len(categories):
        return "Phase 1 진행 중 (AI 추천 대기)"
    done = sum(
        1 for c in categories
        if is_done_category(c, st.session_state.confirmations[f"category:{c['category']}"])
    )
    return f"회계사 확정 진행 중 · {done}/{len(categories)}건 확정"


def render_pipeline_banner(data: dict, categories: list[dict]):
    ai_rec = sum(1 for c in categories if not c["needsReview"] and c.get("drivers"))
    local_rec = sum(1 for c in categories if not c["needsReview"] and not c.get("drivers"))
    recheck = len(data.get("needs_llm_recheck") or [])
    phase05 = "done" if recheck == 0 else "warn"
    phase1 = "done" if ai_rec and local_rec == 0 else ("warn" if local_rec else "idle")

    steps = [
        ("Phase 0 · 계정 스캔", "완료", "done"),
        (
            "Phase 0.5 · 공통/특정 대분류 분리",
            "완료" if phase05 == "done" else f"LLM 재확인 {recheck}건",
            phase05,
        ),
        (
            "Phase 1 · 4-type·동인 추천",
            f"AI {ai_rec}건" if ai_rec else f"자동 추천 {local_rec}건",
            phase1 if ai_rec else "warn",
        ),
        ("회계사 확정", "진행 중", "idle"),
    ]
    chips = "".join(
        f'<div class="pipe-step pipe-step--{state}"><strong>{title}</strong><span>{detail}</span></div>'
        for title, detail, state in steps
    )
    st.markdown(f'<div class="pipeline-bar">{chips}</div>', unsafe_allow_html=True)


def render_app_header(dept_count: int, category_count: int, subaccount_count: int):
    st.markdown(
        f"""
        <div class="hero-wrap">
          <div class="hero-title">원가동인 확정 도구</div>
          <p class="hero-sub">
            대분류를 확정하면 세부계정 전체에 "원칙 준용"으로 자동 상속됩니다. 특정 세부계정만
            다르게 하려면 "예외 지정"으로 개별 확정하세요. 「전체 저장」으로 accounts_master.json과
            부서별 최종 엑셀에 반영합니다.
          </p>
          <span class="hero-chip">{dept_count}개 부서</span>
          <span class="hero-chip">{category_count}개 대분류</span>
          <span class="hero-chip">세부계정 {subaccount_count}건</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def default_category_conf() -> dict:
    return {"confirmStatus": "미확정", "confirmedDriver": "", "confirmedRank": None, "humanFourType": ""}


def default_subaccount_conf() -> dict:
    return {"applyMode": "원칙준용", "confirmStatus": "미확정", "confirmedFourType": "", "confirmedDriver": ""}


def init_confirmations(data: dict, master: dict) -> dict:
    """accounts_master.json에 이미 저장된 대분류/세부계정 확정을 세션 초기값으로 복원한다
    (재시작해도 이전 확정 상태가 유지되도록)."""
    conf_map: dict[str, dict] = {}
    categories_state = master.get("categories", {})

    for cat_item in data["categories"]:
        category = cat_item["category"]
        entry = default_category_conf()
        cat_state = categories_state.get(category, {})
        saved = cat_state.get("회계사확정")
        if saved:
            entry["confirmStatus"] = saved.get("확정여부") or "미확정"
            entry["confirmedDriver"] = saved.get("확정원가동인") or ""
            entry["confirmedRank"] = saved.get("확정순위")
        # cat_state["four_type"]는 AI가 "추가판단 필요"로 남겨둔 placeholder 문자열일 수도
        # 있다 — 그 값을 사람이 이미 확정한 4-type인 것처럼 오인식하지 않도록, 실제
        # FOUR_TYPES 중 하나일 때만 사람 확정값으로 취급한다.
        if cat_item["needsReview"] and cat_state.get("four_type") in FOUR_TYPES:
            entry["humanFourType"] = cat_state["four_type"]
        conf_map[f"category:{category}"] = entry

        for sa in cat_item["subAccounts"]:
            key = f"account:{sa['code']}"
            entry2 = default_subaccount_conf()
            entry2["applyMode"] = sa["applyMode"]
            exc = sa.get("exception")
            if exc:
                exc_confirm = exc.get("회계사확정") or {}
                entry2["confirmStatus"] = exc_confirm.get("확정여부") or "미확정"
                entry2["confirmedDriver"] = exc_confirm.get("확정원가동인") or ""
                entry2["confirmedFourType"] = exc.get("four_type") or ""
            conf_map[key] = entry2

    return conf_map


def load_draft() -> dict:
    """저장 전 확정 초안은 브라우저 새로고침/재접속으로 세션이 새로 생겨도 사라지지 않도록
    가벼운 로컬 파일에도 즉시 함께 남긴다 (accounts_master.json 반영은 "전체 저장" 시에만 발생 —
    이 파일은 그 전 단계의 임시 초안일 뿐, 진실 소스가 아니다)."""
    if not DRAFT_PATH.exists():
        return {}
    try:
        return load_json(DRAFT_PATH)
    except (json.JSONDecodeError, OSError):
        return {}


def persist_draft():
    try:
        with open(DRAFT_PATH, "w", encoding="utf-8") as f:
            json.dump(st.session_state.confirmations, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def ensure_state():
    st.session_state.setdefault("ai_run_count", 0)
    if "loaded" not in st.session_state:
        seg, master, data, amount_by_dept, amount_by_code = load_all()
        st.session_state.seg = seg
        st.session_state.master = master
        st.session_state.data = data
        st.session_state.amount_by_dept = amount_by_dept
        st.session_state.amount_by_code = amount_by_code
        confirmations = init_confirmations(data, master)
        for key, entry in load_draft().items():
            if key in confirmations:
                confirmations[key] = entry
        st.session_state.confirmations = confirmations
        st.session_state.loaded = True


def reload_from_disk():
    seg, master, data, amount_by_dept, amount_by_code = load_all()
    st.session_state.seg = seg
    st.session_state.master = master
    st.session_state.data = data
    st.session_state.amount_by_dept = amount_by_dept
    st.session_state.amount_by_code = amount_by_code
    # 화면에 이미 그려진 미저장 초안은 유지하되, 새로 생긴 대분류/세부계정이 있으면 기본값을 채운다
    fresh = init_confirmations(data, master)
    for key, entry in fresh.items():
        st.session_state.confirmations.setdefault(key, entry)


# ---------------------------------------------------------------------------
# 데이터 업로드 & 신규 배치 시작 (Phase 0 + Phase 0.5)
# ---------------------------------------------------------------------------

DEPT_COLUMN_CANDIDATES = ["부서명", "부서"]  # 실제 업로드 파일마다 컬럼명이 다를 수 있어 후보를 순서대로 확인한다


def find_dept_column_value(df: pd.DataFrame) -> str | None:
    for col in DEPT_COLUMN_CANDIDATES:
        if col in df.columns:
            values = df[col].dropna().astype(str).str.strip()
            values = values[values != ""]
            if not values.empty:
                return values.mode().iloc[0]
    return None


def extract_chunks(uploaded_file) -> list[dict]:
    """업로드 파일 하나에서 부서별 청크를 뽑아낸다.
    - CSV: 파일 전체가 청크 1개. 부서 컬럼("부서명"/"부서") 값이 있으면 그걸, 없으면 파일명으로 추정한다.
    - XLSX: **시트 하나 = 부서 하나**로 간주한다(실제 업로드 파일이 이런 멀티시트 구조로 오는 경우가 있음 —
      시트 지정 없이 read_excel을 부르면 첫 시트만 읽혀 나머지 부서가 통째로 누락되므로 반드시 전체 시트를 읽는다).
      시트 안에 부서 컬럼 값이 있으면 그걸, 없으면 시트 이름 자체를 부서명으로 추정한다."""
    name = uploaded_file.name
    chunks = []
    if name.lower().endswith(".csv"):
        df = pd.read_csv(uploaded_file, dtype={"계정코드": str})
        dept_guess = find_dept_column_value(df) or Path(name).stem
        chunks.append({"key": f"{name}", "file": name, "sheet": None, "dept_guess": dept_guess, "df": df})
    else:
        sheets = pd.read_excel(uploaded_file, sheet_name=None, dtype={"계정코드": str})
        for sheet_name, df in sheets.items():
            dept_guess = find_dept_column_value(df) or sheet_name
            chunks.append({"key": f"{name}::{sheet_name}", "file": name, "sheet": sheet_name,
                           "dept_guess": dept_guess, "df": df})
    uploaded_file.seek(0)
    return chunks


def validate_chunk_df(df: pd.DataFrame) -> list[str]:
    missing = [c for c in SEGMENT_REQUIRED_COLUMNS if c not in df.columns]
    return missing


def backup_and_reset() -> Path | None:
    """새 배치를 완전히 새로 시작하기로 한 경우, 기존 output/과 input/departments/를
    타임스탬프 백업 폴더로 옮겨두고 두 폴더를 비운다 (기존 분류/추천/확정 데이터 보호)."""
    has_existing = MASTER_PATH.exists() or any(DEPARTMENTS_DIR.glob("*"))
    if not has_existing:
        return None

    ts = datetime.now(KST).strftime("%Y%m%d-%H%M%S")
    backup_dir = BASE_DIR / f"output_backup_{ts}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    if OUTPUT_DIR.exists():
        shutil.copytree(OUTPUT_DIR, backup_dir / "output", dirs_exist_ok=True)
        for item in OUTPUT_DIR.iterdir():
            if item.name.lower() == "readme.md":
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()

    if DEPARTMENTS_DIR.exists():
        shutil.copytree(DEPARTMENTS_DIR, backup_dir / "departments", dirs_exist_ok=True)
        for item in DEPARTMENTS_DIR.iterdir():
            if item.name.lower() == "readme.md":
                continue
            if item.is_dir():
                # 참조용 하위 폴더(예: "참고자료/")는 부서 원본이 아니므로 보존한다.
                # scan_accounts.py/segment_accounts.py는 이 디렉토리를 재귀 탐색하지 않으므로
                # 남겨둬도 Phase 0/0.5 스캔에 영향이 없다.
                continue
            item.unlink()

    return backup_dir


def load_sample_data() -> bool:
    """API 키·실행 없이 즉시 둘러볼 수 있도록, 이미 AI로 끝까지 처리해둔 샘플 결과
    (sample_data/)를 output/에 복사한다. 면접관 등 제3자가 크레딧 소모나 키 등록 없이
    실제 판단 결과를 바로 볼 수 있게 하기 위한 경로다 — Phase 0~1 재실행이 아니다."""
    master_src = SAMPLE_DATA_DIR / "accounts_master.json"
    seg_src = SAMPLE_DATA_DIR / "account_segmentation.json"
    if not master_src.exists() or not seg_src.exists():
        return False

    backup_and_reset()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy(master_src, MASTER_PATH)
    shutil.copy(seg_src, SEG_PATH)
    return True


def save_chunks(chunks: list[dict], dept_name_by_key: dict[str, str]) -> list[str]:
    """확정된 부서명으로 청크를 묶어 input/departments/에 저장한다.
    같은 부서명으로 확정된 청크(예: 같은 부서가 여러 시트/파일에 나뉘어 있는 경우)는 하나로 합친다.
    파일명/시트명이 아니라 실제 데이터에 쓰이는 부서 컬럼 값을 확정값으로 덮어써서,
    화면에서 오인식을 고쳤을 때 스캔 결과에도 그대로 반영되도록 한다."""
    DEPARTMENTS_DIR.mkdir(parents=True, exist_ok=True)
    frames_by_dept: dict[str, list[pd.DataFrame]] = {}
    for chunk in chunks:
        dept_name = dept_name_by_key[chunk["key"]].strip()
        df = chunk["df"].copy()
        df["부서명"] = dept_name
        frames_by_dept.setdefault(dept_name, []).append(df)

    saved_depts = []
    for dept_name, frames in frames_by_dept.items():
        combined = pd.concat(frames, ignore_index=True)
        out_path = DEPARTMENTS_DIR / f"{dept_name}.csv"
        combined.to_csv(out_path, index=False, encoding="utf-8-sig")
        saved_depts.append(dept_name)
    return saved_depts


def run_phase0() -> None:
    """Phase 0(계정 인벤토리 스캔) — scan_accounts.py의 scan()을 그대로 재사용한다."""
    master_dict = scan_accounts.scan(str(DEPARTMENTS_DIR))
    MASTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MASTER_PATH, "w", encoding="utf-8") as f:
        json.dump(master_dict, f, ensure_ascii=False, indent=2)


def run_phase05() -> dict:
    """Phase 0.5(공통/특정 대분류 규칙기반 분리) — 2개 이상 부서 등장=공통, 1개=특정."""
    segment_accounts.analyze(str(DEPARTMENTS_DIR), str(MASTER_PATH), str(SEG_PATH), min_depts=2)

    with open(SEG_PATH, encoding="utf-8") as f:
        seg = json.load(f)

    return {
        "부서": seg.get("departments_scanned", []),
        "공통대분류": len(seg.get("common_categories", [])),
        "특정대분류": len(seg.get("department_specific_categories", [])),
        "공통특정_추가판단필요(LLM_재확인_필요)": len(seg.get("needs_llm_recheck", [])),
    }


def apply_phase05_verdicts(new_verdicts: list[dict]) -> None:
    """Phase 0.5 재확인 판정을 llm_verdicts.json에 누적하고 segment_accounts.apply_llm()으로 반영한다."""
    existing: list[dict] = []
    if VERDICTS_PATH.exists():
        with open(VERDICTS_PATH, encoding="utf-8") as f:
            existing = json.load(f)

    by_category = {v["대분류"]: v for v in existing}
    for verdict in new_verdicts:
        by_category[verdict["대분류"]] = verdict

    VERDICTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(VERDICTS_PATH, "w", encoding="utf-8") as f:
        json.dump(list(by_category.values()), f, ensure_ascii=False, indent=2)

    segment_accounts.apply_llm(
        str(SEG_PATH), str(VERDICTS_PATH), str(MASTER_PATH), str(SEG_PATH),
    )
    reload_from_disk()


def render_upload_sidebar():
    st.sidebar.header("📁 새 배치 업로드")
    st.sidebar.caption(
        "부서별 비용 엑셀/CSV를 여러 개 한 번에 올리고 '분석 시작'을 누르면, "
        "기존 데이터는 output_backup_*/에 백업된 뒤 새로 스캔·분리됩니다. "
        "부서 1개당 파일 1개든, 시트마다 부서가 나뉜 워크북 1개든 모두 지원합니다."
    )
    uploaded_files = st.sidebar.file_uploader(
        "부서 파일 선택 (xlsx / csv)", type=["xlsx", "csv"],
        accept_multiple_files=True, key="dept_file_uploader",
    )

    if not uploaded_files:
        return

    errors = []
    chunks: list[dict] = []
    for uploaded_file in uploaded_files:
        try:
            chunks.extend(extract_chunks(uploaded_file))
        except Exception as e:
            errors.append(f"'{uploaded_file.name}': 읽기 실패 ({e})")

    for chunk in chunks:
        missing = validate_chunk_df(chunk["df"])
        if missing:
            source = chunk["file"] if chunk["sheet"] is None else f"{chunk['file']} · {chunk['sheet']}"
            errors.append(f"'{source}': 필수 컬럼 누락 {missing} (계정코드/계정명/금액은 반드시 있어야 합니다)")

    if errors:
        for e in errors:
            st.sidebar.error(e)
        return

    st.sidebar.markdown(f"**{len(chunks)}개 부서 인식됨 — 부서명 확인/수정**")
    table_rows = [{
        "출처": chunk["file"] if chunk["sheet"] is None else f"{chunk['file']} · {chunk['sheet']}",
        "행수": len(chunk["df"]),
        "부서명": chunk["dept_guess"],
        "_key": chunk["key"],
    } for chunk in chunks]
    edited = st.sidebar.data_editor(
        pd.DataFrame(table_rows), hide_index=True, key="dept_name_editor",
        column_config={
            "출처": st.column_config.TextColumn(disabled=True),
            "행수": st.column_config.NumberColumn(disabled=True),
            "_key": None,  # 화면에는 숨기고 내부 매칭용으로만 사용
        },
    )
    dept_name_by_key = dict(zip(edited["_key"], edited["부서명"]))

    if st.sidebar.button(f"🚀 분석 시작 ({len(chunks)}개 부서)", type="primary"):
        with st.status("배치 분석 진행 중...", expanded=True) as status:
            status.write("1/4 · 기존 데이터 백업 중...")
            backup_dir = backup_and_reset()
            status.write("2/4 · 업로드 파일 저장 중...")
            save_chunks(chunks, dept_name_by_key)
            status.write("3/4 · Phase 0 — 계정 인벤토리 스캔 중...")
            run_phase0()
            status.write("4/4 · Phase 0.5 — 공통/특정 대분류 분리 중...")
            summary = run_phase05()
            status.update(label="✅ 분석 완료", state="complete", expanded=False)

        for key in ("loaded", "seg", "master", "data", "confirmations"):
            st.session_state.pop(key, None)

        if backup_dir:
            st.sidebar.success(f"완료! 기존 데이터는 {backup_dir.name}/에 백업했습니다.")
        else:
            st.sidebar.success("완료!")
        st.sidebar.json(summary)
        if summary["공통특정_추가판단필요(LLM_재확인_필요)"]:
            st.sidebar.warning(
                f"이전 분류 결과에서 재확인 대기 {summary['공통특정_추가판단필요(LLM_재확인_필요)']}건 — "
                "「전체」 탭 상단 Phase 0.5 재확인에서 처리하세요."
            )
        if ai_pipeline.is_configured():
            st.sidebar.info(
                "대분류 4-type 분류·원가동인 추천(Phase 1)은 「전체」 탭의 "
                "「🤖 AI 분류·원가동인 추천 시작」 버튼으로 이 화면에서 바로 실행할 수 있습니다."
            )
        else:
            st.sidebar.info(
                "대분류 4-type 분류·원가동인 추천(Phase 1)을 이 화면에서 바로 실행하려면 "
                "ANTHROPIC_API_KEY를 설정하세요(README 참고). 설정 후 「전체」 탭에 "
                "「🤖 AI 분류·원가동인 추천 시작」 버튼이 나타납니다."
            )
        st.rerun()


# ---------------------------------------------------------------------------
# 산출물 다운로드 (사이드바)
# ---------------------------------------------------------------------------

def build_live_summary_md(data: dict, master: dict, confirmations: dict) -> str:
    """현재 세션 상태로 데모 요약 리포트를 즉석에서 생성한다(파일에 의존하지 않아 항상 최신)."""
    categories = data["categories"]
    eff = compute_efficiency_stats(categories)
    common_n = sum(1 for c in categories if c["scope"] == "common")
    specific_n = sum(1 for c in categories if c["scope"] == "specific")
    needs_review_n = sum(1 for c in categories if c["needsReview"])
    done_n = sum(1 for c in categories if is_done_category(c, confirmations[f"category:{c['category']}"]))
    done_pct = (done_n / len(categories) * 100) if categories else 0.0

    four_type_counts: dict[str, int] = {}
    for c in categories:
        key = c["fourType"] or "추가판단 필요"
        four_type_counts[key] = four_type_counts.get(key, 0) + 1

    judgment_path_counts: dict[str, int] = {}
    for cat in master.get("categories", {}).values():
        key = cat.get("판단경로") or "미분류(대기)"
        judgment_path_counts[key] = judgment_path_counts.get(key, 0) + 1
    judgment_path_total = sum(judgment_path_counts.values())

    lines = [
        f"# 원가동인 확정 — 데모 요약 리포트",
        "",
        f"- 생성 시각: {datetime.now(KST).strftime('%Y-%m-%d %H:%M')}",
        f"- 스캔 부서: {len(data['departments'])}개 ({', '.join(data['departments'])})",
        f"- 총 세부계정 수: {len(master.get('accounts', {}))}건",
        "",
        "## 처리 현황",
        f"- 공통 대분류 {common_n}건 · 특정 대분류 {specific_n}건 · 추가판단 필요 {needs_review_n}건",
        f"- 확정 완료율: {done_n} / {len(categories)}건 ({done_pct:.0f}%)",
        "",
        "## 공통 대분류 자동 판별 효율화 효과",
        (
            f"공통 대분류 {eff['common_count']}개 × 평균 {eff['avg_depts']:.1f}개 부서 = "
            f"{eff['total_touchpoints']}건의 개별 판단을 {eff['common_count']}건으로 축소 "
            f"({eff['reduction_pct']:.0f}% 절감, 중복 판단 {eff['reduction']}건 제거)"
        ),
        "",
        "## 판단 경로 — AI 판단 vs 규칙 기반 폴백",
        (
            "account-classifier/driver-recommender 서브에이전트가 실제로 판단한 대분류는 "
            "\"AI 판단\", phase1_apply.py(로컬 규칙 엔진, 서브에이전트 미사용 PoC 폴백)가 대신 채운 "
            "대분류는 \"규칙 기반 폴백\"으로 표시된다. 최종 엑셀의 \"판단 경로\" 컬럼과 동일한 값이다."
        ),
    ]
    for label, n in sorted(judgment_path_counts.items(), key=lambda kv: -kv[1]):
        pct = (n / judgment_path_total * 100) if judgment_path_total else 0.0
        lines.append(f"- {label}: {n}건 ({pct:.0f}%)")

    lines += [
        "",
        "## 원가동인 유형(4-type) 분포",
    ]
    for ft, n in sorted(four_type_counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"- {ft}: {n}건")

    lines += ["", "## 부서별 확정 진행률"]
    for dept in data["departments"]:
        sub_done, sub_total, pct = dept_progress_pct(categories, dept, confirmations)
        lines.append(f"- {dept}: {sub_done} / {sub_total}건 ({pct * 100:.0f}%)")

    return "\n".join(lines) + "\n"


def render_download_sidebar():
    if not st.session_state.get("loaded"):
        return
    st.sidebar.header("📥 산출물 다운로드")

    data = st.session_state.data
    master = st.session_state.master
    confirmations = st.session_state.confirmations
    batch_id = compute_batch_id(data)

    if st.sidebar.button("📊 최종 확정 결과 엑셀 다운로드", use_container_width=True):
        all_records = build_all_dept_records(master, data["departments"])
        out_path = OUTPUT_DIR / f"원가동인_확정결과_{batch_id}.xlsx"
        import importlib
        importlib.reload(write_results)
        write_results.write_workbook(
            all_records, out_path, overwrite=True, export_mode="report", dept_label="전체",
        )
        st.session_state["_excel_download_path"] = str(out_path)

    excel_path = st.session_state.get("_excel_download_path")
    if excel_path and Path(excel_path).exists():
        with open(excel_path, "rb") as f:
            st.sidebar.download_button(
                "⬇ 다운로드 받기 (엑셀)", data=f.read(), file_name=Path(excel_path).name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )

    summary_md = build_live_summary_md(data, master, confirmations)
    st.sidebar.download_button(
        "📝 데모 요약 리포트 다운로드", data=summary_md,
        file_name=f"demo_summary_{batch_id}.md", mime="text/markdown",
        use_container_width=True,
    )


# ---------------------------------------------------------------------------
# 상태 판정
# ---------------------------------------------------------------------------

def is_done_category(cat_item: dict, conf: dict) -> bool:
    if cat_item["needsReview"]:
        return conf["confirmStatus"] == "확정" and bool(conf["confirmedDriver"])
    return conf["confirmStatus"] in ("승인", "수정")


def is_done_subaccount(conf: dict) -> bool:
    """예외지정 세부계정만 독립 판정한다. 원칙준용이면 부모 대분류의 확정 여부를 따른다."""
    if conf["applyMode"] != "예외지정":
        return None
    return conf["confirmStatus"] == "확정" and bool(conf["confirmedDriver"])


# ---------------------------------------------------------------------------
# 뱃지 컴포넌트 (통합) — 상태/구분/적용방식/추천출처 뱃지를 전부 이 함수 하나로 렌더한다.
# 상태 3종(confirmed/pending/needs_review)만 색상+이모지+텍스트 3중 표기로, 나머지는
# 정보성 뱃지로 색상은 절제하고 텍스트로 구분한다(회계법인 톤 통일 + 색맹/흑백 인쇄 대응).
# ---------------------------------------------------------------------------

BADGE_SPECS: dict[str, tuple[str, str, str]] = {
    # kind: (이모지, 기본 라벨, CSS 클래스)
    "confirmed": ("🟢", "확정됨", "badge-confirmed"),
    "pending": ("🔴", "미확정", "badge-pending"),
    "needs_review": ("🟡", "추가판단 필요", "badge-needs-review"),
    "principle": ("", "원칙 준용", "badge-principle"),
    "exception": ("", "예외 지정", "badge-exception"),
    "scope_common": ("", "공통", "badge-scope-common"),
    "scope_specific": ("", "특정", "badge-scope-specific"),
    "ai_rec": ("", "AI 추천", "badge-rec-ai"),
    "auto_rec": ("", "자동 추천", "badge-rec-auto"),
}


def render_badge(kind: str, text: str | None = None) -> str:
    icon, default_label, css_class = BADGE_SPECS[kind]
    label = text if text is not None else default_label
    prefix = f"{icon} " if icon else ""
    return f'<span class="{css_class}">{prefix}{html.escape(label)}</span>'


def status_badge_html(done: bool, rank: int | None = None, custom: bool = False) -> str:
    if not done:
        return render_badge("pending")
    if rank:
        return render_badge("confirmed", f"확정됨 · {rank}순위")
    if custom:
        return render_badge("confirmed", "확정됨 · 직접입력")
    return render_badge("confirmed")


# ---------------------------------------------------------------------------
# 저장(파이프라인 반영) 로직
# ---------------------------------------------------------------------------

def build_payload() -> dict:
    data = st.session_state.data
    confirmations = st.session_state.confirmations

    categories_out = []
    for cat_item in data["categories"]:
        category = cat_item["category"]
        conf = confirmations[f"category:{category}"]
        out = {"category": category, "확정여부": conf["confirmStatus"], "확정원가동인": conf["confirmedDriver"] or ""}
        if cat_item["needsReview"]:
            out["사람확정four_type"] = conf["humanFourType"] or ""
        else:
            out["확정순위"] = conf["confirmedRank"]
        categories_out.append(out)

    sub_accounts_out = []
    for cat_item in data["categories"]:
        for sa in cat_item["subAccounts"]:
            key = f"account:{sa['code']}"
            conf = confirmations[key]
            item = {
                "category": cat_item["category"], "계정코드": sa["code"],
                "적용방식": conf["applyMode"],
            }
            if conf["applyMode"] == "예외지정":
                item["확정여부"] = conf["confirmStatus"]
                item["확정four_type"] = conf["confirmedFourType"] or ""
                item["확정원가동인"] = conf["confirmedDriver"] or ""
            sub_accounts_out.append(item)

    return {"generated_at": datetime.now(KST).isoformat(), "categories": categories_out, "sub_accounts": sub_accounts_out}


def build_dept_records(master: dict, dept: str) -> list[dict]:
    records = []
    for code, acc in master.get("accounts", {}).items():
        if dept not in acc.get("등장부서", []):
            continue
        eff = resolve_account_effective(master, code)
        rec = {
            "대분류": acc.get("대분류"),
            "계정코드": code,
            "계정명": acc["계정명"],
            "계정설명": (acc.get("설명") or {}).get("내용", ""),
            "부서명": dept,
            "적용방식": eff["적용방식"],
            "추가판단필요여부": eff.get("추가판단필요여부", False),
            "four_type": eff.get("four_type"),
            "분류근거": eff.get("분류근거"),
            "판단경로": eff.get("판단경로"),
            "검토필요": False,
            "recommended_drivers": (eff.get("원가동인") or {}).get("recommended_drivers", []),
        }
        confirm = eff.get("회계사확정")
        if confirm:
            rec["확정여부"] = confirm.get("확정여부")
            rec["확정원가동인"] = confirm.get("확정원가동인")
            rec["확정순위"] = confirm.get("확정순위")
        records.append(rec)
    return records


def build_all_dept_records(master: dict, departments: list[str]) -> list[dict]:
    """모든 부서의 build_dept_records를 이어붙인다 — 통합 다운로드 워크북용."""
    records = []
    for dept in departments:
        records.extend(build_dept_records(master, dept))
    return records


def save_all() -> dict:
    payload = build_payload()
    CONFIRMED_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIRMED_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    result = track_batch.apply_browser_confirmations(str(MASTER_PATH), str(CONFIRMED_PATH))

    reload_from_disk()
    master = st.session_state.master
    batch_id = compute_batch_id(st.session_state.data)

    written = {}
    import importlib
    importlib.reload(write_results)
    for dept in st.session_state.data["departments"]:
        records = build_dept_records(master, dept)
        out_path = OUTPUT_DIR / f"batch_{batch_id}-{dept}_final.xlsx"
        total = write_results.write_workbook(records, out_path, overwrite=True, export_mode="report")
        written[dept] = total

    if DRAFT_PATH.exists():
        DRAFT_PATH.unlink()

    return {"apply_result": result, "excel_written": written}


# ---------------------------------------------------------------------------
# UI: 진행률 / 요약 카드
# ---------------------------------------------------------------------------

def compute_efficiency_stats(categories: list[dict]) -> dict:
    """공통 대분류 자동 판별로 줄어든 개별 판단 건수를 계산한다.
    "부서마다 따로 판단했다면 필요했을 건수(부서 접점 합계)"와 "실제로 필요한 판단 건수
    (대분류 수)"의 차이가 절감 효과다."""
    common_cats = [c for c in categories if c["scope"] == "common"]
    n = len(common_cats)
    total_touchpoints = sum(len(c["departments"]) for c in common_cats)
    avg_depts = (total_touchpoints / n) if n else 0.0
    reduction = total_touchpoints - n
    reduction_pct = (reduction / total_touchpoints * 100) if total_touchpoints else 0.0
    return {
        "common_count": n,
        "avg_depts": avg_depts,
        "total_touchpoints": total_touchpoints,
        "reduction": reduction,
        "reduction_pct": reduction_pct,
    }


def render_progress_and_summary(categories: list[dict], confirmations: dict, label: str = "전체 확정 진행률"):
    total = len(categories)
    done = sum(1 for c in categories if is_done_category(c, confirmations[f"category:{c['category']}"]))
    pct = (done / total) if total else 0.0
    st.progress(pct, text=f"{label}: {done} / {total} 확정 ({pct * 100:.0f}%)")

    common_n = sum(1 for c in categories if c["scope"] == "common")
    specific_n = sum(1 for c in categories if c["scope"] == "specific")
    needs_review_n = sum(1 for c in categories if c["needsReview"])

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("공통 대분류", common_n)
    c2.metric("특정 대분류", specific_n)
    c3.metric("추가판단 필요 대분류", needs_review_n)
    c4.metric("확정 완료", done)


def dept_progress_pct(categories: list[dict], dept: str, confirmations: dict) -> tuple[int, int, float]:
    """이 부서에 해당하는 세부계정 중 확정된 건수/전체건수/비율을 계산한다.
    대시보드 탭과 부서별 탭이 이 함수 하나를 공유한다."""
    sub_total = 0
    sub_done = 0
    for cat_item in categories:
        if dept not in cat_item.get("departments", []):
            continue
        for sa in cat_item["subAccounts"]:
            if dept not in sa.get("departments", []):
                continue
            sub_total += 1
            eff = effective_display(cat_item, sa, confirmations)
            if eff["confirmStatus"] in ("승인", "수정", "확정"):
                sub_done += 1
    pct = (sub_done / sub_total) if sub_total else 0.0
    return sub_done, sub_total, pct


def render_dept_progress(categories_for_dept: list[dict], dept: str, confirmations: dict):
    sub_done, sub_total, pct = dept_progress_pct(categories_for_dept, dept, confirmations)
    st.progress(pct, text=f"{dept} 확정 진행률: {sub_done} / {sub_total} 확정 ({pct * 100:.0f}%)")


# ---------------------------------------------------------------------------
# UI: 대분류 카드 (공통/특정 공용) — 확정 시 세부계정이 하위에 펼쳐짐
# ---------------------------------------------------------------------------

def render_needs_review_category_form(cat_item: dict, conf: dict, key_prefix: str, nature_input: dict):
    suggested = suggest_local_driver(nature_input)
    col1, col2, col3 = st.columns([1, 2, 1])
    with col1:
        options = [""] + FOUR_TYPES
        current = conf["humanFourType"] if conf["humanFourType"] in FOUR_TYPES else ""
        four_type_choice = st.selectbox(
            "4-type 확정", options, index=options.index(current), key=f"ftype_{key_prefix}",
        )
    with col2:
        driver_val = st.text_input(
            "확정 원가동인 (선택 — 입력하면 즉시 확정)",
            value=conf["confirmedDriver"],
            key=f"amb_driver_{key_prefix}",
            placeholder=suggested or "원가동인 직접 입력",
        )
        if suggested and not conf["confirmedDriver"]:
            st.caption(f"자동 추천 참고: {suggested}")
    with col3:
        st.write("")
        st.write("")
        if st.button("저장", key=f"amb_save_{key_prefix}"):
            if not four_type_choice:
                st.error("4-type을 선택해야 저장할 수 있습니다.")
            else:
                conf["humanFourType"] = four_type_choice
                if driver_val.strip():
                    conf["confirmStatus"] = "확정"
                    conf["confirmedDriver"] = driver_val.strip()
                else:
                    conf["confirmStatus"] = "미확정"
                    conf["confirmedDriver"] = ""
                persist_draft()
                st.rerun()


def render_sub_accounts_expander(cat_item: dict, cat_done: bool):
    sub_accounts = cat_item["subAccounts"]
    with st.expander(f"세부계정 {len(sub_accounts)}건 보기"):
        for sa in sub_accounts:
            key = f"account:{sa['code']}"
            conf = st.session_state.confirmations[key]
            apply_mode = conf["applyMode"]
            depts = sa.get("departments", [])
            dept_label = depts[0] if len(depts) == 1 else f"{len(depts)}개 부서 공통"

            row_l, row_r = st.columns([4, 1])
            with row_l:
                badge = render_badge("exception" if apply_mode == "예외지정" else "principle")
                st.markdown(
                    f'{badge}<strong>{html.escape(sa["name"])}</strong> '
                    f'<span class="acct-code">({sa["code"]} · {dept_label})</span>',
                    unsafe_allow_html=True,
                )
                desc = (sa.get("description") or {}).get("내용")
                if desc:
                    st.caption(f"📝 {desc}")
            with row_r:
                toggle_label = "원칙 준용으로" if apply_mode == "예외지정" else "예외로 지정"
                if st.button(toggle_label, key=f"toggle_{key}"):
                    conf["applyMode"] = "원칙준용" if apply_mode == "예외지정" else "예외지정"
                    persist_draft()
                    st.rerun()

            if apply_mode == "예외지정":
                ec1, ec2, ec3 = st.columns([1, 2, 1])
                with ec1:
                    options = [""] + FOUR_TYPES
                    current = conf["confirmedFourType"] if conf["confirmedFourType"] in FOUR_TYPES else ""
                    ftype = st.selectbox(
                        "4-type", options, index=options.index(current),
                        key=f"exc_ftype_{key}", label_visibility="collapsed",
                    )
                with ec2:
                    driver_val = st.text_input(
                        "확정 원가동인", value=conf["confirmedDriver"], key=f"exc_driver_{key}",
                        label_visibility="collapsed", placeholder="이 계정만 다른 원가동인",
                    )
                with ec3:
                    if st.button("예외 확정", key=f"exc_save_{key}"):
                        if not ftype or not driver_val.strip():
                            st.error("4-type과 원가동인을 모두 입력해야 합니다.")
                        else:
                            conf["confirmedFourType"] = ftype
                            conf["confirmedDriver"] = driver_val.strip()
                            conf["confirmStatus"] = "확정"
                            persist_draft()
                            st.rerun()
                done_sub = is_done_subaccount(conf)
                st.markdown(status_badge_html(bool(done_sub)), unsafe_allow_html=True)
            else:
                st.caption("이 계정은 대분류 확정값을 그대로 상속합니다." + ("" if cat_done else " (대분류 미확정 — 확정 대기)"))
            st.divider()


def render_category_card(cat_item: dict):
    category = cat_item["category"]
    key_prefix = f"category:{category}"
    conf = st.session_state.confirmations[key_prefix]
    needs_review = cat_item["needsReview"]
    done = is_done_category(cat_item, conf)

    total_depts = len(st.session_state.data["departments"])
    if cat_item["scope"] == "common":
        n = len(cat_item["departments"])
        scope_label = "전체 부서 공통" if n == total_depts else f"{n}개 부서 공통"
        scope_badge = render_badge("scope_common", text=scope_label)
    else:
        scope_label = cat_item["departments"][0] if cat_item["departments"] else ""
        scope_badge = render_badge("scope_specific", text=scope_label)

    with st.container(border=True):
        head_l, head_r = st.columns([5, 1])
        with head_l:
            warn_badge = render_badge("needs_review") if needs_review else ""
            st.markdown(
                f'{scope_badge}'
                f'{warn_badge}'
                f'<span class="acct-title">{html.escape(category)}</span> '
                f'<span class="acct-code">(세부계정 {len(cat_item["subAccounts"])}건)</span>',
                unsafe_allow_html=True,
            )
        with head_r:
            badge_html = (
                status_badge_html(done) if needs_review
                else status_badge_html(done, conf.get("confirmedRank"), conf.get("confirmStatus") == "수정")
            )
            st.markdown(badge_html, unsafe_allow_html=True)

        amount_by_dept = st.session_state.get("amount_by_dept", {})
        nature_input = category_to_nature_input(cat_item, amount_by_dept)
        render_nature_block(nature_input, cat_item.get("reason") or "")

        if needs_review:
            render_needs_review_category_form(cat_item, conf, key_prefix, nature_input)
        else:
            render_driver_section(cat_item, conf, key_prefix, nature_input)

        render_sub_accounts_expander(cat_item, done)


# ---------------------------------------------------------------------------
# Tab 1: 전체
# ---------------------------------------------------------------------------

def _try_start_ai_run() -> bool:
    """AI 실행 버튼 클릭 시 API 키·사용량 한도를 확인한다. 통과하면 카운터를 올리고 True,
    막히면 안내 메시지를 표시하고 False를 반환한다 — 공개 배포 시 API 비용 노출 방지용."""
    if not ai_pipeline.is_configured():
        st.error(
            "ANTHROPIC_API_KEY가 설정되지 않아 AI 기능을 쓸 수 없습니다. 로컬에서는 프로젝트 "
            "루트의 `.env` 파일에, 배포 환경에서는 Streamlit Cloud 앱 설정의 Secrets에 등록하세요."
        )
        return False
    blocked = ai_pipeline.check_usage_limits(st.session_state.get("ai_run_count", 0))
    if blocked:
        st.warning(blocked)
        return False
    st.session_state["ai_run_count"] = st.session_state.get("ai_run_count", 0) + 1
    ai_pipeline.increment_daily_usage()
    return True


def _render_ai_usage_caption():
    remaining_session = max(0, ai_pipeline.SESSION_RUN_LIMIT - st.session_state.get("ai_run_count", 0))
    remaining_daily = max(0, ai_pipeline.DAILY_GLOBAL_LIMIT - ai_pipeline.read_daily_usage())
    st.caption(
        f"🔑 AI 실행 가능 횟수 — 이 브라우저 세션: {remaining_session}/{ai_pipeline.SESSION_RUN_LIMIT}회 · "
        f"오늘 전체 방문자: {remaining_daily}/{ai_pipeline.DAILY_GLOBAL_LIMIT}회 "
        "(공개 데모의 API 비용 보호를 위한 한도이며, 로컬 실행에는 적용해도 큰 의미가 없어 원하면 "
        "ai_pipeline.py 상단 상수로 조정할 수 있습니다)"
    )


def render_ai_phase1_section(data: dict):
    """Phase 1(4-type 분류 → 원가동인 추천 → 자기검증)을 Claude Code 세션 없이 이 화면에서
    Anthropic API로 직접 실행한다. Phase 0.5(공통/특정 재확인)가 끝나야 대상이 된다."""
    if data.get("needs_llm_recheck"):
        return

    result = st.session_state.pop("last_ai_phase1_result", None)
    if result:
        st.success(
            f"🤖 AI 파이프라인 완료 — 4-type 분류 {result['분류완료']}건"
            f"(추가판단 필요 {result['추가판단필요']}건 포함) · 원가동인 추천·검증 통과 {result['추천완료']}건"
        )
        if result["검증실패_에스컬레이션"]:
            with st.expander(
                f"⚠ 검증 실패로 보류된 대분류 {len(result['검증실패_에스컬레이션'])}건 "
                "(재시도 2회 초과 — 회계사 확인 필요)", expanded=True,
            ):
                for item in result["검증실패_에스컬레이션"]:
                    st.markdown(f"- **{item['대분류']}**: {item['사유']}")

    categories_state = st.session_state.master.get("categories", {})
    todo = [c for c, s in categories_state.items() if s.get("카테고리분류상태") != "분류완료"]
    if not todo:
        return

    st.subheader(f"🤖 AI 분류·원가동인 추천 ({len(todo)}건 대기)")
    st.caption(
        "Anthropic API를 이 화면에서 직접 호출해 4-type 분류 → 원가동인 1~3순위 추천 → "
        "자기검증까지 실행합니다. Claude Code 세션이나 개발자 개입이 필요 없습니다."
    )
    _render_ai_usage_caption()

    if not ai_pipeline.is_configured():
        st.info(
            "ANTHROPIC_API_KEY가 설정되지 않았습니다. 로컬 실행 시 프로젝트 루트에 `.env` 파일을 "
            "만들어 `ANTHROPIC_API_KEY=sk-...`를 추가하거나, Streamlit Cloud 배포라면 앱 설정의 "
            "Secrets에 등록하세요."
        )
        return

    if st.button(f"🤖 AI 분류·원가동인 추천 시작 ({len(todo)}건)", type="primary", key="run_ai_phase1"):
        if not _try_start_ai_run():
            return
        with st.status(f"AI 파이프라인 실행 중... (대상 {len(todo)}건)", expanded=True) as status:
            def _cb(i, total, msg):
                status.write(f"[{i + 1}/{total}] {msg}")

            try:
                run_result = ai_pipeline.run_phase1_pipeline(MASTER_PATH, LOG_PATH, progress_cb=_cb)
            except Exception as e:  # AIPipelineError뿐 아니라 예상 못한 실패도 화면을 깨뜨리지 않는다
                status.update(label=f"❌ 실패: {e}", state="error", expanded=True)
                return
            status.update(label="✅ AI 파이프라인 완료", state="complete", expanded=False)

        st.session_state["last_ai_phase1_result"] = run_result
        reload_from_disk()
        st.rerun()


def _default_recheck_verdict(item: dict) -> dict:
    """Phase 0.5 재확인 대기(legacy) 대분류용 기본 판정."""
    name = item.get("대분류") or ""
    flag = item.get("플래그_사유") or ""
    extra = f" ({flag})" if flag else ""
    return {
        "판정": "공통유지",
        "사유": f"「{name}」은 2개 이상 부서에 등장하는 공통 후보 대분류{extra}로, 공통 대분류로 묶어 원가동인을 검토합니다.",
    }


def render_llm_recheck_section(data: dict):
    pending = data.get("needs_llm_recheck") or []
    if not pending:
        return

    st.warning(
        f"⚠ Phase 0.5 미완료 — 공통/특정 여부 재확인 대기 **{len(pending)}건**. "
        "아래에서 판정하면 경고가 사라지고 공통/특정 목록에 반영됩니다."
    )
    st.subheader(f"0. Phase 0.5 재확인 — 공통/특정 판정 ({len(pending)}건)")
    st.caption(
        "이전 분류 결과에서 재확인 대기로 남은 대분류입니다. "
        "**공통유지** = 하나의 공통 대분류로 묶음 · **특정전환** = 부서별로 따로 판단."
    )

    if st.button(f"🤖 AI 공통/특정 판정 시작 ({len(pending)}건)", type="primary", key="bulk_ai_recheck"):
        if _try_start_ai_run():
            with st.status(f"AI 판정 중... (대상 {len(pending)}건)", expanded=True) as status:
                def _cb(i, total, msg):
                    status.write(f"[{i + 1}/{total}] {msg}")

                try:
                    ai_pipeline.run_phase05_recheck(SEG_PATH, VERDICTS_PATH, MASTER_PATH, progress_cb=_cb)
                except Exception as e:  # AIPipelineError뿐 아니라 예상 못한 실패도 화면을 깨뜨리지 않는다
                    status.update(label=f"❌ 실패: {e}", state="error", expanded=True)
                else:
                    status.update(label="✅ AI 판정 완료", state="complete", expanded=False)
                    reload_from_disk()
                    st.rerun()
    _render_ai_usage_caption()

    for item in pending:
        category = item["대분류"]
        suggestion = _default_recheck_verdict(item)
        amounts = item.get("부서별_금액") or {}
        amount_rows = [
            {"부서": dept, "금액": f"{int(amt):,}원", "_sort": amt}
            for dept, amt in amounts.items()
        ]
        amount_rows.sort(key=lambda r: r["_sort"], reverse=True)
        amount_df = pd.DataFrame([{k: v for k, v in r.items() if k != "_sort"} for r in amount_rows])

        with st.expander(f"{category} — {item.get('플래그_사유', '')}", expanded=len(pending) <= 3):
            st.markdown(f"**1차 판정:** {item.get('규칙기반_1차판정', '공통후보')} · **참고 추천:** `{suggestion['판정']}`")
            st.dataframe(amount_df, hide_index=True, use_container_width=True)
            reason_key = f"phase05_reason_{category}"
            if reason_key not in st.session_state:
                st.session_state[reason_key] = suggestion["사유"]
            reason = st.text_area("판정 사유", key=reason_key, height=80)

            c1, c2 = st.columns(2)
            with c1:
                if st.button("공통유지로 확정", key=f"phase05_common_{category}", type="primary"):
                    with st.spinner("반영 중..."):
                        apply_phase05_verdicts([{"대분류": category, "판정": "공통유지", "사유": reason.strip()}])
                    st.rerun()
            with c2:
                if st.button("특정전환으로 확정", key=f"phase05_specific_{category}"):
                    with st.spinner("반영 중..."):
                        apply_phase05_verdicts([{"대분류": category, "판정": "특정전환", "사유": reason.strip()}])
                    st.rerun()


def render_dashboard_tab():
    """요약 대시보드 — 처음 보는 사람이 스크린샷 한 장으로 이 도구의 가치를 이해할 수 있게
    하는 화면. 확정 작업이 진행될수록 st.session_state를 다시 읽어 실시간으로 갱신된다."""
    data = st.session_state.data
    master = st.session_state.master
    confirmations = st.session_state.confirmations
    categories = data["categories"]

    render_pipeline_banner(data, categories)

    total_accounts = len(master.get("accounts", {}))
    common_n = sum(1 for c in categories if c["scope"] == "common")
    specific_n = sum(1 for c in categories if c["scope"] == "specific")
    needs_review_n = sum(1 for c in categories if c["needsReview"])
    done_n = sum(1 for c in categories if is_done_category(c, confirmations[f"category:{c['category']}"]))
    done_pct = (done_n / len(categories) * 100) if categories else 0.0

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("총 세부계정 수", f"{total_accounts}건")
    c2.metric("공통 대분류", f"{common_n}건")
    c3.metric("특정 대분류", f"{specific_n}건")
    c4.metric("확정 완료율", f"{done_pct:.0f}%")
    c5.metric("추가판단 필요", f"{needs_review_n}건")

    eff = compute_efficiency_stats(categories)
    if eff["common_count"]:
        st.markdown(
            f"""
            <div class="efficiency-box">
              <div class="eff-label">공통 대분류 자동 판별 효과</div>
              <div class="eff-main">
                공통 대분류 <b>{eff['common_count']}개</b> × 평균 <b>{eff['avg_depts']:.1f}개 부서</b>
                = <b>{eff['total_touchpoints']}건</b>의 개별 판단을 <b>{eff['common_count']}건</b>으로 축소
                (<b>{eff['reduction_pct']:.0f}%</b> 절감 · 중복 판단 {eff['reduction']}건 제거)
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    chart_col1, chart_col2 = st.columns(2)
    with chart_col1:
        st.markdown('<div class="section-title">원가동인 유형(4-type) 분포</div>', unsafe_allow_html=True)
        four_type_counts: dict[str, int] = {}
        for c in categories:
            key = c["fourType"] or "추가판단 필요"
            four_type_counts[key] = four_type_counts.get(key, 0) + 1
        if four_type_counts:
            st.bar_chart(pd.Series(four_type_counts, name="대분류 수"))
        else:
            st.caption("표시할 데이터가 없습니다.")

    with chart_col2:
        st.markdown('<div class="section-title">부서별 확정 진행률</div>', unsafe_allow_html=True)
        dept_pcts = {}
        for dept in data["departments"]:
            _, _, pct = dept_progress_pct(categories, dept, confirmations)
            dept_pcts[dept] = round(pct * 100, 1)
        if dept_pcts:
            st.bar_chart(pd.Series(dept_pcts, name="확정률(%)"))
        else:
            st.caption("표시할 데이터가 없습니다.")

    st.caption(
        f"생성 시각: {data['generated_at']} · 스캔 부서 {len(data['departments'])}개 · "
        "이 화면은 아래 「전체」/부서별 탭에서 확정 작업을 진행할 때마다 자동으로 갱신됩니다."
    )


def render_overview_tab():
    data = st.session_state.data
    confirmations = st.session_state.confirmations
    categories = data["categories"]

    render_llm_recheck_section(data)
    render_ai_phase1_section(data)

    ai_rec_count = sum(1 for c in categories if not c["needsReview"] and c.get("drivers"))
    local_rec_count = sum(1 for c in categories if not c["needsReview"] and not c.get("drivers"))

    if local_rec_count and not ai_rec_count:
        st.info(
            f"Phase 1 AI 추천 전 {local_rec_count}건에 **자동 추천(1~3순위)** 을 표시합니다. "
            "1순위 승인·직접 입력으로 확정할 수 있습니다."
        )
    elif local_rec_count:
        st.caption(
            f"AI 추천 {ai_rec_count}건 · 자동 추천 {local_rec_count}건 — Phase 1 완료 후 AI 추천이 우선 적용됩니다."
        )

    render_progress_and_summary(categories, confirmations)
    non_review_needed = len(categories) - sum(1 for c in categories if c["needsReview"])
    c5, c6 = st.columns(2)
    c5.metric("비용 성격 설명", f"{len(categories)} / {len(categories)}")
    c6.metric("동인 추천 제공", f"{ai_rec_count + local_rec_count} / {non_review_needed}")
    st.caption("모든 대분류에 1차 비용 설명 + (명확 대분류) 원가동인 1~3순위 추천 제공")

    save_col, _ = st.columns([1, 3])
    with save_col:
        if st.button("💾 전체 저장 (accounts_master.json + 최종 엑셀 갱신)", type="primary", use_container_width=True):
            with st.spinner("저장 중..."):
                result = save_all()
            st.success("저장 완료 — accounts_master.json과 부서별 최종 엑셀이 갱신되었습니다.")
            st.json(result["apply_result"])

    search_col, filter_col = st.columns([2, 2])
    with search_col:
        search_q = st.text_input("🔍 대분류명 검색", key="search_q")
    with filter_col:
        status_filter = st.radio("상태 필터", ["전체", "확정 완료", "미확정"], horizontal=True, key="status_filter")

    def visible(cat_item):
        if search_q:
            if search_q.lower() not in cat_item["category"].lower():
                return False
        conf = confirmations[f"category:{cat_item['category']}"]
        done = is_done_category(cat_item, conf)
        if status_filter == "확정 완료" and not done:
            return False
        if status_filter == "미확정" and done:
            return False
        return True

    common_cats = [c for c in categories if c["scope"] == "common" and not c["needsReview"] and visible(c)]
    specific_cats_all = [c for c in categories if c["scope"] == "specific" and not c["needsReview"]]
    specific_cats = [c for c in specific_cats_all if visible(c)]
    needs_review_cats = [c for c in categories if c["needsReview"] and visible(c)]

    st.subheader(f"1. 공통 대분류 ({len(common_cats)}건)")
    st.caption("2개 이상 부서에 세부계정이 걸친 대분류입니다. 한 번만 확정하면 그 아래 세부계정 전체(원칙 준용)에 자동 반영됩니다.")
    if not common_cats:
        st.caption("조건에 맞는 공통 대분류가 없습니다.")
    for cat_item in common_cats:
        render_category_card(cat_item)

    st.subheader(f"2. 특정 대분류 ({len(specific_cats)}건)")
    st.caption("한 부서에만 해당하는 대분류입니다. 소속 부서 탭에만 반영됩니다.")
    dept_options = ["전체"] + data["departments"]
    dept_pick = st.selectbox("부서 필터", dept_options, key="specific_dept_filter")
    filtered_specific = specific_cats if dept_pick == "전체" else [c for c in specific_cats if dept_pick in c["departments"]]
    if not filtered_specific:
        st.caption("조건에 맞는 특정 대분류가 없습니다.")
    for cat_item in filtered_specific:
        render_category_card(cat_item)

    st.subheader(f"3. ⚠ 추가판단 필요 대분류 ({len(needs_review_cats)}건)")
    st.caption(
        "AI가 4-type을 확정하지 못한 대분류입니다. 4-type만 저장하면 원가동인 추천 재실행 대상, "
        "원가동인까지 함께 입력하면 즉시 확정됩니다. 대분류 확정을 기다리지 않고 세부계정을 먼저 예외 지정할 수도 있습니다."
    )
    if not needs_review_cats:
        st.caption("추가판단이 필요한 대분류가 없습니다.")
    for cat_item in needs_review_cats:
        render_category_card(cat_item)


# ---------------------------------------------------------------------------
# Tab 2~N: 부서별 확인 화면 (읽기 전용)
# ---------------------------------------------------------------------------

def effective_display(cat_item: dict, sa: dict, confirmations: dict) -> dict:
    """세션 상태(미저장 초안 포함) 기준으로 이 계정코드의 현재 예상 확정값을 계산한다(부서 무관 —
    이 계정이 등장하는 모든 부서에 동일하게 적용). 저장 후 최종 판정은
    category_utils.resolve_account_effective()가 담당하며, 이건 화면 표시 전용이다."""
    sa_conf = confirmations[f"account:{sa['code']}"]
    if sa_conf["applyMode"] == "예외지정":
        return {
            "적용방식": "예외지정",
            "fourType": sa_conf["confirmedFourType"] or "미정",
            "driver": sa_conf["confirmedDriver"] or "",
            "confirmStatus": sa_conf["confirmStatus"],
        }
    cat_conf = confirmations[f"category:{cat_item['category']}"]
    driver = cat_conf["confirmedDriver"] or (cat_item["drivers"][0]["driver"] if cat_item["drivers"] else "")
    four_type = (cat_conf["humanFourType"] or cat_item["fourType"]) if cat_item["needsReview"] else cat_item["fourType"]
    return {
        "적용방식": "원칙준용",
        "fourType": four_type,
        "driver": driver,
        "confirmStatus": cat_conf["confirmStatus"],
    }


def render_dept_tab(dept: str):
    data = st.session_state.data
    confirmations = st.session_state.confirmations
    categories_for_dept = [c for c in data["categories"] if dept in c["departments"]]

    st.caption("이 화면은 확인 전용입니다. 확정/수정은 \"전체\" 탭에서만 합니다.")
    render_dept_progress(categories_for_dept, dept, confirmations)

    rows = []
    for cat_item in categories_for_dept:
        for sa in cat_item["subAccounts"]:
            if dept not in sa.get("departments", []):
                continue
            eff = effective_display(cat_item, sa, confirmations)
            nature_input = category_to_nature_input(cat_item, st.session_state.get("amount_by_dept", {}))
            nature_text, _ = resolve_display_nature(nature_input, cat_item.get("reason") or "")
            status = "확정 대기"
            if eff["confirmStatus"] in ("승인", "수정", "확정"):
                status = f"✓ 확정 완료 ({eff['적용방식']})"
            elif cat_item["needsReview"] and eff["적용방식"] == "원칙준용":
                status = "대분류 추가판단 필요"
            rows.append({
                "대분류": cat_item["category"], "계정코드": sa["code"], "계정명": sa["name"],
                "계정 설명": (sa.get("description") or {}).get("내용", ""),
                "비용 성격": nature_text,
                "적용방식": eff["적용방식"],
                "4-type": eff["fourType"], "확정 원가동인": eff["driver"] or "확정 대기", "상태": status,
            })

    if not rows:
        st.caption("이 부서에 해당하는 계정이 없습니다.")
    else:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# 엔트리포인트
# ---------------------------------------------------------------------------

def main():
    render_upload_sidebar()

    if not SEG_PATH.exists() or not MASTER_PATH.exists():
        st.markdown(
            """
            <div class="hero-wrap">
              <div class="hero-title">원가동인 확정 도구</div>
              <p class="hero-sub">왼쪽 사이드바에서 부서별 비용 파일을 업로드하고 분석을 시작하세요.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if (SAMPLE_DATA_DIR / "accounts_master.json").exists():
            st.markdown("#### 빠르게 둘러보기")
            st.caption(
                "API 키나 업로드 없이, 실제로 AI(Claude)가 끝까지 처리한 샘플 결과(가상 부서 "
                "데이터 20개 부서 · 대분류 32건)를 바로 볼 수 있습니다. 비용 소모나 대기 시간이 "
                "전혀 없습니다."
            )
            if st.button("📦 샘플 데이터 불러오기", type="primary"):
                with st.spinner("샘플 데이터 불러오는 중..."):
                    load_sample_data()
                st.rerun()
            st.divider()

        st.info(
            "직접 업로드해서 처음부터 돌려보고 싶다면, Phase 0(계정 스캔) + Phase 0.5(공통/특정 "
            "대분류 분리)부터 실행됩니다. 이후 이 화면에서 비용 설명·동인 추천·회계사 확정을 "
            "진행할 수 있습니다."
        )
        st.markdown(
            '<div class="empty-state-box">📄 <strong>샘플 파일 형식 예시</strong> — 아래 4개 컬럼(계정코드/계정명/'
            '부서명/금액)만 있으면 CSV든 엑셀이든 그대로 업로드할 수 있습니다. 부서명은 파일 하나에 여러 부서가 '
            '섞여 있어도(시트별/컬럼값별) 자동으로 인식됩니다.</div>',
            unsafe_allow_html=True,
        )
        st.dataframe(
            pd.DataFrame({
                "계정코드": ["C1001", "S4012", "C1008", "S4139"],
                "계정명": ["복리후생비-건강보험료", "회의비-확대영업회의", "전산비-H/W정비료", "지급임차료-사택"],
                "부서명": ["재무팀", "총무팀", "IT운영팀", "인사팀"],
                "금액": [12_500_000, 3_200_000, 18_800_000, 27_500_000],
            }),
            hide_index=True, use_container_width=True,
        )
        st.stop()

    ensure_state()
    render_download_sidebar()
    st.sidebar.caption(f"📍 현재 단계: {current_phase_label()}")

    data = st.session_state.data
    categories = data["categories"]
    subaccount_count = sum(len(c["subAccounts"]) for c in categories)
    render_app_header(len(data["departments"]), len(categories), subaccount_count)

    # 대시보드를 index 0에 둔다 — Streamlit은 st.rerun() 후 탭 선택을 유지하지 않고
    # 항상 첫 탭으로 돌아가므로, 배치 업로드/처리 완료 직후 자동으로 대시보드가
    # 보이는 효과를 별도 JS 없이 얻는다.
    tab_names = ["📊 요약 대시보드", "전체"] + data["departments"]
    tabs = st.tabs(tab_names)

    with tabs[0]:
        render_dashboard_tab()
    with tabs[1]:
        render_overview_tab()
    for i, dept in enumerate(data["departments"], start=2):
        with tabs[i]:
            render_dept_tab(dept)

    st.caption(
        f"생성 시각: {data['generated_at']} · 스캔 부서: {', '.join(data['departments'])} · "
        "account_segmentation.json + accounts_master.json 기준"
    )


if __name__ == "__main__":
    main()
