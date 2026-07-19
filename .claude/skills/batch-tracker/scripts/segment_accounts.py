"""Phase 0.5: 전체 부서 계정을 대분류 단위로 공통/특정으로 분리 확정한다.

규칙 기반 판별: 계정명에서 도출한 대분류(하이픈 앞부분, 없으면 전체)가 min_depts 이상
부서에 등장하면 공통, 1개 부서만이면 특정. 대분류 산하 세부계정명은 부서마다 달라도 무방하다.

**공통 대분류 중 일부는 규칙 기반 판정만으로 확정하지 않고 needs_llm_recheck로 보내
account-classifier의 재확인을 받는다.** 과거 버전은 "부서별 금액 편차"를 그대로
판단 기준으로 썼다가, 그 편차가 조직 규모 차이 때문일 뿐인 경우가 많아(예: 인원이
많은 부서가 원래 금액도 크다) 오탐(false positive)이 잦았던 문제가 있었다. 이번
재도입에서는 그 실패를 반복하지 않도록 두 가지 신호를 쓴다.
1. **세부계정 등장부서 분포 이질성**(구조적 신호, 금액과 무관) — 대분류 안에
   "여러 부서에 공통으로 등장하는 세부계정"과 "1개 부서에만 있는 세부계정"이 섞여
   있으면, 그 대분류를 하나의 원가동인으로 묶는 게 타당한지 의심할 구조적 근거가
   된다(account-classifier.md §5가 Phase 1에서 이미 점검하는 것과 같은 관점을
   Phase 0.5에서 먼저 규칙 기반으로 스크리닝한다).
2. **부서별 금액 쏠림(점유율/배율)** — 단순 절대금액 편차가 아니라, 이 대분류
   금액에서 한 부서가 차지하는 점유율(share)과 1위/2위 부서 배율(ratio)을 본다.
   조직 규모가 크게 다른 두 부서라도 "이 대분류 하나에만" 유독 쏠려 있다면(예:
   전사 인원 비율로는 설명 안 되는 특정 부서 집중) 원가 발생 구조가 다를 가능성을
   시사한다. 두 임계치(`--ratio`, `--share`, 기본 5.0 / 0.8) 중 하나라도 넘으면 플래그.

analyze()는 account_segmentation.json(구조적 스냅샷)을 생성하는 동시에, accounts_master.json에
계정별 "대분류"/"구분"과 대분류별 categories 스켈레톤을 병합(merge) 방식으로 반영한다 —
이미 존재하는 대분류의 four_type/추가판단필요여부/원가동인/회계사확정 등 상태 필드는 덮어쓰지 않는다.
이미 "카테고리분류상태"가 "분류완료"인 대분류(Phase 1이 이미 끝난 것)는 재확인 대상에서
제외한다 — Phase 0.5 재확인은 Phase 1 착수 전에만 의미가 있다.

Usage:
    # 1차 분석 (규칙 기반)
    python segment_accounts.py analyze <input_departments_dir> <accounts_master.json> \
        <output_account_segmentation.json> [--min-depts 2] [--ratio 5.0] [--share 0.8]

    # LLM 재확인 판정 반영 (account-classifier의 판정 결과를 병합해 최종 확정)
    python segment_accounts.py apply-llm <account_segmentation.json> <llm_verdicts.json> \
        <accounts_master.json> <output_account_segmentation.json>
"""
import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from category_utils import CATEGORY_ALIASES, derive_category  # noqa: E402

REQUIRED_COLUMNS = ["계정코드", "계정명", "금액", "부서명"]
KST = timezone(timedelta(hours=9))


def load_department_file(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path, dtype={"계정코드": str})
    else:
        df = pd.read_excel(path, dtype={"계정코드": str})

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"필수 컬럼 누락: {missing} (파일: {path.name})")

    df["계정코드"] = df["계정코드"].astype(str).str.strip()
    df["계정명"] = df["계정명"].astype(str).str.strip()
    df["부서명"] = df["부서명"].astype(str).str.strip()
    return df


def _sync_master(master: dict, category_depts: dict[str, set], category_sub_accounts: dict,
                  code_categories: dict[str, str], code_names: dict[str, str],
                  depts_scanned: set, min_depts: int, now: str) -> None:
    """accounts_master.json에 대분류/구분/categories 스켈레톤을 병합 반영한다."""
    accounts = master.setdefault("accounts", {})
    categories = master.setdefault("categories", {})

    for code, category in code_categories.items():
        acc = accounts.setdefault(code, {
            "계정명": code_names[code],
            "등장부서": [],
            "구분": "특정",
            "분류상태": "미착수",
            "four_type": None,
            "분류근거": None,
            "추가판단필요여부": False,
            "추천상태": "미착수",
            "원가동인": None,
            "부서별_비고": {},
            "처리완료": False,
            "마지막갱신": None,
        })
        acc["계정명"] = code_names[code]
        acc["대분류"] = category
        # 계정코드는 전사 공통 코드 체계라 여러 부서에 걸쳐 동일하게 등장한다 —
        # 세부계정 단위 적용방식/예외확정도 대분류처럼 부서 무관 단일 값으로 취급한다.
        acc["등장부서"] = sorted(category_sub_accounts[category][code]["등장부서"])
        # 대분류 전체의 등장부서 합집합이 아니라 이 세부계정 자신의 등장부서 수로 판단한다 —
        # 대분류는 공통이어도 그 안의 특정 세부계정은 1개 부서에만 등장할 수 있다(예: 전산비
        # 대분류는 공통이지만 그 아래 "전산비-네트웍운영용역"은 IT운영팀에만 존재).
        acc["구분"] = "공통" if len(category_sub_accounts[category][code]["등장부서"]) >= min_depts else "특정"
        # 구버전(계정코드+부서 단위) 스키마의 부서별 dict가 남아있으면 계정코드 단위 단일값으로
        # 정규화한다 — 이 프로젝트는 아직 실사용 확정 데이터가 없는 개발 단계라 안전하게 리셋한다.
        if not isinstance(acc.get("적용방식"), str):
            acc["적용방식"] = "원칙준용"
        existing_exc = acc.get("예외확정")
        is_old_shape = isinstance(existing_exc, dict) and existing_exc and all(
            isinstance(v, dict) for v in existing_exc.values()
        )
        if is_old_shape:
            acc["예외확정"] = {}
        acc.setdefault("적용방식", "원칙준용")
        acc.setdefault("예외확정", {})
        acc.setdefault("설명", None)
        acc["마지막갱신"] = now

    for category, depts in category_depts.items():
        sub_codes = sorted(category_sub_accounts[category].keys())
        구분 = "공통" if len(depts) >= min_depts else "특정"
        if category not in categories:
            categories[category] = {
                "구분": 구분,
                "등장부서": sorted(depts),
                "세부계정코드": sub_codes,
                "카테고리분류상태": "미착수",
                "four_type": None,
                "분류근거": None,
                "추가판단필요여부": False,
                "추천상태": "미착수",
                "원가동인": None,
                "회계사확정": None,
                "처리경로": None,
                "처리완료": False,
                "마지막갱신": now,
            }
        else:
            cat = categories[category]
            cat["구분"] = 구분
            cat["등장부서"] = sorted(depts)
            cat["세부계정코드"] = sub_codes
            cat["마지막갱신"] = now

    master["departments_scanned"] = sorted(depts_scanned)


def _amount_concentration(dept_amounts: dict[str, float]) -> tuple[float, float]:
    """부서별 금액에서 (1위 부서 점유율, 1위/2위 배율)을 계산한다. 절대금액이 아니라
    상대적 쏠림을 보므로 조직 규모 차이 자체로는 잘 트리거되지 않는다."""
    amounts = sorted(dept_amounts.values(), reverse=True)
    total = sum(amounts)
    if total <= 0 or len(amounts) < 2:
        return 0.0, 0.0
    share = amounts[0] / total
    ratio = amounts[0] / amounts[1] if amounts[1] > 0 else float("inf")
    return share, ratio


def _coverage_split(sub_accounts: list[dict], min_depts: int) -> bool:
    """대분류 안에 '등장부서가 min_depts 이상인 세부계정'과 '1개 부서뿐인 세부계정'이
    함께 있으면 True. 등장부서 수 차이 자체가 아니라, 원가 발생 구조가 이질적일 가능성의
    구조적 신호다(금액과 무관 — account-classifier.md §5의 판단 기준을 규칙 기반으로
    1차 스크리닝)."""
    counts = [len(sa["등장부서"]) for sa in sub_accounts]
    if len(counts) < 2:
        return False
    return max(counts) >= min_depts and min(counts) == 1


def analyze(input_dir: str, master_path: str, out_path: str, min_depts: int = 2,
            ratio_threshold: float | None = 5.0, share_threshold: float | None = 0.8,
            alias_map: dict[str, str] = CATEGORY_ALIASES):
    dept_dir = Path(input_dir)
    files = sorted([*dept_dir.glob("*.xlsx"), *dept_dir.glob("*.csv")])
    if not files:
        raise FileNotFoundError(f"{input_dir}에 부서 원본 파일이 없습니다.")

    with open(master_path, encoding="utf-8") as f:
        master = json.load(f)

    category_depts: dict[str, set[str]] = {}
    # 대분류 -> 계정코드 -> {계정코드, 계정명, 등장부서(set), 금액} — 세부계정은 계정코드
    # 단위로 집계한다(전사 공통 코드 체계라 계정코드가 여러 부서에 그대로 등장하므로,
    # (계정코드,부서) 조합으로 쪼개면 같은 계정이 부서 수만큼 중복 표시된다).
    category_sub_accounts: dict[str, dict[str, dict]] = {}
    # 대분류 -> 부서 -> 금액 합계. 위 category_sub_accounts는 계정코드 단위라 같은 코드가
    # 여러 부서에 걸치면 부서별 금액이 하나로 합쳐진다 — 쏠림(concentration) 판단에는
    # 부서별 합계가 따로 필요해 별도로 집계한다.
    category_dept_amounts: dict[str, dict[str, float]] = {}
    code_categories: dict[str, str] = {}
    code_names: dict[str, str] = {}
    depts_scanned: set[str] = set()

    for file in files:
        df = load_department_file(file)
        for _, row in df.iterrows():
            code, dept, name, amt = row["계정코드"], row["부서명"], row["계정명"], float(row["금액"])
            depts_scanned.add(dept)
            category = derive_category(name, alias_map)
            code_categories[code] = category
            code_names[code] = name

            category_depts.setdefault(category, set()).add(dept)
            subs = category_sub_accounts.setdefault(category, {})
            if code not in subs:
                subs[code] = {"계정코드": code, "계정명": name, "등장부서": set(), "금액": 0.0}
            subs[code]["등장부서"].add(dept)
            subs[code]["금액"] += amt

            dept_amounts = category_dept_amounts.setdefault(category, {})
            dept_amounts[dept] = dept_amounts.get(dept, 0.0) + amt

    common_categories = []
    department_specific_categories = []
    needs_llm_recheck = []
    existing_categories = master.get("categories", {})

    for category in sorted(category_depts.keys()):
        depts = sorted(category_depts[category])
        sub_accounts = [
            {**sa, "등장부서": sorted(sa["등장부서"])}
            for sa in sorted(category_sub_accounts[category].values(), key=lambda s: s["계정코드"])
        ]

        if len(depts) < min_depts:
            department_specific_categories.append({
                "category": category,
                "department": depts[0],
                "확정방식": "규칙기반",
                "비고": "1개 부서에만 등장",
                "sub_accounts": sub_accounts,
            })
            continue

        already_classified = existing_categories.get(category, {}).get("카테고리분류상태") == "분류완료"
        coverage_flag = _coverage_split(sub_accounts, min_depts)
        share, ratio = _amount_concentration(category_dept_amounts.get(category, {}))
        concentration_flag = (
            (share_threshold is not None and share > share_threshold)
            or (ratio_threshold is not None and ratio > ratio_threshold)
        )

        if (coverage_flag or concentration_flag) and not already_classified:
            reasons = []
            if coverage_flag:
                reasons.append("세부계정 중 여러 부서 공통형과 1개 부서 전용형이 섞여 있어 원가 발생 구조가 이질적일 가능성")
            if concentration_flag:
                reasons.append(
                    f"부서별 금액이 특정 부서에 쏠림(1위 부서 점유율 {share:.0%}, 1위/2위 배율 {ratio:.1f}배)"
                )
            needs_llm_recheck.append({
                "대분류": category,
                "등장부서": depts,
                "부서별_금액": {
                    d: round(a) for d, a in
                    sorted(category_dept_amounts.get(category, {}).items(), key=lambda kv: -kv[1])
                },
                "규칙기반_1차판정": "공통후보",
                "플래그_사유": " / ".join(reasons),
                "sub_accounts": sub_accounts,
            })
            continue  # 재확인이 끝나기 전까지는 common_categories에 넣지 않는다 (apply_llm에서 처리)

        common_categories.append({
            "category": category,
            "departments": depts,
            "부서범위": "전체" if len(depts) == len(depts_scanned) else "일부",
            "확정방식": "규칙기반",
            "비고": f"{len(depts)}개 부서 등장, 세부계정 {len(sub_accounts)}건",
            "sub_accounts": sub_accounts,
        })

    out = {
        "generated_at": datetime.now(KST).isoformat(),
        "departments_scanned": sorted(depts_scanned),
        "min_depts_for_common": min_depts,
        "divergence_ratio_threshold": ratio_threshold,
        "divergence_share_threshold": share_threshold,
        "divergence_check_enabled": ratio_threshold is not None or share_threshold is not None,
        "category_alias_map_used": alias_map,
        "common_categories": common_categories,
        "department_specific_categories": department_specific_categories,
        "needs_llm_recheck": needs_llm_recheck,
        "llm_verdicts": [],
    }
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    now = datetime.now(KST).isoformat()
    _sync_master(master, category_depts, category_sub_accounts, code_categories, code_names,
                 depts_scanned, min_depts, now)
    with open(master_path, "w", encoding="utf-8") as f:
        json.dump(master, f, ensure_ascii=False, indent=2)

    print(
        f"공통 대분류 {len(common_categories)}건 / 특정 대분류 {len(department_specific_categories)}건 / "
        f"LLM 재확인 필요 {len(needs_llm_recheck)}건 -> {out_path} (accounts_master.json 동기화 완료)"
    )
    if needs_llm_recheck:
        names = ", ".join(item["대분류"] for item in needs_llm_recheck)
        print(f"Phase 0.5 미완료 — account-classifier 재확인 필요: {names}")
    else:
        print("Phase 0.5 완료 조건 충족 (LLM 재확인 대상 없음)")


def apply_llm(segmentation_path: str, verdicts_path: str, master_path: str, out_path: str):
    with open(segmentation_path, encoding="utf-8") as f:
        seg = json.load(f)
    with open(verdicts_path, encoding="utf-8") as f:
        verdicts = json.load(f)
    with open(master_path, encoding="utf-8") as f:
        master = json.load(f)

    verdict_by_category = {v["대분류"]: v for v in verdicts}
    remaining_recheck = []

    for item in seg.get("needs_llm_recheck", []):
        category = item["대분류"]
        verdict = verdict_by_category.get(category)
        if verdict is None:
            remaining_recheck.append(item)
            continue

        seg.setdefault("llm_verdicts", []).append(verdict)

        if verdict["판정"] == "공통유지":
            seg.setdefault("common_categories", []).append({
                "category": category,
                "departments": item["등장부서"],
                "부서범위": "전체" if len(item["등장부서"]) == len(seg["departments_scanned"]) else "일부",
                "확정방식": "LLM재확인",
                "비고": f"규칙기반으로는 {item['플래그_사유']}였으나 account-classifier 재확인 결과 공통 유지: {verdict['사유']}",
                "sub_accounts": item.get("sub_accounts", []),
            })
        elif verdict["판정"] == "특정전환":
            for dept in item["등장부서"]:
                dept_sub_accounts = [
                    {**sa, "등장부서": [dept]}
                    for sa in item.get("sub_accounts", [])
                    if dept in sa.get("등장부서", [])
                ]
                seg.setdefault("department_specific_categories", []).append({
                    "category": category,
                    "department": dept,
                    "확정방식": "LLM재확인",
                    "비고": f"규칙기반으로는 공통 후보({item['플래그_사유']})였으나 account-classifier 재확인 결과 부서별 개별 판단으로 전환: {verdict['사유']}",
                    "sub_accounts": dept_sub_accounts,
                })
            if category in master.get("categories", {}):
                master["categories"][category]["구분"] = "특정"
        else:
            raise ValueError(f"알 수 없는 판정: {verdict['판정']} (대분류 {category})")

    seg["needs_llm_recheck"] = remaining_recheck

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(seg, f, ensure_ascii=False, indent=2)
    with open(master_path, "w", encoding="utf-8") as f:
        json.dump(master, f, ensure_ascii=False, indent=2)

    print(f"LLM 판정 {len(verdicts)}건 반영 -> {out_path} (accounts_master.json 동기화 완료)")
    if remaining_recheck:
        codes = ", ".join(r["대분류"] for r in remaining_recheck)
        print(f"여전히 미해소: {codes}")
    else:
        print("Phase 0.5 완료 조건 충족 (LLM 재확인 대상 모두 해소)")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_analyze = sub.add_parser("analyze")
    p_analyze.add_argument("input_dir")
    p_analyze.add_argument("master_path")
    p_analyze.add_argument("out_path")
    p_analyze.add_argument("--min-depts", type=int, default=2)
    p_analyze.add_argument("--ratio", type=float, default=5.0)
    p_analyze.add_argument("--share", type=float, default=0.8)

    p_apply = sub.add_parser("apply-llm")
    p_apply.add_argument("segmentation_path")
    p_apply.add_argument("verdicts_path")
    p_apply.add_argument("master_path")
    p_apply.add_argument("out_path")

    args = parser.parse_args()

    if args.cmd == "analyze":
        analyze(args.input_dir, args.master_path, args.out_path, args.min_depts, args.ratio, args.share)
    elif args.cmd == "apply-llm":
        apply_llm(args.segmentation_path, args.verdicts_path, args.master_path, args.out_path)


if __name__ == "__main__":
    main()
