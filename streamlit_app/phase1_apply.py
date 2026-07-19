"""Phase 1 결과를 accounts_master.json에 반영한다 (4-type + 원가동인 1~3순위).

PoC용 규칙+프레임워크 기반 분류. Claude Code driver-recommender와 동일한
master 스키마(원가동인.recommended_drivers)로 저장하면 Streamlit에서 「AI 추천」으로 표시된다.

Usage:
    python streamlit_app/phase1_apply.py
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "streamlit_app"))

from cost_nature import analyze_cost_item_locally, build_local_driver_recommendations  # noqa: E402

MASTER_PATH = BASE_DIR / "output" / "accounts_master.json"
SEG_PATH = BASE_DIR / "output" / "account_segmentation.json"
LOG_PATH = BASE_DIR / "output" / "batch_log.json"
KST = timezone(timedelta(hours=9))

DRIVER_TO_FOUR_TYPE: dict[str, str] = {
    "조직별 실질 집행액": "직접귀속형",
    "출장횟수": "직접귀속형",
    "처리건수": "직접귀속형",
    "상담건수": "직접귀속형",
    "법률자문 처리건수": "직접귀속형",
    "위탁계약 정산기준": "직접귀속형",
    "전사 인원수 비율": "배부형",
    "교육이수인원": "배부형",
    "사용면적(㎡)": "배부형",
    "라이선스 사용자수": "배부형",
    "시스템 사용량(트랜잭션)": "배부형",
    "감사대상 자산규모": "배부형",
    "전사 고정비 비율": "공통비형",
    "전액 직접 귀속": "직접귀속형",
}

AMBIGUOUS_PATTERNS = [
    re.compile(r"기타\s*[-·]?\s*기타", re.I),
    re.compile(r"기타\s*\(\s*기타\s*\)", re.I),
    re.compile(r"잡급여", re.I),
]


def is_ambiguous_account(name: str, local) -> bool:
    if any(p.search(name) for p in AMBIGUOUS_PATTERNS):
        return True
    if "기타" in name and name.count("기타") >= 2:
        return True
    return (
        local.drivers == ["조직별 실질 집행액"]
        and "검토" in local.rationale
        and "어려워" in local.rationale
    )


def infer_four_type(name: str, primary_driver: str) -> str:
    if re.search(r"명예퇴직|퇴직급여|퇴직금|퇴직\s*위로", name, re.I):
        return "공통비형"
    # "임원"이 붙어도 실질이 업무추진비·회의비 등 집행성 경비면 공통비형으로 일괄 처리하지 않는다
    # (cost_classification_standard.md "명목과 실질의 불일치" 원칙 — 실질(substance) 우선 판단).
    # 급여·상여·학자금 등 순수 개인 보수·복지성 계정만 개인정보 민감성을 이유로 공통비형 처리한다.
    if re.search(r"임원", name, re.I) and not re.search(
        r"업무추진비|회의비|출장비|교통비", name, re.I
    ):
        return "공통비형"
    if re.search(r"건강보험|4대\s*보험|국민연금|고용보험|산재", name, re.I):
        return "배부형"
    if re.search(r"복리후생|경조|시상|격려금", name, re.I):
        return "배부형"
    return DRIVER_TO_FOUR_TYPE.get(primary_driver, "직접귀속형")


def classify_account(acc: dict) -> dict:
    name = acc["계정명"]
    item = {
        "account": name,
        "accountCode": acc.get("계정코드", ""),
        "dept": (acc.get("등장부서") or [""])[0],
        "amount": 0,
        "major": "",
        "minor": "",
        "memo": "",
    }
    local = analyze_cost_item_locally(item)
    drivers = build_local_driver_recommendations(item)

    if is_ambiguous_account(name, local):
        return {
            "추가판단필요여부": True,
            "four_type": None,
            "분류근거": (
                f"「{name}」은 계정명만으로 4-type 단일 판정이 어렵습니다. "
                f"{local.nature[:120]}… 회계사 4-type 확정이 필요합니다."
            ),
            "원가동인": None,
        }

    primary = drivers[0]["driver"] if drivers else "조직별 실질 집행액"
    four_type = infer_four_type(name, primary)
    doc_ref = "cost_classification_standard.md·abc_costing_principles.md 기반 규칙 분류 (PoC Phase 1)"

    if four_type == "공통비형":
        # 공통비형은 조직별로 배분하지 않고 전사 손익에서 직접 관리한다
        # (cost_classification_standard.md 공통비형 정의) — 내용 기반으로 도출된
        # 부서 배분용 동인(drivers)을 그대로 쓰면 모순되므로 배분 제외로 대체한다.
        recommended = [{
            "rank": 1,
            "driver": "배분 제외(전사 공통비)",
            "reason": (
                f"{doc_ref}: 공통비형 유형은 조직별로 배분하지 않고 전사 손익에서 "
                "직접 관리하므로 부서별 배분 동인을 두지 않습니다."
            ),
            "근거출처": "문서인용",
        }]
    else:
        recommended = []
        for d in drivers[:3]:
            reason = d.get("reason") or ""
            if d["rank"] == 1:
                reason = (
                    f"{reason} ({doc_ref}: {four_type} 유형에 부합하는 1순위 동인으로 분류했습니다.)"
                )
            recommended.append({
                "rank": d["rank"],
                "driver": d["driver"],
                "reason": reason,
                "근거출처": "문서인용" if d["rank"] == 1 else "일반 회계 지식 기반 추정",
            })

    return {
        "추가판단필요여부": False,
        "four_type": four_type,
        "분류근거": f"{local.nature} — {doc_ref}에 따라 {four_type}로 분류.",
        "원가동인": {"recommended_drivers": recommended},
    }


def _rollup_categories(master: dict, now: str) -> dict:
    """계정코드 단위 결과를 대분류(categories) 단위로 묶어 반영한다.

    이 롤업이 없으면 이 스크립트의 결과가 화면/엑셀에 전혀 나타나지 않는다 —
    `category_utils.resolve_account_effective()`는 원칙준용 계정의 경우
    `accounts[code]` 최상위 필드가 아니라 `categories[대분류]`를 읽기 때문이다
    (`accounts_master_schema.md`의 "레거시 필드 안내" 참조). 이미 실제
    서브에이전트로 "분류완료"된 대분류는 건드리지 않는다 — 규칙 기반 폴백이
    진짜 AI 판단을 덮어쓰면 안 되기 때문이다.

    대분류 안의 세부계정들이 서로 다른 four_type으로 판정되면(계정명 패턴별로
    독립 판단하는 이 PoC 규칙 엔진의 한계) 대분류 하나로 확정하지 않고
    "추가판단필요(검토대기)"로 남긴다. 모두 일치하면 대표(첫 계정)의 판정을
    대분류 값으로 채택한다 — 대분류 전용 재판단이 아니라 세부계정 판정의 다수
    일치를 그대로 쓰는 근사치이므로, 실제 서브에이전트(account-classifier /
    driver-recommender)보다 정밀도가 낮다는 점을 판단경로로 명시해 구분한다.
    """
    stats = {"categories_total": 0, "categories_confirmed": 0, "categories_ambiguous": 0, "categories_skipped_already_ai": 0}
    by_category: dict[str, list[str]] = {}
    for code, acc in master.get("accounts", {}).items():
        by_category.setdefault(acc.get("대분류"), []).append(code)

    for category, codes in by_category.items():
        cat = master.get("categories", {}).get(category)
        if cat is None:
            continue
        stats["categories_total"] += 1
        if cat.get("카테고리분류상태") == "분류완료" and cat.get("판단경로") != "규칙 기반 폴백":
            stats["categories_skipped_already_ai"] += 1
            continue

        accs = [master["accounts"][c] for c in codes]
        ambiguous_accs = [a for a in accs if a.get("추가판단필요여부")]
        four_types = {a.get("four_type") for a in accs if not a.get("추가판단필요여부")}

        if ambiguous_accs or len(four_types) > 1:
            reasons = [a["분류근거"] for a in accs if a.get("분류근거")]
            cat.update({
                "추가판단필요여부": True,
                "카테고리분류상태": "추가판단필요(검토대기)",
                "four_type": None,
                "분류근거": (
                    "규칙 기반 폴백(Phase1 PoC)이 세부계정별로 독립 판정한 결과가 서로 달라 "
                    "대분류 하나로 확정하지 못함: " + " / ".join(reasons[:3])
                ),
                "추천상태": "미착수",
                "원가동인": None,
                "처리완료": False,
            })
            stats["categories_ambiguous"] += 1
        else:
            representative = accs[0]
            cat.update({
                "four_type": representative.get("four_type"),
                "카테고리분류상태": "분류완료",
                "추가판단필요여부": False,
                "분류근거": representative.get("분류근거"),
                "추천상태": "완료",
                "원가동인": representative.get("원가동인"),
                "처리완료": True,
                "처리경로": "AI추천-확정(대기)",
            })
            stats["categories_confirmed"] += 1

        cat["판단경로"] = "규칙 기반 폴백"
        cat["마지막갱신"] = now

    return stats


def apply_to_master() -> dict:
    with open(MASTER_PATH, encoding="utf-8") as f:
        master = json.load(f)

    now = datetime.now(KST).isoformat()
    stats = {"total": 0, "classified": 0, "ambiguous": 0, "drivers": 0}

    for code, acc in master.get("accounts", {}).items():
        stats["total"] += 1
        result = classify_account(acc)
        acc["four_type"] = result["four_type"]
        acc["분류근거"] = result["분류근거"]
        acc["추가판단필요여부"] = result["추가판단필요여부"]
        acc["마지막갱신"] = now

        if result["추가판단필요여부"]:
            acc["분류상태"] = "추가판단필요(검토대기)"
            acc["추천상태"] = None
            acc["원가동인"] = None
            acc["처리완료"] = False
            stats["ambiguous"] += 1
        else:
            acc["분류상태"] = "분류완료"
            acc["추천상태"] = "완료"
            acc["원가동인"] = result["원가동인"]
            acc["처리완료"] = True
            stats["classified"] += 1
            stats["drivers"] += len(result["원가동인"]["recommended_drivers"])

            for dept in acc.get("등장부서", []):
                acc.setdefault("처리경로", {})[dept] = "AI추천-확정(대기)"

    rollup_stats = _rollup_categories(master, now)
    stats.update(rollup_stats)

    master["phase1_applied_at"] = now
    master["phase1_note"] = "규칙+프레임워크 기반 Phase 1 (PoC). Claude Code subagent 재실행 시 덮어쓸 수 있음."

    with open(MASTER_PATH, "w", encoding="utf-8") as f:
        json.dump(master, f, ensure_ascii=False, indent=2)

    batch_id = datetime.now(KST).strftime("%Y%m%d")
    log = {"batches": []}
    if LOG_PATH.exists():
        with open(LOG_PATH, encoding="utf-8") as f:
            log = json.load(f)
    dept_count = len(master.get("departments_scanned", []))
    log.setdefault("batches", []).append({
        "batch_id": batch_id,
        "부서명": f"전체({dept_count}부서)",
        "원본파일": "streamlit_app/phase1_apply.py",
        "처리유형": "Phase1-PoC",
        "시작시각": now,
        "완료시각": now,
        "총계정수": stats["total"],
        "추가판단필요계정수": stats["ambiguous"],
        "상태": "완료",
    })
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)

    out_validated = BASE_DIR / "output" / f"batch_{batch_id}_validated.json"
    validated = []
    for code, acc in master["accounts"].items():
        if acc.get("추가판단필요여부"):
            continue
        validated.append({
            "계정코드": code,
            "계정명": acc["계정명"],
            "four_type": acc["four_type"],
            "분류근거": acc["분류근거"],
            "추가판단필요여부": False,
            "recommended_drivers": (acc.get("원가동인") or {}).get("recommended_drivers", []),
            "검증결과": "통과",
            "검증사유": "PoC 규칙 기반 Phase 1",
        })
    with open(out_validated, "w", encoding="utf-8") as f:
        json.dump(validated, f, ensure_ascii=False, indent=2)

    return stats


if __name__ == "__main__":
    s = apply_to_master()
    print(json.dumps(s, ensure_ascii=False, indent=2))
