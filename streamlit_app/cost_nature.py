"""계정별 1차 비용 성격 설명 (Phase 1 분류근거 없을 때 Streamlit UI용 fallback).

Vite 데모(cost-allocation-project)의 costAnalysis.js 규칙을 Python으로 이식했다.
"""
from __future__ import annotations

__all__ = [
    "analyze_cost_item_locally",
    "build_local_driver_recommendations",
    "resolve_display_nature",
    "suggest_local_driver",
]

import re
from dataclasses import dataclass
from typing import Callable


@dataclass
class NatureResult:
    nature: str
    drivers: list[str]
    rationale: str


def _format_amount(amount: float | int) -> str:
    return f"{int(round(amount)):,}원"


def _build_search_text(item: dict) -> str:
    # 부서명은 계정의 성격과 무관하므로 매칭 대상에서 제외한다.
    # (예: "IT개발팀"의 "IT"가 IT비용 규칙에 우연히 매칭되는 등 부서명 유사도로 오분류되는 것을 방지)
    parts = [
        item.get("major"),
        item.get("minor"),
        item.get("account"),
        item.get("memo"),
        item.get("accountCode"),
    ]
    return " ".join(str(p) for p in parts if p).lower()


def _build_context_nature(item: dict, body: str) -> str:
    meta = " · ".join(p for p in (item.get("major"), item.get("minor")) if p)
    meta_part = f" ({meta})" if meta else ""
    memo_part = f" 비고: {item['memo']}." if item.get("memo") else ""
    dept = item.get("dept") or "해당 부서"
    amount = item.get("amount") or 0
    amount_part = f" {dept}에서 {_format_amount(amount)} 규모로 집행되었습니다." if amount > 0 else f" {dept}에서 집행되는 비용입니다."
    return f"「{item['account']}」{meta_part} — {body}{amount_part}{memo_part}"


def _build_default_nature(item: dict) -> str:
    meta = [p for p in (item.get("major"), item.get("minor")) if p]
    meta_text = f"{' · '.join(meta)} 분류의 " if meta else ""
    return _build_context_nature(
        item,
        f"{meta_text}손해보험사 사업비·간접비 항목으로, 계정명·발생부서·집행 맥락을 종합해 원가동인을 검토해야 하는 비용입니다.",
    )


@dataclass
class _Rule:
    test: Callable[[str, dict], bool]
    nature: Callable[[dict], str]
    drivers: list[str]
    rationale: str


def _rx(pattern: str) -> Callable[[str, dict], bool]:
    compiled = re.compile(pattern, re.IGNORECASE)

    def _test(text: str, _item: dict) -> bool:
        return bool(compiled.search(text))

    return _test


ANALYSIS_RULES: list[_Rule] = [
    _Rule(
        _rx(r"명예퇴직|퇴직급여|퇴직\s*급여|퇴직금|퇴직\s*위로|early\s*retirement"),
        lambda item: _build_context_nature(
            item,
            "임직원 퇴직·명예퇴직 등 퇴직급여성 비용으로, 인사 정책·조직 재편에 따라 일회적·준고정적으로 발생합니다. "
            "특정 영업조직의 일상 업무활동비와 달리 전사 인력 구조와 연동되는 비용입니다.",
        ),
        ["전사 인원수 비율"],
        "퇴직급여성 비용은 개별 조직 활동량보다 전사 인원·조직 구조와 연관되어 인원수 기준 배분이 일반적입니다.",
    ),
    _Rule(
        _rx(r"임원.*급여|임원.*보수|임원.*퇴직|등기임원"),
        lambda item: _build_context_nature(
            item,
            "등기임원·임원 인건비로, 전사 경영·지배 기능에 해당하며 개별 사업부 성과와 직접 연결하기 어렵습니다. "
            "특정 조직에 전액 귀속 시 평가왜곡·민감정보 이슈가 있습니다.",
        ),
        ["전사 고정비 비율"],
        "임원 인건비는 전사 공통비 성격이 강해 고정비 비율 배분이 적합합니다.",
    ),
    _Rule(
        lambda t, _item: bool(re.search(r"(급여|상여|성과급|연봉|인건비|급여성)", t, re.I))
        and not re.search(r"퇴직|임원", t, re.I),
        lambda item: _build_context_nature(
            item,
            "해당 부서 소속 직원에 지급되는 인건비(급여·상여 등)로, 부서의 인력 운용·업무 수행과 직접 연결되는 직접비입니다. "
            "발생부서가 집행을 통제·관리합니다.",
        ),
        ["조직별 실질 집행액"],
        "인건비는 발생부서가 통제하는 비용으로 실질 집행액 귀속이 원칙입니다.",
    ),
    _Rule(
        _rx(r"4대\s*보험|국민연금|건강보험|고용보험|산재보험|장기요양"),
        lambda item: _build_context_nature(
            item,
            "법정 4대보험·장기요양 등 전 직원 대상 의무 부담금으로, 개별 조직장이 통제하기 어렵고 "
            "인원수에 비례해 발생하는 복리후생·법정비용입니다.",
        ),
        ["전사 인원수 비율"],
        "4대보험료는 인원수에 연동되므로 전사 인원수 비율 배분이 적합합니다.",
    ),
    _Rule(
        _rx(r"경조|복리후생|어린이집|자녀\s*학자|건강검진|단체\s*보험|동호회|선물|명절|시상"),
        lambda item: _build_context_nature(
            item,
            "전 직원 또는 일정 범위 임직원 대상 복리후생·경조·후생성 비용으로, "
            "특정 부서 성과보다 전사 인원 규모와 연관됩니다.",
        ),
        ["전사 인원수 비율"],
        "복리후생성 비용은 인원수 비례 배분이 일반적입니다.",
    ),
    _Rule(
        _rx(r"광고|홍보|선전|프로모션|스폰서|판촉|마케팅"),
        lambda item: _build_context_nature(
            item,
            "브랜드·상품 홍보·판촉을 위한 광고선전비로, 특정 부서가 주도적으로 기획·집행하며 "
            "예산 절감의 책임과 권한도 해당 부서에 있는 비용입니다.",
        ),
        ["전액 직접 귀속"],
        "광고선전비는 집행을 주도한 부서가 예산 편성·절감 권한을 가지므로, "
        "동인으로 나누지 않고 전액 해당 부서에 직접 귀속하는 것이 적합합니다.",
    ),
    _Rule(
        _rx(r"행사|이벤트|시상|리셉션|접대|회의비|식대|다과"),
        lambda item: _build_context_nature(
            item,
            "대내·대외 행사·회의·접대성 지출로, 회의비처럼 특정 부서가 주도하여 "
            "집행·추진하는 성격의 비용입니다.",
        ),
        ["전액 직접 귀속"],
        "회의비처럼 일부 부서가 주도하여 집행·추진하는 경우, 집행하는 부서가 해당 지출의 "
        "절감 책임과 권한을 가지므로 동인으로 나누지 않고 전액 귀속합니다.",
    ),
    _Rule(
        _rx(r"출장|여비|교통비|택시|항공|숙박|통행료"),
        lambda item: _build_context_nature(
            item,
            "업무 출장·교통에 소요되는 비용으로, 출장 빈도·업무 범위에 따라 발생부서가 직접 집행·통제합니다.",
        ),
        ["출장횟수", "조직별 실질 집행액"],
        "출장비는 출장 건수 또는 발생부서 집행액 기준 귀속이 일반적입니다.",
    ),
    _Rule(
        _rx(
            r"sw|소프트웨어|라이선스|클라우드|saas|aws|azure|서버|hosting|idc|"
            r"네트워크|nw|통신|회선|전산|it|정보\s*시스템|시스템\s*유지"
        ),
        lambda item: _build_context_nature(
            item,
            "전산·IT 인프라·소프트웨어·통신 등 디지털 기반 비용으로, 사용자 수·트랜잭션·서버 사용량 등 "
            "활동 기준 배분이 가능합니다. 전사 공용일 경우 간접비 성격도 있습니다.",
        ),
        ["시스템 사용량(트랜잭션)", "라이선스 사용자수"],
        "IT·시스템 비용은 사용량·사용자수 기반 배분이 타당합니다.",
    ),
    _Rule(
        _rx(r"감사|audit|컴플라이언스|준법|aml|내부\s*통제"),
        lambda item: _build_context_nature(
            item,
            "내부·외부 감사·준법·컴플라이언스 관련 비용으로, 전사 거버넌스 기능에 해당하며 "
            "감사 대상 자산·조직 규모와 연관됩니다.",
        ),
        ["감사대상 자산규모", "전사 고정비 비율"],
        "감사·준법 비용은 자산규모 또는 전사 고정비 비율 배분이 검토됩니다.",
    ),
    _Rule(
        _rx(r"법률|법무|소송|자문|변호"),
        lambda item: _build_context_nature(
            item,
            "법률자문·소송·계약 검토 등 법무 관련 비용으로, 처리 건수·사건 성격에 따라 "
            "발생부서 또는 법무 집행부서에 연결됩니다.",
        ),
        ["법률자문 처리건수", "조직별 실질 집행액"],
        "법률자문비는 처리건수 또는 집행부서 귀속이 일반적입니다.",
    ),
    _Rule(
        _rx(r"교육|훈련|연수|세미나|워크숍|자격|e-learning|lms|예비군"),
        lambda item: _build_context_nature(
            item,
            "임직원 역량·Compliance 교육·훈련 비용으로, 교육 이수 인원·대상 조직과 연동됩니다.",
        ),
        ["교육이수인원"],
        "교육훈련비는 교육 이수 인원 기준 배분이 타당합니다.",
    ),
    _Rule(
        _rx(r"임차|임대|관리비|전기|수도|가스|냉난방|사옥|부동산|공실|수도광열|난방"),
        lambda item: _build_context_nature(
            item,
            "사옥·지점 등 시설 임차·유지·관리·수도광열 비용으로, 사용 면적·점유 부서와 연관됩니다.",
        ),
        ["사용면적(㎡)"],
        "시설·임차·유틸리티 비용은 사용면적 기준 배분이 일반적입니다.",
    ),
    _Rule(
        _rx(r"위탁|용역|아웃소|bpo|외주|대행|수수료"),
        lambda item: _build_context_nature(
            item,
            "외부 위탁·용역·수수료성 비용으로, 위탁 계약 범위·처리량·발생부서의 업무 위탁과 직접 연결됩니다.",
        ),
        ["위탁계약 정산기준", "조직별 실질 집행액"],
        "위탁·용역비는 계약 정산기준 또는 발생부서 집행액 귀속이 적합합니다.",
    ),
    _Rule(
        _rx(r"콜\s*센터|고객\s*센터|상담|cs|민원|voc"),
        lambda item: _build_context_nature(
            item,
            "고객·민원·콜센터 상담 관련 비용으로, 상담 건수·채널 처리량과 연동됩니다.",
        ),
        ["상담건수", "처리건수"],
        "상담·CS 비용은 상담·처리 건수 기준 배분이 타당합니다.",
    ),
    _Rule(
        _rx(r"보험\s*금\s*지급|손해\s*사|계약\s*관리|언더|uw|인수|심사|보상"),
        lambda item: _build_context_nature(
            item,
            "보험 계약·인수·보상·언더라이팅 등 핵심 보험영업·계약 관리 업무와 연관된 비용입니다.",
        ),
        ["처리건수", "조직별 실질 집행액"],
        "보험 핵심 업무 비용은 처리건수 또는 발생부서 집행액 귀속이 검토됩니다.",
    ),
    _Rule(
        _rx(r"채널|모집|ga|fa|대리점|설계\s*사|영업\s*지원"),
        lambda item: _build_context_nature(
            item,
            "영업 채널·모집·설계사 지원 등 판매 채널 관련 비용으로, 채널 활동·모집 실적과 연동됩니다.",
        ),
        ["조직별 실질 집행액"],
        "영업 채널 비용은 해당 채널·부서 집행액 귀속이 일반적입니다.",
    ),
    _Rule(
        _rx(r"법정\s*적립|법정\s*부담|세금|세\s*과|공과|지방\s*세|재산\s*세|인지|등록|상공회의소"),
        lambda item: _build_context_nature(
            item,
            "법정부담금·세금·공과금 등 법령에 따른 의무 비용으로, "
            "개별 조직 성과와 무관한 전사·시설 기준 부과 비용입니다.",
        ),
        ["전사 고정비 비율"],
        "법정부담·세금은 전사 고정비 비율 배분이 일반적입니다.",
    ),
    _Rule(
        _rx(r"감가|상각|무형\s*자산|depreciation|기계장치"),
        lambda item: _build_context_nature(
            item,
            "유·무형 자산·기계장치 등 감가상각비로, 자산을 사용·수혜하는 조직·부서에 배분하는 것이 원칙입니다.",
        ),
        ["사용면적(㎡)", "전사 고정비 비율"],
        "감가상각비는 사용·수혜 기준(면적·고정비 비율) 배분이 검토됩니다.",
    ),
    _Rule(
        _rx(r"연구|개발|r&d|신\s*product|상품\s*개발"),
        lambda item: _build_context_nature(
            item,
            "신상품·서비스 연구개발 비용으로, 개발 프로젝트·상품 라인·기획 부서와 연동되는 투자·혁신 성격의 비용입니다.",
        ),
        ["조직별 실질 집행액"],
        "R&D 비용은 프로젝트·기획 부서 집행액 귀속이 일반적입니다.",
    ),
    _Rule(
        _rx(r"인쇄|문구|사무|소모|비품|택배|우편|용지"),
        lambda item: _build_context_nature(
            item,
            "사무·소모품·인쇄·우편 등 일반 관리·운영 지원 비용으로, 발생부서의 일상 업무 소요와 연결됩니다.",
        ),
        ["조직별 실질 집행액"],
        "사무·소모성 경비는 발생부서 집행액 귀속이 원칙입니다.",
    ),
    _Rule(
        lambda t, item: bool(re.search(r"전사|공통|본사|shared|센터", t, re.I))
        or ("전사" in str(item.get("dept") or "")),
        lambda item: _build_context_nature(
            item,
            "전사 공통·본사·센터 집행 비용으로, 특정 사업부 단독 귀속이 어렵고 전사 간접비로 배분 검토가 필요합니다.",
        ),
        ["전사 고정비 비율"],
        "전사 공통비는 고정비 비율 또는 인원수 기준 간접 배분이 적합합니다.",
    ),
]


def analyze_cost_item_locally(item: dict) -> NatureResult:
    text = _build_search_text(item)
    for rule in ANALYSIS_RULES:
        if rule.test(text, item):
            return NatureResult(
                nature=rule.nature(item),
                drivers=rule.drivers,
                rationale=rule.rationale,
            )
    return NatureResult(
        nature=_build_default_nature(item),
        drivers=["조직별 실질 집행액"],
        rationale=(
            "계정명만으로 세부 동인을 특정하기 어려워, 우선 발생부서 실질 집행액 귀속을 제안합니다. "
            "회계사·현업 협의를 통해 조정이 필요할 수 있습니다."
        ),
    )


def resolve_display_nature(item: dict, phase1_reason: str = "") -> tuple[str, str]:
    """화면에 보여줄 비용 설명과 출처 라벨을 반환한다.

    Phase 1 분류근거가 있으면 우선 표시하고, 없으면 로컬 자동 설명을 쓴다.
    """
    reason = (phase1_reason or "").strip()
    if reason:
        return reason, "AI 분류근거"
    local = analyze_cost_item_locally(item)
    return local.nature, "자동 설명"


FALLBACK_DRIVER_CANDIDATES = [
    "조직별 실질 집행액",
    "전사 인원수 비율",
    "전사 고정비 비율",
    "전액 직접 귀속",
    "처리건수",
    "출장횟수",
]


def build_local_driver_recommendations(item: dict) -> list[dict]:
    """Phase 1 미실행 시 데모·실사용 로컬 원가동인 1~3순위 추천."""
    local = analyze_cost_item_locally(item)
    drivers: list[dict] = []
    seen: set[str] = set()

    for idx, driver in enumerate(local.drivers):
        if driver in seen:
            continue
        drivers.append({
            "rank": len(drivers) + 1,
            "driver": driver,
            "reason": local.rationale if idx == 0 else "자동 규칙 기반 보조 후보 동인입니다.",
        })
        seen.add(driver)

    for candidate in FALLBACK_DRIVER_CANDIDATES:
        if len(drivers) >= 3:
            break
        if candidate in seen:
            continue
        drivers.append({
            "rank": len(drivers) + 1,
            "driver": candidate,
            "reason": "유사 계정군에서 흔히 검토되는 보조 동인입니다.",
        })
        seen.add(candidate)

    return drivers


def suggest_local_driver(item: dict) -> str:
    """추가판단 필요 계정 등에서 참고용 1순위 동인 텍스트."""
    recs = build_local_driver_recommendations(item)
    return recs[0]["driver"] if recs else ""
