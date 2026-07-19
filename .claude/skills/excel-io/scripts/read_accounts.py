"""부서 원본 엑셀/CSV 파일을 읽어 정규화된 JSON 레코드 목록으로 변환한다.

Usage:
    python read_accounts.py <input_file> <output_json>

입력 파일 필수 컬럼: 계정코드, 계정명, 금액, 부서명
"""
import json
import sys
from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = ["계정코드", "계정명", "금액", "부서명"]


def read_accounts(input_path: str) -> list[dict]:
    path = Path(input_path)
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path, dtype={"계정코드": str})
    else:
        df = pd.read_excel(path, dtype={"계정코드": str})

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"필수 컬럼 누락: {missing} (파일: {path.name}, 발견된 컬럼: {list(df.columns)})"
        )

    df["계정코드"] = df["계정코드"].astype(str).str.strip()
    df["계정명"] = df["계정명"].astype(str).str.strip()
    df["부서명"] = df["부서명"].astype(str).str.strip()

    records = df[REQUIRED_COLUMNS].to_dict(orient="records")
    for r in records:
        r["원본파일"] = path.name
    return records


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)

    input_file, output_json = sys.argv[1], sys.argv[2]
    records = read_accounts(input_file)

    Path(output_json).parent.mkdir(parents=True, exist_ok=True)
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    print(f"{len(records)}건 → {output_json}")


if __name__ == "__main__":
    main()
