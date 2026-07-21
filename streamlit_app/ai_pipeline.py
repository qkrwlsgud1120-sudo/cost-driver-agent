"""Streamlit 앱이 Claude Code 세션 없이도 완전히 독립 구동되도록, Anthropic API를 직접
호출해 Phase 0.5(LLM 재확인)와 Phase 1(4-type 분류 → 원가동인 추천 → 자기검증)을 수행한다.

`.claude/agents/account-classifier.md`, `driver-recommender.md`, `result-validator.md`에
정의된 판단 절차·출력 스키마를 그대로 시스템 프롬프트로 옮겨, Claude Code의 Task
서브에이전트 호출을 단일 Anthropic Messages API 호출로 대체한다. 판단 원칙(참조 문서
인용, 근거 작성, 4-type 판정 기준 등)은 원본 .md 파일과 100% 동일하게 유지해야 한다 —
이 모듈을 고칠 때는 대응하는 .claude/agents/*.md도 함께 확인한다.

로컬 개발(Claude Code 세션 안)에서는 여전히 진짜 서브에이전트(사람 오케스트레이터가
판단 품질을 직접 검토)를 쓰는 것이 원칙이다 — 이 모듈은 배포된 Streamlit 앱이 사람의
개입 없이도 동작하기 위한 별도 경로이며, CLAUDE.md의 서브에이전트 오케스트레이션을
대체하지 않는다.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# batch-tracker/segment_accounts 스크립트는 진행 로그를 print()로 찍는데, 그 문장에 —(em
# dash) 같은 문자가 섞여 있다. Streamlit을 Windows에서 실행하면 표준출력이 cp949로 열려
# 이런 문자를 만나면 UnicodeEncodeError로 프로세스 전체가 죽는다(이 모듈이 Claude Code
# 세션 없이 track_batch.complete()/segment_accounts.apply_llm()을 직접 호출하는 첫
# 소비자라 이번에 실제로 재현됨). 이 파이프라인 전체에서 안전하게 쓰기 위해 표준출력
# 인코딩을 UTF-8로 맞춘다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

BASE_DIR = Path(__file__).resolve().parent.parent
REFERENCE_DOCS_DIR = BASE_DIR / ".claude" / "skills" / "cost-driver-framework" / "references"
KST = timezone(timedelta(hours=9))

# 모델 티어 정책은 CLAUDE.md §4와 동일하게 유지한다 — 분류·추천은 판단 품질이 핵심이라
# Opus, 검증은 형식·논리 확인 위주라 Sonnet.
MODEL_CLASSIFY = "claude-opus-4-8"
MODEL_RECOMMEND = "claude-opus-4-8"
MODEL_VALIDATE = "claude-sonnet-5"
MODEL_RECHECK = "claude-opus-4-8"

MAX_RECOMMEND_RETRY = 2


class AIPipelineError(Exception):
    """API 호출·응답 파싱 실패 등 파이프라인 실행 중 발생한 오류."""


# ---------------------------------------------------------------------------
# API 키 / 클라이언트
# ---------------------------------------------------------------------------

def _load_dotenv_if_present() -> None:
    """python-dotenv 의존성을 추가하지 않고 .env 파일을 최소한으로 읽어들인다.
    이미 설정된 환경변수는 덮어쓰지 않는다(터미널에서 직접 export한 값 우선)."""
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv_if_present()


def get_api_key() -> str | None:
    """우선순위: Streamlit secrets(배포 환경) → 환경변수/.env(로컬 개발)."""
    try:
        import streamlit as st
        if "ANTHROPIC_API_KEY" in st.secrets:
            return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        pass
    return os.environ.get("ANTHROPIC_API_KEY")


def is_configured() -> bool:
    return bool(get_api_key())


def get_client():
    import anthropic
    api_key = get_api_key()
    if not api_key:
        raise AIPipelineError(
            "ANTHROPIC_API_KEY가 설정되지 않았습니다. 로컬에서는 .env 또는 터미널 환경변수로, "
            "Streamlit Cloud 배포 환경에서는 앱 설정의 Secrets에 등록하세요."
        )
    return anthropic.Anthropic(api_key=api_key)


# ---------------------------------------------------------------------------
# 참조 문서 (account-classifier / driver-recommender / result-validator 공통 근거 자료)
# ---------------------------------------------------------------------------

_REFERENCE_DOCS_CACHE: str | None = None


def load_reference_docs() -> str:
    global _REFERENCE_DOCS_CACHE
    if _REFERENCE_DOCS_CACHE is not None:
        return _REFERENCE_DOCS_CACHE
    parts = []
    for name in ["abc_costing_principles.md", "cost_classification_standard.md", "insurance_accounting_guide.md"]:
        path = REFERENCE_DOCS_DIR / name
        parts.append(f"### {name}\n\n{path.read_text(encoding='utf-8')}")
    _REFERENCE_DOCS_CACHE = "\n\n---\n\n".join(parts)
    return _REFERENCE_DOCS_CACHE


# ---------------------------------------------------------------------------
# 공통 JSON 호출 헬퍼
# ---------------------------------------------------------------------------

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _extract_json(text: str):
    cleaned = _JSON_FENCE_RE.sub("", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # 모델이 설명을 덧붙인 경우, 첫 '{'/'[' 부터 마지막 '}'/']' 까지만 추출해 재시도한다.
    start_candidates = [i for i in (cleaned.find("{"), cleaned.find("[")) if i != -1]
    end_candidates = [i for i in (cleaned.rfind("}"), cleaned.rfind("]")) if i != -1]
    if start_candidates and end_candidates:
        start, end = min(start_candidates), max(end_candidates) + 1
        try:
            return json.loads(cleaned[start:end])
        except json.JSONDecodeError as e:
            raise AIPipelineError(f"모델 응답을 JSON으로 파싱하지 못했습니다: {e}\n원문: {text[:800]}")
    raise AIPipelineError(f"모델 응답에서 JSON을 찾지 못했습니다. 원문: {text[:800]}")


def _call_json(system_prompt: str, user_content: str, model: str, max_tokens: int = 4096):
    client = get_client()
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
    except Exception as e:  # anthropic.APIError 및 네트워크 오류 등을 사용자 메시지로 통일
        raise AIPipelineError(f"Anthropic API 호출 실패: {e}")
    text = "".join(block.text for block in resp.content if getattr(block, "type", None) == "text")
    return _extract_json(text)


# ---------------------------------------------------------------------------
# Phase 0.5 재확인 모드 (account-classifier.md "Phase 0.5 재확인 모드" 절 이식)
# ---------------------------------------------------------------------------

def _build_recheck_system_prompt() -> str:
    return f"""너는 원가동인 추천 시스템의 계정 분류 판단 모듈이다. 아래는 이 판단을 수행하는
account-classifier 서브에이전트의 "Phase 0.5 재확인 모드" 지침이다. 이 지침을 그대로 따른다.

Phase 1(배치 분류)과는 별도로, Phase 0.5(공통/특정 대분류 분리)에서 호출된다. 규칙 기반 1차 판별로
"공통 후보"로 분류됐지만 부서별 금액 편차가 임계치를 초과해 재확인이 필요한 대분류 하나를 판단한다.
**4-type 분류는 이 모드의 책임이 아니다** — 오직 "여러 부서에 등장하는 이 대분류를 하나의 공통
원가동인 대상으로 묶어도 되는지"만 판단한다.

## 판단 절차
1. 부서별 금액 규모와 플래그 사유(편차 비율 또는 특정 부서 쏠림)를 확인한다.
2. 아래 참조 문서(특히 insurance_accounting_guide.md)를 근거로, 이 대분류가 부서마다 실질적으로
   같은 원가 발생 구조(같은 원인·같은 통제 주체 성격)를 공유하는지, 아니면 부서마다 발생 원인이
   달라 통일된 원가동인 적용이 부적절한지 판단한다. 단순히 "금액이 다르다"만으로 특정전환하지
   않는다 — 금액 차이가 부서 규모(인원수 등) 차이를 반영할 뿐이라면 공통 유지가 타당할 수 있다.
3. 참조 문서에서 근거를 찾을 수 없으면 "일반 회계 지식 기반 추정"이라고 명시한다.

## 출력
아래 JSON 형식 하나만 출력한다. 다른 설명·마크다운 코드펜스 없이 JSON 객체 그 자체만 출력한다.
{{"대분류": "...", "판정": "공통유지 또는 특정전환", "사유": "..."}}

## 참조 문서
{load_reference_docs()}
"""


def llm_recheck_segment(item: dict) -> dict:
    """Phase 0.5 needs_llm_recheck 항목 하나를 판정한다."""
    user_content = json.dumps({
        "대분류": item["대분류"],
        "등장부서": item["등장부서"],
        "부서별_금액": item.get("부서별_금액"),
        "플래그_사유": item.get("플래그_사유"),
        "sub_accounts": item.get("sub_accounts"),
    }, ensure_ascii=False, indent=2)
    result = _call_json(_build_recheck_system_prompt(), user_content, MODEL_RECHECK, max_tokens=1024)
    if result.get("판정") not in ("공통유지", "특정전환"):
        raise AIPipelineError(f"'{item['대분류']}' 재확인 판정이 올바르지 않습니다: {result.get('판정')!r}")
    return result


# ---------------------------------------------------------------------------
# Phase 1 ③ 대분류 분류 (account-classifier.md 이식)
# ---------------------------------------------------------------------------

def _build_classify_system_prompt() -> str:
    return f"""너는 원가동인 추천 시스템의 계정 분류 판단 모듈이다. 아래는 이 판단을 수행하는
account-classifier 서브에이전트의 지침이다. 이 지침을 그대로 따른다.

대분류(하이픈 앞부분 등으로 계정명에서 도출한 카테고리) 원본 데이터를 받아 4-type(직접귀속형/
배부형/공통비형/기타) 중 하나로 분류하고, 그 판단 근거를 작성한다. **판정 단위는 계정코드가
아니라 대분류다** — 하나의 대분류 아래 세부계정이 여러 개 있어도 그 전체에 대해 4-type 하나를
매긴다. 원가동인 추천은 이 단계의 책임이 아니다 — "이 대분류가 어떤 유형인가"까지만 판단한다.

## 판단 절차
1. **공통 대분류**(2개 이상 부서 등장)인 경우: 그 대분류에 속한 세부계정들이 여러 부서에 걸쳐
   실질적으로 같은 원가 발생 구조를 공유하는지 세부계정 목록 전체를 보고 종합 판단한다.
2. **부서 특정 대분류**(1개 부서만 등장)인 경우: 그 부서 맥락에서 개별적으로 판단한다.
2-1. **직접귀속형 vs 공통비형 판정 시 "여러 부서에 나뉘어 계상됨" 자체를 공통비형의 근거로 쓰지
     않는다.** 공통비형 정의는 "전사 차원의 의사결정으로 발생(특정 조직의 활동과 무관하게 회사
     전체 판단으로 발생)"이며, 이는 의사결정 주체가 누구인가의 문제이지 몇 개 부서에 계상되어
     있는가의 문제가 아니다. 특정 부서(들)가 주도적으로 기획·집행하며 예산 편성·절감의 책임과
     권한도 그 부서에 있는 활동이라면 직접귀속형이 맞다. 진짜 공통비형은 기부금·주주총회비처럼
     "어느 부서가 계상하든 그 부서의 활동·예산권한과 무관하게 회사 전체 판단으로 발생"하는
     경우로 한정한다.
2-2. 대분류 안에서 "부서가 주도적으로 기획·집행하는 세부계정 그룹"과 "그렇지 않은(전사
     의사결정성) 세부계정 그룹"이 섞여 있으면, 다수 쪽 성격으로 대분류 전체를 판정하고 소수의
     이질적 세부계정은 분류근거에 그 사실을 명시한다. 비중이 팽팽하면 추가판단 필요로 표시한다.
3. 판단 시 아래 참조 문서(특히 cost_classification_standard.md, insurance_accounting_guide.md)를
   반드시 참조하고, 근거 문장에 어떤 문서의 어떤 내용을 근거로 삼았는지 인용한다. 참조 문서에서
   근거를 찾을 수 없으면 "일반 회계 지식 기반 추정"이라고 명시한다.
4. 자기신뢰도(0~100)를 매긴다. 70 미만이면 **추가판단 필요 대분류**로 표시한다 — 4-type을
   강제로 배정하지 않고, 대분류 성격에 대한 상세 설명(정의/추정 근거/왜 추가판단이 필요한지)만
   작성한다.
5. 세부계정들의 원가 발생 구조가 서로 너무 달라 대분류 전체를 하나의 four_type으로 묶기
   부적절하다고 판단되면, 자기신뢰도와 무관하게 추가판단 필요로 표시하고 분류근거에 왜 세부계정
   성격이 이질적인지 구체적으로 설명한다. 한 대분류 안에 "등장부서가 2개 이상인 세부계정"과
   "1개뿐인 세부계정"이 섞여 있으면, 그 소수 세부계정들이 다수와 실질적으로 같은 원가 발생
   구조를 공유하는지 반드시 검토하고 판단 결과를 분류근거에 명시한다. 등장부서 수 차이 자체가
   추가판단 필요를 뜻하지는 않는다 — 성격이 동질적이면 하나로 묶는 것이 맞다.
6. **세부계정 설명**: 입력에 포함된 세부계정(계정코드) 전체에 대해, 그 계정이 통상 어떤 성격의
   비용인지 1~2문장으로 설명한다. 참조 문서에 직접적인 근거가 있으면 인용하고 근거출처를
   "문서인용"으로 표시한다. 없으면 계정명과 일반적인 회계 관행에 기반해 추정하고 근거출처를
   "일반적인 회계 관행 기반 추정"으로 표시한다 — 근거 없이 지어내지 않는다. **근거출처를
   "문서인용"으로 표시하려면 설명 문장 안에 그 문서 파일명(abc_costing_principles.md /
   cost_classification_standard.md / insurance_accounting_guide.md)을 반드시 그대로
   언급한다** — 언급 없이 "문서인용"만 표시하면 병합 단계의 기계적 안전망에 걸려 경고로 남는다.

## 출력
아래 JSON 형식 하나만 출력한다. 다른 설명·마크다운 코드펜스 없이 JSON 객체 그 자체만 출력한다.
{{
  "대분류": "...",
  "four_type": "직접귀속형" | "배부형" | "공통비형" | "기타" | null,
  "분류근거": "...",
  "근거출처": "문서인용" | "일반추론",
  "자기신뢰도": 0-100,
  "추가판단필요여부": true | false,
  "세부계정_설명": [{{"계정코드": "...", "설명": "...", "근거출처": "문서인용" | "일반적인 회계 관행 기반 추정"}}]
}}
추가판단 필요면 "four_type"은 null로 두고 "분류근거"에 정의/추정근거/왜 추가판단이 필요한지를 담는다.
세부계정_설명은 four_type 판정과 무관하게 입력받은 세부계정 전체를 빠짐없이 포함해야 한다.

## 참조 문서
{load_reference_docs()}
"""


def classify_category(category: str, cat_state: dict, sub_accounts: list[dict]) -> dict:
    """대분류 하나를 4-type으로 분류한다 (account-classifier 대체)."""
    user_content = json.dumps({
        "대분류": category,
        "구분": cat_state.get("구분"),
        "등장부서": cat_state.get("등장부서"),
        "세부계정": [
            {"계정코드": sa.get("계정코드"), "계정명": sa.get("계정명"), "등장부서": sa.get("등장부서")}
            for sa in sub_accounts
        ],
    }, ensure_ascii=False, indent=2)
    result = _call_json(_build_classify_system_prompt(), user_content, MODEL_CLASSIFY, max_tokens=4096)
    result.setdefault("대분류", category)
    return result


# ---------------------------------------------------------------------------
# Phase 1 ⑤ 원가동인 추천 (driver-recommender.md 이식)
# ---------------------------------------------------------------------------

def _build_recommend_system_prompt() -> str:
    return f"""너는 원가동인 추천 시스템의 원가동인(cost driver) 추천 모듈이다. 아래는 이 판단을
수행하는 driver-recommender 서브에이전트의 지침이다. 이 지침을 그대로 따른다.

4-type이 확정된 대분류에 대해 적합한 원가동인을 추천한다. **추천 단위는 대분류다** — 그 아래
세부계정 개별 추천은 만들지 않는다. 4-type 판정 자체는 이미 끝난 결과를 입력으로만 받는다.

## 판단 절차
1. 아래 참조 문서(특히 abc_costing_principles.md)를 참조해, four_type에 맞는 원가동인 후보를
   도출한다. 판단 맥락은 그 대분류 아래 실제로 존재하는 세부계정명·부서 분포를 참고한다.
2. **후보는 최대 3순위까지 제시하되, 억지로 3개를 채우지 않는다.** 다만 습관적으로 1순위만
   제시하지 않는다 — 최소한 "1순위보다 정밀도·데이터 확보 용이성이 낮은 대안"을 한 번은
   검토해보고, 그럴듯한 대안이 하나라도 있으면 2순위로 포함시킨다. 1순위 하나만 제시해도 되는
   경우는 (a) four_type이 공통비형이라 애초에 "배분 제외" 하나만 성립하는 경우, (b) 세부계정이
   사실상 단일 계정뿐이고 대안적 배부기준 자체가 존재하지 않는 경우로 좁게 한정한다.
3. 순위마다 근거를 개별적으로 작성한다. 2순위 이후는 "왜 1순위보다 우선순위가 낮은지"도 함께
   설명한다(데이터 확보가 더 어렵다, 인과관계가 상대적으로 약하다 등).
4. 근거 문장은 참조 문서를 인용하고, 인용할 수 없으면 "일반 회계 지식 기반 추정"임을 명시한다.
   이 표시는 순위마다 개별적으로 한다. **근거출처를 "문서인용"으로 표시하려면 reason 문장
   안에 그 문서 파일명(abc_costing_principles.md / cost_classification_standard.md /
   insurance_accounting_guide.md)을 반드시 그대로 언급한다** — 파일명 언급 없이 "문서인용"만
   표시하면 병합 단계의 기계적 안전망에 걸려 경고로 남는다.
5. **four_type이 "공통비형"인 대분류는 배부 대상이 아니다.** recommended_drivers를 빈 배열로
   두지 말고, rank 1에 driver="배분 제외(전사 공통비)"인 항목 1개만 넣는다.

## 출력
아래 JSON 형식 하나만 출력한다. 다른 설명·마크다운 코드펜스 없이 JSON 객체 그 자체만 출력한다.
{{
  "recommended_drivers": [
    {{"rank": 1, "driver": "...", "reason": "...", "근거출처": "문서인용" | "일반 회계 지식 기반 추정"}}
  ]
}}

## 참조 문서
{load_reference_docs()}
"""


def recommend_drivers(classify_record: dict, cat_state: dict, retry_feedback: str | None = None) -> dict:
    """분류 완료된 대분류에 대해 원가동인을 추천한다 (driver-recommender 대체).

    반환값은 classify_record의 분류근거/세부계정_설명을 그대로 옮겨 담아, result-validator가
    한 번에 인용 진위를 검증하고 track_batch.complete()가 그대로 병합할 수 있게 한다.
    """
    user_content = json.dumps({
        "대분류": classify_record["대분류"],
        "four_type": classify_record.get("four_type"),
        "등장부서": cat_state.get("등장부서"),
        "세부계정_설명": classify_record.get("세부계정_설명", []),
    }, ensure_ascii=False, indent=2)
    if retry_feedback:
        user_content += (
            f"\n\n[재시도] 이전 추천이 검증에서 실패했다. 사유: {retry_feedback}\n"
            "이 문제를 반영해 다시 추천하라."
        )
    result = _call_json(_build_recommend_system_prompt(), user_content, MODEL_RECOMMEND, max_tokens=4096)
    return {
        "대분류": classify_record["대분류"],
        "four_type": classify_record.get("four_type"),
        "분류근거": classify_record.get("분류근거"),
        "추가판단필요여부": False,
        "recommended_drivers": result.get("recommended_drivers", []),
        "세부계정_설명": classify_record.get("세부계정_설명", []),
    }


# ---------------------------------------------------------------------------
# Phase 1 ⑥ 추천 결과 자기검증 (result-validator.md 이식)
# ---------------------------------------------------------------------------

def _build_validate_system_prompt() -> str:
    return f"""너는 원가동인 추천 시스템의 검증 모듈이다. 아래는 이 판단을 수행하는 result-validator
서브에이전트의 지침이다. 이 지침을 그대로 따른다. 새로운 추천을 만들지 않는다 — 기존 추천이
타당한지 판정하고, 문제가 있으면 왜 실패했는지 구체적으로 기록한다.

## 검증 항목
1. **형식 검증**: recommended_drivers 배열이 존재하고 최소 1개 이상의 원소를 가지며, 각 원소에
   rank/driver/reason/근거출처가 모두 채워져 있는가. rank가 1부터 빈 번호 없이 오름차순인가.
2. **논리적 적합성**: 각 순위의 드라이버가 해당 대분류의 four_type 및 세부계정 성격과 실제로
   인과관계가 있는가.
3. **순위 간 논리적 일관성**: 2순위 이후의 reason에 "왜 1순위보다 낮은지"에 대한 설명이 있는가,
   2순위 근거가 1순위보다 오히려 더 설득력 있게 작성되어 순위가 뒤바뀌어야 할 것처럼 보이지는
   않는가, 같은 근거 문장을 순위만 바꿔 복사하지 않았는가.
4. **인용 진위 검증**: 근거출처가 "문서인용"으로 표시된 항목에 대해, 아래 참조 문서를 실제로
   대조해 그 근거가 문서에 존재하는지 확인한다. 문서에 없는데 "문서인용"으로 표시되어 있으면
   실패 처리한다 — 순위마다 개별적으로 확인한다.
5. **세부계정 설명 인용 진위 검증**: 세부계정_설명의 각 원소에 대해서도 4번과 동일한 기준을
   적용한다. 세부계정 하나라도 날조된 인용이 있으면 그 대분류 전체를 실패로 표시한다.

## 출력
아래 JSON 형식 하나만 출력한다. 다른 설명·마크다운 코드펜스 없이 JSON 객체 그 자체만 출력한다.
{{"검증결과": "통과" | "실패", "검증사유": null 또는 "어떤 순위에서 무엇이 문제였는지 구체적으로"}}
지어낸 근거를 봐주지 말고, 실제로 타당성이 의심스러우면 가차없이 "실패"로 판정한다.

## 참조 문서
{load_reference_docs()}
"""


def validate_recommendation(record: dict) -> dict:
    """recommend_drivers()의 결과를 검증한다 (result-validator 대체)."""
    user_content = json.dumps({
        "대분류": record["대분류"],
        "four_type": record.get("four_type"),
        "recommended_drivers": record.get("recommended_drivers", []),
        "세부계정_설명": record.get("세부계정_설명", []),
    }, ensure_ascii=False, indent=2)
    result = _call_json(_build_validate_system_prompt(), user_content, MODEL_VALIDATE, max_tokens=2048)
    if result.get("검증결과") not in ("통과", "실패"):
        raise AIPipelineError(f"'{record['대분류']}' 검증결과가 올바르지 않습니다: {result.get('검증결과')!r}")
    return result


# ---------------------------------------------------------------------------
# 오케스트레이션 (CLAUDE.md의 Phase 0.5 재확인 루프 / Phase 1 ③~⑦ 루프를
# Task 서브에이전트 위임 없이 직접 API 호출로 순차 실행한다)
# ---------------------------------------------------------------------------

def run_phase05_recheck(seg_path: Path, verdicts_path: Path, master_path: Path, progress_cb=None) -> dict:
    """account_segmentation.json의 needs_llm_recheck 전체를 판정하고 반영한다."""
    import segment_accounts  # batch-tracker 스킬 스크립트 (app.py가 sys.path에 등록해둠)

    with open(seg_path, encoding="utf-8") as f:
        seg = json.load(f)
    pending = seg.get("needs_llm_recheck", [])

    verdicts = []
    for i, item in enumerate(pending):
        if progress_cb:
            progress_cb(i, len(pending), f"{item['대분류']} — 공통/특정 재확인 중...")
        verdicts.append(llm_recheck_segment(item))

    existing: list[dict] = []
    if verdicts_path.exists():
        existing = json.loads(verdicts_path.read_text(encoding="utf-8"))
    by_category = {v["대분류"]: v for v in existing}
    for v in verdicts:
        by_category[v["대분류"]] = v
    verdicts_path.parent.mkdir(parents=True, exist_ok=True)
    verdicts_path.write_text(json.dumps(list(by_category.values()), ensure_ascii=False, indent=2), encoding="utf-8")

    if pending:
        segment_accounts.apply_llm(str(seg_path), str(verdicts_path), str(master_path), str(seg_path))
    return {"처리건수": len(pending), "판정": verdicts}


def run_phase1_pipeline(master_path: Path, log_path: Path, progress_cb=None) -> dict:
    """분류 미완료 대분류 전체에 대해 ③ 분류 → ⑤ 추천 → ⑥ 검증(최대 재시도 2회)을 순차
    실행하고, 통과분을 track_batch.complete()로 한 번에 병합한다."""
    import track_batch  # batch-tracker 스킬 스크립트 (app.py가 sys.path에 등록해둠)

    with open(master_path, encoding="utf-8") as f:
        master = json.load(f)
    categories_state = master.get("categories", {})
    by_category: dict[str, list[dict]] = {}
    for code, acc in master.get("accounts", {}).items():
        by_category.setdefault(acc.get("대분류"), []).append({**acc, "계정코드": code})

    todo = [cat for cat, state in categories_state.items() if state.get("카테고리분류상태") != "분류완료"]
    validated: list[dict] = []
    stats = {"대상": len(todo), "분류완료": 0, "추가판단필요": 0, "추천완료": 0, "검증실패_에스컬레이션": []}

    for i, category in enumerate(todo):
        cat_state = categories_state[category]
        sub_accounts = by_category.get(category, [])

        if progress_cb:
            progress_cb(i, len(todo), f"{category} — 4-type 분류 중...")
        classify_record = classify_category(category, cat_state, sub_accounts)
        stats["분류완료"] += 1

        if classify_record.get("추가판단필요여부"):
            stats["추가판단필요"] += 1
            validated.append({**classify_record, "recommended_drivers": []})
            continue

        if progress_cb:
            progress_cb(i, len(todo), f"{category} — 원가동인 추천 중...")
        record = recommend_drivers(classify_record, cat_state)

        feedback = None
        result = {"검증결과": "실패", "검증사유": "미실행"}
        for attempt in range(MAX_RECOMMEND_RETRY + 1):
            if progress_cb:
                progress_cb(i, len(todo), f"{category} — 추천 검증 중 (시도 {attempt + 1})...")
            result = validate_recommendation(record)
            if result["검증결과"] == "통과":
                break
            feedback = result["검증사유"]
            if attempt < MAX_RECOMMEND_RETRY:
                record = recommend_drivers(classify_record, cat_state, retry_feedback=feedback)

        if result["검증결과"] != "통과":
            stats["검증실패_에스컬레이션"].append({"대분류": category, "사유": feedback})
            continue

        stats["추천완료"] += 1
        validated.append(record)

    if validated:
        batch_id = f"live-api-{datetime.now(KST).strftime('%Y%m%d%H%M%S')}"
        tmp_path = master_path.parent / f".ai_pipeline_validated_{batch_id}.json"
        tmp_path.write_text(json.dumps(validated, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            track_batch.complete(str(master_path), str(log_path), batch_id, "AI 파이프라인(직접 API)", str(tmp_path))
        finally:
            tmp_path.unlink(missing_ok=True)

    return stats


# ---------------------------------------------------------------------------
# 사용량 제한 (공개 배포 비용 노출 방지)
# ---------------------------------------------------------------------------

SESSION_RUN_LIMIT = 3
DAILY_GLOBAL_LIMIT = 50
DAILY_COUNTER_PATH = BASE_DIR / "output" / ".api_usage_daily.json"


def _today_str() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


def read_daily_usage() -> int:
    if not DAILY_COUNTER_PATH.exists():
        return 0
    try:
        data = json.loads(DAILY_COUNTER_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0
    if data.get("date") != _today_str():
        return 0
    return int(data.get("count", 0))


def increment_daily_usage(by: int = 1) -> int:
    today = _today_str()
    count = read_daily_usage() + by
    DAILY_COUNTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    DAILY_COUNTER_PATH.write_text(
        json.dumps({"date": today, "count": count}, ensure_ascii=False), encoding="utf-8",
    )
    return count


def check_usage_limits(session_run_count: int) -> str | None:
    """실행 전 한도를 확인한다. 막혀야 하면 사용자에게 보여줄 안내 메시지를, 통과하면 None을 반환한다."""
    if session_run_count >= SESSION_RUN_LIMIT:
        return (
            f"이 브라우저 세션에서 데모 실행 한도({SESSION_RUN_LIMIT}회)에 도달했습니다. "
            "공개 데모는 API 비용 보호를 위해 세션당 실행 횟수를 제한하고 있습니다. "
            "더 사용해보시려면 GitHub 레포의 README를 참고해 로컬에서 직접 실행해주세요."
        )
    if read_daily_usage() >= DAILY_GLOBAL_LIMIT:
        return (
            f"오늘 전체 방문자의 데모 실행 한도({DAILY_GLOBAL_LIMIT}회)에 도달했습니다. "
            "공개 데모의 API 비용 보호를 위한 일일 한도이며, 한국 시간 자정에 초기화됩니다. "
            "로컬에서 직접 실행해보시려면 GitHub 레포의 README를 참고해주세요."
        )
    return None
