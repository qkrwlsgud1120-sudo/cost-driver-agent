---
name: cost-driver-framework
description: ABC 원가계산 원칙, 4-type 분류체계, 보험회계 일반 지식(계정 성격, 업계 특정 비용 구조) 참조 문서를 제공한다. account-classifier, driver-recommender, result-validator 서브에이전트가 판단·근거 작성·인용 검증 시 반드시 참조해야 한다.
---

# cost-driver-framework

계정 분류와 원가동인 추천의 판단 기준이 되는 참조 문서 모음. 이 스킬 자체는 판단하지 않고, 서브에이전트가 근거를 인용할 1차 소스를 제공한다.

## 참조 문서
- [references/cost_classification_standard.md](references/cost_classification_standard.md) — 4-type 분류체계 정의 (account-classifier가 사용)
- [references/abc_costing_principles.md](references/abc_costing_principles.md) — ABC(활동기준원가계산) 원가배부 원칙, 4-type별 적합 원가동인 후보군 (driver-recommender가 사용)
- [references/insurance_accounting_guide.md](references/insurance_accounting_guide.md) — 보험업 계정과목/비용구조 일반 지식 (account-classifier, driver-recommender 공통 사용)

## 인용 규칙 (모든 서브에이전트 공통)
1. 분류·추천 근거를 작성할 때는 위 참조 문서 중 실제로 사용한 문서명과 근거 부분을 명시한다.
2. 참조 문서에서 근거를 찾을 수 없는 경우, 임의로 그럴듯한 근거를 지어내지 말고 **"일반 회계 지식 기반 추정"**이라고 명시한다.
3. result-validator는 ⑥ 단계에서 각 근거 문장이 "문서 인용"인지 "일반 추론"인지 태깅해 검증한다.

## 문서 성격
`abc_costing_principles.md`, `cost_classification_standard.md`, `insurance_accounting_guide.md`는 특정 프로젝트·고객사의 실사 자료가 아니라, 공개된 관리회계·보험회계 일반론(ABC 원가계산, 책임회계의 통제가능성 원칙, 보험회계 해설서 수준의 도메인 지식)을 바탕으로 재구성한 문서다. 특정 계정코드·금액·고객사 정보를 포함하지 않으며, 실제 프로젝트에 투입할 때는 대상 회사의 실사 자료로 별도 보강이 필요하다.
