"""배치(대분류) 식별 및 완료 처리를 담당한다.

Usage:
    # ② 배치 식별: 신규/기존 판별
    python track_batch.py identify <accounts_master.json> <부서명>

    # ⑦ 배치 완료: batch_log.json append + accounts_master 대분류 상태 갱신
    python track_batch.py complete <accounts_master.json> <batch_log.json> \
        <batch_id> <배치명> <validated_json>

    # 회계사 확정 반영 (경로 A — 엑셀 재업로드): "추가판단 필요" 대분류에 대해 회계사가
    # batch_{id}-{부서}_final.xlsx(editable 모드)의 "4-type 분류(사람 확정)"/"확정 여부"/
    # "확정 원가동인"을 채워 재업로드한 파일을 반영한다. accounts_master.json 기준으로
    # 아직 "추가판단필요(검토대기)" 상태인 대분류만 대상으로 삼는다 — 이미 해소됐거나
    # 여전히 미기입인 대분류는 건드리지 않는다(전체 재처리 금지). 명확 대분류(추가판단
    # 필요가 아닌)의 확정은 이 경로의 대상이 아니다 — Streamlit 확정 앱(경로 B)에서 처리한다.
    python track_batch.py confirm <accounts_master.json> <accountant_edited_final.xlsx>

    # 회계사 확정 반영 (Streamlit 확정 앱 경로): streamlit_app/app.py의 "전체" 탭에서
    # "전체 저장" 시 생성되는 confirmed_results.json을 반영한다. 대분류 확정은 부서 무관
    # 단일 결정이며(등장부서 전체에 자동 적용), 세부계정을 "예외 지정"한 경우에만 그 계정
    # 개별 확정값이 대분류 원칙을 대체한다.
    python track_batch.py apply-browser-confirmations <accounts_master.json> <confirmed_results.json>
"""
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from category_utils import flag_suspect_citations, resolve_account_effective  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "excel-io" / "scripts"))
import read_confirmations  # noqa: E402

KST = timezone(timedelta(hours=9))


def load(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"batches": []} if "log" in p.name else {"accounts": {}, "categories": {}, "departments_scanned": []}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save(path: str, data: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def identify(master_path: str, dept: str):
    master = load(master_path)
    known = dept in master.get("departments_scanned", [])
    dept_accounts = [
        code for code, acc in master.get("accounts", {}).items()
        if dept in acc.get("등장부서", [])
    ]
    processed = [
        code for code in dept_accounts
        if resolve_account_effective(master, code).get("처리완료")
    ]
    print(json.dumps({
        "부서명": dept,
        "인벤토리에_존재": known,
        "해당부서_계정수": len(dept_accounts),
        "이미처리완료": len(processed),
        "미처리": len(dept_accounts) - len(processed),
        "유형": "갱신" if 0 < len(processed) < len(dept_accounts) else ("신규" if len(processed) == 0 else "완료됨"),
    }, ensure_ascii=False, indent=2))


def _check_citations(record: dict) -> list[str]:
    """recommended_drivers/세부계정_설명 중 '문서인용'으로 표시된 항목의 인용 출처를 기계적으로
    한 번 더 점검한다. result-validator가 이미 검증하지만, 병합 단계의 안전망이다."""
    suspects = []
    for d in record.get("recommended_drivers", []) or []:
        if d.get("근거출처") == "문서인용":
            bad = flag_suspect_citations(d.get("reason", ""))
            if bad:
                suspects.append(f"rank{d.get('rank')} 근거에 화이트리스트 밖 문서 언급: {bad}")
    for desc in record.get("세부계정_설명", []) or []:
        if desc.get("근거출처") == "문서인용":
            bad = flag_suspect_citations(desc.get("설명", ""))
            if bad:
                suspects.append(f"세부계정 {desc.get('계정코드')} 설명에 화이트리스트 밖 문서 언급: {bad}")
    return suspects


def complete(master_path: str, log_path: str, batch_id: str, batch_label: str, validated_json: str):
    master = load(master_path)
    log = load(log_path)

    with open(validated_json, encoding="utf-8") as f:
        validated = json.load(f)

    now = datetime.now(KST).isoformat()
    needs_review_count = 0
    citation_warnings = {}

    for record in validated:
        category = record["대분류"]
        cat = master.get("categories", {}).get(category)
        if cat is None:
            continue
        cat["four_type"] = record.get("four_type")
        cat["분류근거"] = record.get("분류근거")
        cat["추가판단필요여부"] = record.get("추가판단필요여부", False)
        if cat["추가판단필요여부"]:
            needs_review_count += 1
            cat["카테고리분류상태"] = "추가판단필요(검토대기)"
            cat["추천상태"] = "미착수"
            cat["원가동인"] = None
            cat["처리완료"] = False
        else:
            cat["카테고리분류상태"] = "분류완료"
            cat["추천상태"] = "완료"
            if "원가동인" in record and isinstance(record.get("원가동인"), dict):
                cat["원가동인"] = record["원가동인"]
            else:
                cat["원가동인"] = {"recommended_drivers": record.get("recommended_drivers", [])}
            cat["처리완료"] = True
            cat["처리경로"] = "AI추천-확정(대기)"
        cat["판단경로"] = "AI 판단"
        cat["마지막갱신"] = now

        suspects = _check_citations(record)
        if suspects:
            cat["인용출처_경고"] = suspects
            citation_warnings[category] = suspects
        else:
            cat.pop("인용출처_경고", None)

        for desc_item in record.get("세부계정_설명", []):
            code = desc_item.get("계정코드")
            acc = master.get("accounts", {}).get(code)
            if acc is None:
                continue
            acc["설명"] = {
                "내용": desc_item.get("설명"),
                "근거출처": desc_item.get("근거출처"),
            }
            acc["마지막갱신"] = now

    save(master_path, master)

    log.setdefault("batches", []).append({
        "batch_id": batch_id,
        "배치명": batch_label,
        "원본파일": validated_json,
        "처리유형": "신규",
        "시작시각": now,
        "완료시각": now,
        "총대분류수": len(validated),
        "추가판단필요대분류수": needs_review_count,
        "상태": "완료",
    })
    save(log_path, log)

    print(f"배치 {batch_id} 완료 기록: {len(validated)}건 (추가판단 필요 {needs_review_count}건)")
    if citation_warnings:
        print(f"경고: 인용 출처 의심 {len(citation_warnings)}개 대분류 — 화이트리스트 밖 문서를 '문서인용'으로 표시함:")
        for category, suspects in citation_warnings.items():
            for s in suspects:
                print(f"  - {category}: {s}")


def confirm(master_path: str, excel_path: str) -> dict:
    """확정 경로 A — 회계사가 편집해 재업로드한 batch_{id}-{부서}_final.xlsx를 반영한다.

    범위: **"추가판단 필요" 대분류의 Path 1/Path 2 해소만** 다룬다(CLAUDE.md §2 "회계사
    확정 피드백 루프" 정의 그대로). 명확 대분류의 승인/수정은 이 경로의 대상이 아니다 —
    엑셀이 계정코드 단위 행 구조라 대분류 전체를 여기서 재확정할 방법이 없고(계정코드
    여러 행에 값이 갈리면 대분류 단일 결정이 애매해진다), 그 경우는 Streamlit 확정 앱
    (경로 B)에서 처리하도록 CLAUDE.md에도 명시되어 있다.

    "새로 기입된 항목만" 원칙: accounts_master.json 기준으로 아직 카테고리분류상태가
    "추가판단필요(검토대기)"인 대분류만 대상으로 삼는다. 이미 다른 경로로 해소됐거나
    엑셀에 여전히 값이 비어있는 대분류는 건드리지 않는다(전체 재처리 금지). 같은
    대분류에 속한 여러 계정코드 행에 서로 다른 "사람 확정" 값이 입력되면 임의로
    하나를 고르지 않고 '이상'으로 보고한다.
    """
    master = load(master_path)
    rows = read_confirmations.read_confirmations(excel_path)["rows"]
    now = datetime.now(KST).isoformat()

    issues: list[dict] = []
    category_decisions: dict[str, dict] = {}
    skipped_no_decision: list[str] = []
    skipped_already_resolved = 0
    skipped_still_blank = 0

    for row in rows:
        if not row["needs_review"]:
            continue
        category = row["대분류"]
        cat = master.get("categories", {}).get(category)
        if cat is None:
            issues.append({"category": category, "사유": "categories에 없는 대분류 (재업로드 파일이 최신 마스터와 불일치할 수 있음)"})
            continue
        if cat.get("카테고리분류상태") != "추가판단필요(검토대기)":
            skipped_already_resolved += 1
            continue
        if not row["사람확정4type"]:
            skipped_still_blank += 1
            continue

        decision = {
            "category": category,
            "사람확정four_type": row["사람확정4type"],
            "확정여부": row["확정여부"] or "미확정",
            "확정원가동인": row["확정원가동인"] or "",
        }
        existing = category_decisions.get(category)
        if existing and existing != decision:
            issues.append({
                "category": category,
                "사유": f"같은 대분류의 서로 다른 계정코드 행에서 값이 어긋남 (계정코드 {row['계정코드']} 포함) — 모든 행을 동일하게 채워서 다시 업로드하세요.",
            })
            category_decisions.pop(category, None)
            continue
        category_decisions[category] = decision

    rerun_recommend: list[str] = []
    rerun_validate_only: list[str] = []
    for item in category_decisions.values():
        result = apply_category_item(master, item, now, issues, skipped_no_decision)
        if result:
            kind, category = result
            (rerun_recommend if kind == "rerun_recommend" else rerun_validate_only).append(category)

    save(master_path, master)

    summary = {
        "신규_반영_대분류수": len(category_decisions),
        "변경없음_이미해소": skipped_already_resolved,
        "변경없음_여전히미기입": skipped_still_blank,
        "재실행_필요_recommend": rerun_recommend,
        "재실행_필요_validate_only": rerun_validate_only,
        "이상": issues,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(
        f"신규 반영 {len(category_decisions)}건 "
        f"(Path1/재추천대기 {len(rerun_recommend)}건, Path2/검증대기 {len(rerun_validate_only)}건) — "
        f"이미해소 {skipped_already_resolved}건, 미기입 {skipped_still_blank}건은 그대로 유지"
    )
    if issues:
        print("경고: '이상' 항목이 있습니다 — 사용자에게 보고 후 처리하세요.")
    return summary


def apply_category_item(master: dict, item: dict, now: str, issues: list, skipped: list):
    category = item["category"]
    cat = master.get("categories", {}).get(category)
    if cat is None:
        issues.append({"category": category, "사유": "categories에 없는 대분류"})
        return None

    needs_review_item = "사람확정four_type" in item
    status = item.get("확정여부", "미확정")
    driver = item.get("확정원가동인") or ""

    if needs_review_item:
        human_type = item.get("사람확정four_type") or ""
        if not human_type:
            skipped.append(category)
            return None

        cat["four_type"] = human_type
        cat["추가판단필요여부"] = False
        cat["카테고리분류상태"] = "분류완료"
        cat["분류근거"] = (cat.get("분류근거") or "") + f" / Streamlit 확정 앱: four_type={human_type}"
        cat["마지막갱신"] = now

        if status == "확정" and driver:
            cat["추천상태"] = "검증중"
            cat["원가동인"] = {"recommended_drivers": [
                {"rank": 1, "driver": driver, "reason": "회계사가 4-type과 함께 직접 확정한 값 (Path 2 — AI 추천 아님)", "근거출처": "사람 직접 확정"},
            ]}
            cat["회계사확정"] = {"확정여부": "확정", "확정원가동인": driver, "확정순위": None, "확정일시": now}
            cat["처리경로"] = "사람직접확정"
            cat["판단경로"] = "사람 직접 확정"
            return ("rerun_validate_only", category)
        cat["처리경로"] = "AI추천-확정(대기)"
        return ("rerun_recommend", category)

    if status == "미확정":
        cat["회계사확정"] = {"확정여부": "미확정", "확정원가동인": None, "확정순위": None, "확정일시": now}
        cat["마지막갱신"] = now
        return None
    if status == "수정" and not driver:
        issues.append({"category": category, "사유": "확정 여부를 '수정'으로 선택했으나 확정 원가동인이 비어있음"})
        return None

    cat["회계사확정"] = {
        "확정여부": status, "확정원가동인": driver if status == "수정" else None,
        "확정순위": item.get("확정순위"), "확정일시": now,
    }
    cat["처리경로"] = "AI추천-확정"
    cat["마지막갱신"] = now
    if status == "수정":
        return ("rerun_validate_only", category)
    return None


def apply_subaccount_item(master: dict, item: dict, now: str, issues: list):
    """세부계정(계정코드) 단위로 적용방식/예외확정을 갱신한다.

    계정코드는 전사 공통 코드 체계라 부서 무관 단일 레코드다 — 예외 지정하면
    그 계정이 등장하는 모든 부서(accounts[code].등장부서)에 동일하게 적용된다.
    """
    code = item["계정코드"]
    acc = master.get("accounts", {}).get(code)
    if acc is None:
        issues.append({"계정코드": code, "사유": "accounts에 없는 계정코드"})
        return

    apply_mode = item.get("적용방식", "원칙준용")
    acc["적용방식"] = apply_mode
    acc["마지막갱신"] = now

    if apply_mode != "예외지정":
        acc["예외확정"] = {}
        return

    status = item.get("확정여부", "미확정")
    exc = acc.setdefault("예외확정", {})

    if status != "확정":
        exc["처리완료"] = False
        return

    four_type = item.get("확정four_type") or ""
    driver = item.get("확정원가동인") or ""
    if not four_type or not driver:
        issues.append({
            "계정코드": code,
            "사유": "예외 지정을 '확정'으로 선택했으나 4-type 또는 확정 원가동인이 비어있음",
        })
        return

    exc["four_type"] = four_type
    exc["추가판단필요여부"] = False
    exc["분류근거"] = f"회계사 예외 지정: 대분류 '{acc.get('대분류')}' 원칙과 별도로 이 계정만 직접 확정"
    exc["원가동인"] = {"recommended_drivers": [
        {"rank": 1, "driver": driver, "reason": "회계사가 예외 지정으로 직접 확정한 값 (사람 직접 확정 — 대분류 AI 추천 재활용 아님)", "근거출처": "사람 직접 확정"},
    ]}
    exc["회계사확정"] = {"확정여부": "확정", "확정원가동인": driver, "확정순위": None, "확정일시": now}
    exc["처리경로"] = "사람직접확정"
    exc["판단경로"] = "사람 직접 확정"
    exc["처리완료"] = True


def apply_browser_confirmations(master_path: str, confirmed_path: str):
    master = load(master_path)
    with open(confirmed_path, encoding="utf-8") as f:
        payload = json.load(f)

    now = datetime.now(KST).isoformat()
    rerun_recommend = []
    rerun_validate_only = []
    skipped_no_decision = []
    issues = []

    for item in payload.get("categories", []):
        result = apply_category_item(master, item, now, issues, skipped_no_decision)
        if result:
            kind, category = result
            (rerun_recommend if kind == "rerun_recommend" else rerun_validate_only).append(category)

    for item in payload.get("sub_accounts", []):
        apply_subaccount_item(master, item, now, issues)

    save(master_path, master)

    result = {
        "재실행_필요_recommend": rerun_recommend,
        "재실행_필요_validate_only": rerun_validate_only,
        "결정_없이_건너뜀": skipped_no_decision,
        "이상": issues,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if issues:
        print("경고: '이상' 항목이 있습니다 — 사용자에게 보고 후 처리하세요.")
    return result


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "identify" and len(sys.argv) == 4:
        identify(sys.argv[2], sys.argv[3])
    elif cmd == "complete" and len(sys.argv) == 7:
        complete(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6])
    elif cmd == "confirm" and len(sys.argv) == 4:
        confirm(sys.argv[2], sys.argv[3])
    elif cmd == "apply-browser-confirmations" and len(sys.argv) == 4:
        apply_browser_confirmations(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
