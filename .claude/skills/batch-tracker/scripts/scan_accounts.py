"""Phase 0: 전체 부서 원본 파일을 스캔해 accounts_master.json을 생성한다.

계정코드가 2개 이상 부서에 등장하면 "공통", 1개 부서에만 등장하면 "특정"으로 판정한다.
LLM을 사용하지 않는 순수 규칙 기반 단계다.

Usage:
    python scan_accounts.py <input_departments_dir> <output_accounts_master_json>

<input_departments_dir> 안의 모든 .xlsx/.csv 파일을 부서 원본으로 간주한다.
필수 컬럼: 계정코드, 계정명, 부서명
"""
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = ["계정코드", "계정명", "부서명"]
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


def scan(input_dir: str) -> dict:
    dept_dir = Path(input_dir)
    files = sorted([*dept_dir.glob("*.xlsx"), *dept_dir.glob("*.csv")])
    if not files:
        raise FileNotFoundError(f"{input_dir}에 부서 원본 파일이 없습니다.")

    accounts: dict[str, dict] = {}
    departments_scanned: set[str] = set()

    for file in files:
        df = load_department_file(file)
        for _, row in df.iterrows():
            code = row["계정코드"]
            dept = row["부서명"]
            departments_scanned.add(dept)

            if code not in accounts:
                accounts[code] = {
                    "계정명": row["계정명"],
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
                }
            if dept not in accounts[code]["등장부서"]:
                accounts[code]["등장부서"].append(dept)

    for acc in accounts.values():
        acc["구분"] = "공통" if len(acc["등장부서"]) >= 2 else "특정"

    return {
        "generated_at": datetime.now(KST).isoformat(),
        "departments_scanned": sorted(departments_scanned),
        "accounts": accounts,
    }


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)

    input_dir, output_json = sys.argv[1], sys.argv[2]
    master = scan(input_dir)

    Path(output_json).parent.mkdir(parents=True, exist_ok=True)
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(master, f, ensure_ascii=False, indent=2)

    common = sum(1 for a in master["accounts"].values() if a["구분"] == "공통")
    specific = len(master["accounts"]) - common
    print(
        f"부서 {len(master['departments_scanned'])}개, "
        f"계정 {len(master['accounts'])}개 (공통 {common} / 특정 {specific}) → {output_json}"
    )


if __name__ == "__main__":
    main()
