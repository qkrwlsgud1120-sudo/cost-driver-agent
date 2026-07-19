"""대분류(카테고리) 도출 및 원칙준용/예외지정 리졸브 공유 유틸.

segment_accounts.py, track_batch.py, write_segmentation_excel.py, write_results.py,
streamlit_app/app.py가 전부 이 모듈을 import해서 쓴다 — "대분류를 어떻게 뽑는가"와
"원칙 준용 vs 예외 지정 중 무엇이 이기는가"의 로직이 여러 곳에 중복되면 불일치 버그가
나기 가장 쉬운 지점이라 한 곳으로 강제 통일한다.
"""

# 계정명 표기 불일치를 정규화하는 테이블. 새 샘플에서 불일치가 발견될 때마다 추가한다.
# 예: {"복리후생": "복리후생비"}
CATEGORY_ALIASES: dict[str, str] = {}


def derive_category(account_name: str, alias_map: dict[str, str] = CATEGORY_ALIASES) -> str:
    """계정명에서 대분류를 도출한다.

    하이픈이 있으면 첫 세그먼트, 없으면 계정명 전체를 대분류로 삼는다.
    별칭 테이블에 등록된 표기는 정규화한다(예: "복리후생" -> "복리후생비").
    """
    base = account_name.split("-", 1)[0].strip() if "-" in account_name else account_name.strip()
    return alias_map.get(base, base)


def resolve_account_effective(master: dict, code: str, dept: str | None = None) -> dict:
    """계정코드의 유효 상태를 적용방식(원칙준용/예외지정)에 따라 계산한다.

    계정코드는 여러 부서에 걸쳐 등장해도 같은 계정(전사 공통 코드 체계)이므로, 적용방식/
    예외확정은 부서 무관 단일 값이다 — 세부계정 하나를 예외 지정하면 그 계정이 등장하는
    모든 부서에 동일하게 적용된다. `dept` 인자는 과거 (계정코드,부서) 단위 스키마와의
    호출부 호환을 위해 받되 무시한다(하위 호환용, 신규 코드에서는 생략 가능).

    예외지정이면 그 계정의 예외확정을, 원칙준용이면 대분류의 categories[대분류]를
    반환한다. 이 함수가 "무엇이 최종값인가"를 결정하는 유일한 지점이며, 다른 모든 소비자
    (track_batch, app.py, write_results.py 등)는 이 함수를 통해서만 최종값을 얻어야 한다.
    """
    acc = master["accounts"][code]
    category = acc.get("대분류")
    apply_mode = acc.get("적용방식") or "원칙준용"

    if apply_mode == "예외지정":
        source = acc.get("예외확정") or {}
    else:
        source = master.get("categories", {}).get(category) or {}

    return {"대분류": category, "적용방식": apply_mode, **source}


# "문서인용"으로 표시할 수 있는 참조 문서 화이트리스트. cost-driver-framework 스킬의
# 참조 문서만 인용 근거로 인정한다 — CLAUDE.md 같은 오케스트레이터 지침 문서나
# classified.json 같은 중간 산출물을 "문서인용"이라고 표시하는 것은 인용 오류다.
REFERENCE_DOCS = {"abc_costing_principles.md", "cost_classification_standard.md", "insurance_accounting_guide.md"}


def flag_suspect_citations(reason: str) -> list[str]:
    """근거출처가 "문서인용"인 근거 문장을 점검한다. 화이트리스트 문서를 하나라도 실제로
    언급하면 통과(다른 문서를 곁들여 언급해도 무방 — 예: classified.json을 보조 맥락으로
    함께 인용하는 것은 정상). 화이트리스트 문서를 전혀 언급하지 않으면 "문서인용"이라는
    표시 자체의 근거가 없다는 뜻이므로 의심 목록을 반환한다.

    result-validator가 이미 인용 진위를 검증하지만, 병합 단계에서 기계적으로 한 번 더
    걸러내는 안전망이다. 빈 리스트를 반환하면 의심되는 인용이 없다는 뜻(정상).
    """
    import re

    if not reason:
        return ["근거 문장 없음"]
    if any(doc in reason for doc in REFERENCE_DOCS):
        return []
    other_files = set(re.findall(r"[\w\-]+\.(?:md|json)", reason)) - REFERENCE_DOCS
    return sorted(other_files) if other_files else ["참조 문서(화이트리스트) 인용 없음"]
