"""완성된 원고를 100점 만점으로 평가하고, 기준 미달이면 재생성 지시를 만든다.

SEO 항목만은 AI 판단 대신 seo.score() 의 결정론적 계산 결과를 환산해 쓴다.
나머지 네 항목은 사람이 읽는 느낌에 가까워 AI 평가가 더 적절하다.
"""

from __future__ import annotations

from ai.client import MyGenAssistClient
from ai.prompts import BASE_RULES
from core.models import Article, ProductBrief, QualityScore, SeoPlan, SeoScore

PASS_MARK = 80

_SCHEMA = """{
  "readability": 0,
  "information": 0,
  "humanness": 0,
  "conversion": 0,
  "feedback": ["점수를 깎은 구체적 이유와 고칠 방법", "3~5개"]
}"""


def evaluate(
    client: MyGenAssistClient,
    article: Article,
    brief: ProductBrief,
    seo: SeoPlan,
    seo_score: SeoScore,
) -> QualityScore:
    user = f"""아래 블로그 원고를 냉정하게 평가해라. 후하게 주지 마라.

[메인 키워드] {seo.main_keyword}
[검색 의도] {seo.search_intent}
[상품 카테고리] {brief.category}

[원고]
제목: {article.title}

{article.body_text()[:8000]}

각 항목을 20점 만점으로 채점한다.

- readability(가독성): 문단 길이, 소제목 흐름, 한눈에 들어오는 정도.
- information(정보밀도): 구체적 수치와 사례가 있는지. 뻔한 일반론은 감점.
- humanness(인간다움): AI 가 쓴 티가 나면 크게 감점. 실제 경험의 결이 느껴지는지.
- conversion(구매전환 가능성): 읽고 나서 사고 싶어지는지. 단점을 솔직히 밝혀 신뢰를
  얻었는지도 포함한다. 장점만 나열한 글은 오히려 감점.

feedback 은 "좋습니다" 같은 말 대신, 어느 부분을 어떻게 고치라고 구체적으로 쓴다.

아래 JSON 형식으로만 출력한다.

{_SCHEMA}"""

    data = client.chat_json(BASE_RULES, user, temperature=0.3, max_tokens=6000, websearch=False)
    if not isinstance(data, dict):
        data = {}

    return QualityScore(
        seo=round(seo_score.total * 0.2),
        readability=_clamp(data.get("readability")),
        information=_clamp(data.get("information")),
        humanness=_clamp(data.get("humanness")),
        conversion=_clamp(data.get("conversion")),
        feedback=[str(f).strip() for f in (data.get("feedback") or []) if str(f).strip()],
    )


def feedback_text(quality: QualityScore, seo_score: SeoScore) -> str:
    """재집필 프롬프트에 넣을 개선 지시문."""
    lines = [f"- 직전 원고 총점 {quality.total}/100 (기준 {PASS_MARK}점)"]
    lines += [f"- {name} {got}/{full}" for name, got, full in quality.as_rows() if got < full * 0.8]
    lines += [f"- {f}" for f in quality.feedback]
    lines += [f"- SEO: {n}" for n in seo_score.notes]
    return "\n".join(lines)


def _clamp(value: object) -> int:
    try:
        return max(0, min(20, int(float(value))))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
