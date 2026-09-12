"""상품 페이지를 읽고 집필 설계도(ProductBrief)를 만든다.

한 번의 호출로 특징, 타깃 고객, 사용 시나리오, 구매 고민, 장단점, FAQ, 추천 대상을
모두 뽑는다. 이후 모든 집필 단계가 이 설계도만 참조하므로 원문을 반복해서 읽지 않는다.
"""

from __future__ import annotations

from ai.client import MyGenAssistClient
from ai.prompts import BASE_RULES
from core.models import FAQ, Concern, Feature, Persona, ProductBrief, Product, Scenario

_SCHEMA = """{
  "category": "상품 카테고리 (예: 골프 거리측정기, 수분 세럼)",
  "one_liner": "이 상품을 한 문장으로",
  "features": [
    {"name": "특징 이름", "detail": "페이지에 근거가 있는 구체적 설명", "benefit": "그래서 사용자에게 뭐가 좋은지"}
  ],
  "personas": [
    {"who": "구체적인 인물상", "pain": "그 사람이 겪는 불편", "why_fits": "이 상품이 맞는 이유"}
  ],
  "scenarios": [
    {"when": "언제/어디서", "situation": "그 상황 묘사", "outcome": "이 상품으로 달라지는 점"}
  ],
  "concerns": [
    {"worry": "살까 말까 망설이게 하는 지점", "answer": "그에 대한 솔직한 답"}
  ],
  "pros": ["장점", "4~6개"],
  "cons": ["아쉬운 점. 반드시 솔직하게. 3개 이상", "장점만 쓰면 광고로 보여 신뢰를 잃는다"],
  "faqs": [{"question": "실제로 검색할 법한 질문", "answer": "2~3문장 답변"}],
  "recommended_for": ["이런 사람에게 추천", "3~4개"],
  "not_recommended_for": ["이런 사람에겐 비추천", "2~3개"]
}"""


def analyze(client: MyGenAssistClient, product: Product) -> ProductBrief:
    user = f"""아래 상품 페이지를 분석해 블로그 리뷰 집필용 설계도를 만들어라.

{product.as_prompt_context()}

요구사항.
- features 는 5~7개. 페이지에 실제로 적힌 사양이나 기능에 근거해야 한다.
- personas 는 3개. "30대 직장인" 같은 뭉뚱그린 표현 대신 상황까지 묘사한다.
- scenarios 는 3~4개. 읽는 사람이 자기 상황을 대입할 수 있게 구체적으로 쓴다.
- concerns 는 3~4개. 가격, 학습 난이도, 대체재, AS 같은 현실적인 고민을 다룬다.
- cons 는 반드시 3개 이상 채운다. 페이지에 단점이 없으면 "이런 사람에겐 과할 수 있다"
  같은 조건부 한계를 쓴다.
- faqs 는 5개.

아래 JSON 형식으로만 출력한다.

{_SCHEMA}"""

    data = client.chat_json(BASE_RULES, user, temperature=0.6, max_tokens=10000, websearch=False)
    if not isinstance(data, dict):
        raise RuntimeError("상품 분석 응답이 JSON 객체가 아닙니다.")

    return ProductBrief(
        category=_s(data.get("category")),
        one_liner=_s(data.get("one_liner")),
        features=[
            Feature(_s(f.get("name")), _s(f.get("detail")), _s(f.get("benefit")))
            for f in _list(data, "features")
        ],
        personas=[
            Persona(_s(p.get("who")), _s(p.get("pain")), _s(p.get("why_fits")))
            for p in _list(data, "personas")
        ],
        scenarios=[
            Scenario(_s(s.get("when")), _s(s.get("situation")), _s(s.get("outcome")))
            for s in _list(data, "scenarios")
        ],
        concerns=[Concern(_s(c.get("worry")), _s(c.get("answer"))) for c in _list(data, "concerns")],
        pros=_strs(data.get("pros")),
        cons=_strs(data.get("cons")),
        faqs=[FAQ(_s(f.get("question")), _s(f.get("answer"))) for f in _list(data, "faqs")],
        recommended_for=_strs(data.get("recommended_for")),
        not_recommended_for=_strs(data.get("not_recommended_for")),
    )


def _s(value: object) -> str:
    return str(value or "").strip()


def _list(data: dict, key: str) -> list[dict]:
    return [item for item in (data.get(key) or []) if isinstance(item, dict)]


def _strs(value: object) -> list[str]:
    return [str(v).strip() for v in (value or []) if str(v).strip()]
