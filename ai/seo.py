"""키워드 전략 수립과 SEO 점수 계산.

키워드 선정은 AI(웹 검색 포함)가 하고, 완성된 글의 채점은 파이썬이 결정론적으로 한다.
AI 에게 "SEO 점수 몇 점이야?"라고 묻는 방식은 재현되지 않아 쓰지 않는다.
"""

from __future__ import annotations

import re

from ai.client import MyGenAssistClient
from ai.prompts import BASE_RULES
from core.models import (
    Article,
    FocusPoint,
    ProductBrief,
    Product,
    SeoPlan,
    SeoScore,
    KIND_HEADING,
)

#: 메인 키워드의 이상적인 밀도 구간(%). 이보다 낮으면 약하고, 높으면 남용으로 감점된다.
DENSITY_MIN = 1.0
DENSITY_MAX = 2.5

_SCHEMA = """{
  "main_keyword": "가장 중요한 검색 키워드 하나",
  "sub_keywords": ["보조 키워드", "4~6개"],
  "lsi_keywords": ["연관어. 본문에 자연스럽게 섞을 단어", "6~10개"],
  "search_intent": "이 키워드를 검색하는 사람이 진짜 알고 싶은 것",
  "search_type": "정보형 / 구매형 / 비교형 / 브랜드형 중 하나",
  "competition": "낮음 / 중간 / 높음 중 하나",
  "h1": "H1 제목",
  "h2s": ["H2 소제목", "6~9개"],
  "h3s": {"H2 소제목": ["그 아래 H3", "0~3개"]},
  "title_candidates": ["클릭을 부르는 제목안", "3개. 각 32자 이내"],
  "meta_description": "검색 결과에 노출될 요약. 80~120자"
}"""


def plan(
    client: MyGenAssistClient,
    product: Product,
    brief: ProductBrief,
    *,
    focus: FocusPoint | None = None,
    extra_titles: list[str] | None = None,
    theme: str = "",
) -> SeoPlan:
    focus_block = ""
    if focus:
        hint = ", ".join(focus.keywords) or "(없음)"
        focus_block = (
            f"\n{focus.as_prompt()}\n"
            f"[이 포인트에서 나온 키워드 후보] {hint}\n"
            "소제목과 보조 키워드는 위 집중 포인트를 살리는 방향으로 잡는다.\n"
            "단, 집중 포인트를 메인 키워드에 이어 붙이지 마라. "
            "'골프 거리측정기 슬로프 보정 추천' 같은 조합은 아무도 검색하지 않는다.\n"
        )

    if extra_titles:
        names = " / ".join([product.title, *extra_titles])
        opener = (
            f"아래 상품들을 한 편의 추천 글로 묶어 쓰려 한다.\n"
            f"웹 검색으로 이 쓰임새에서 한국 사람들이 실제로 검색하는 표현을 조사한 뒤 "
            f"키워드 전략을 세워라.\n\n"
            f"[묶어 소개할 상품] {names}\n"
            f"[조합 테마] {theme or brief.category}\n"
        )
        structure = (
            "공통점 / 같이 쓰는 장면 / 상품소개 / 비교 / 추천대상 / FAQ / 총평"
        )
    else:
        opener = (
            "아래 상품으로 네이버 블로그 리뷰를 쓰려 한다.\n"
            "웹 검색으로 이 카테고리에서 한국 사람들이 실제로 검색하는 표현을 조사한 뒤 "
            "키워드 전략을 세워라.\n\n"
            f"[상품] {product.title}\n"
        )
        structure = "문제제기 / 상품소개 / 주요특징 / 실사용 / 장점 / 아쉬운점 / FAQ / 총평"

    user = f"""{opener}[카테고리] {brief.category}
[한 줄 요약] {brief.one_liner}
[주요 특징] {', '.join(f.name for f in brief.features)}
[타깃] {', '.join(p.who for p in brief.personas)}
{focus_block}
요구사항.
- main_keyword 는 검색량이 있으면서 개인 블로그가 노려볼 만한 것으로 고른다.
  너무 광범위한 단어(예: "골프")나 아무도 안 치는 긴 문장은 피한다.
- main_keyword 를 모델명만으로 잡지 마라. 리뷰 글은 제품명을 어차피 수십 번 쓰게 되어
  밀도가 터진다. 모델명은 sub_keywords 에 넣고, main_keyword 는 "카테고리 + 의도"
  형태로 잡아라. 예: 모델명 "파인캐디 UPL2000" -> 메인 "골프 거리측정기 추천".
  여러 상품을 묶을 때는 쓰임새가 메인이다. 예: "골프 입문 용품 추천".
- sub_keywords 는 반드시 '문장 안에 그대로 써도 어색하지 않은' 표현이어야 한다.
  메인 키워드 앞에 단어만 갖다 붙인 조합은 금지다.
  나쁜 예: "골프 거리측정기 슬로프 보정", "골프 거리측정기 에이밍 기능"
  좋은 예: "슬로프 보정", "에이밍 기능", "파인캐디 UPL2000", "골프 거리측정기 추천"
  즉 기능·모델명·상황처럼 그 자체로 하나의 말이 되는 덩어리로 뽑는다.
- h2s 는 아래 글 구조 순서에 맞춰 만든다.
  {structure}
- h2s 중 최소 절반에는 main_keyword 또는 sub_keywords 중 하나가 글자 그대로
  들어가야 한다. 단, 소제목이 어색해질 정도로 밀어 넣지는 않는다.

아래 JSON 형식으로만 출력한다.

{_SCHEMA}"""

    data = client.chat_json(BASE_RULES, user, temperature=0.6, max_tokens=8000)
    if not isinstance(data, dict):
        raise RuntimeError("SEO 전략 응답이 JSON 객체가 아닙니다.")

    h3s = data.get("h3s") or {}
    return SeoPlan(
        main_keyword=str(data.get("main_keyword") or product.title).strip(),
        sub_keywords=_strs(data.get("sub_keywords")),
        lsi_keywords=_strs(data.get("lsi_keywords")),
        search_intent=str(data.get("search_intent") or "").strip(),
        search_type=str(data.get("search_type") or "").strip(),
        competition=str(data.get("competition") or "").strip(),
        h1=str(data.get("h1") or "").strip(),
        h2s=_strs(data.get("h2s")),
        h3s={str(k): _strs(v) for k, v in h3s.items()} if isinstance(h3s, dict) else {},
        title_candidates=_strs(data.get("title_candidates")),
        meta_description=str(data.get("meta_description") or "").strip(),
    )


def score(article: Article, seo: SeoPlan) -> SeoScore:
    """완성된 글을 100점 만점으로 채점한다."""
    body = article.body_text()
    chars = len(re.sub(r"\s", "", body))
    result = SeoScore(char_count=chars)

    if not chars or not seo.main_keyword:
        result.notes.append("본문이나 메인 키워드가 비어 있어 채점할 수 없습니다.")
        return result

    main = seo.main_keyword
    result.main_count = body.count(main)
    result.density = {
        kw: round(density(body, kw), 2) for kw in [main, *seo.sub_keywords[:5]] if kw
    }

    total = 0

    # 제목에 메인 키워드 (15점)
    result.title_has_main = main in article.title or _loose_match(article.title, main)
    if result.title_has_main:
        total += 15
    else:
        result.notes.append(f"제목에 메인 키워드 '{main}' 가 없습니다.")

    # 도입부 300자 안에 메인 키워드 (10점)
    result.intro_has_main = main in body[:300] or _loose_match(body[:300], main)
    if result.intro_has_main:
        total += 10
    else:
        result.notes.append("도입부 300자 안에 메인 키워드가 없습니다.")

    # 메인 키워드 밀도 (25점)
    main_density = result.density.get(main, 0.0)
    if DENSITY_MIN <= main_density <= DENSITY_MAX:
        total += 25
    elif main_density < DENSITY_MIN:
        total += int(25 * main_density / DENSITY_MIN)
        result.notes.append(f"메인 키워드 밀도 {main_density}% 로 낮습니다. {DENSITY_MIN}% 이상 권장.")
    else:
        over = min(main_density - DENSITY_MAX, DENSITY_MAX)
        total += int(25 * (1 - over / DENSITY_MAX))
        result.notes.append(f"메인 키워드 밀도 {main_density}% 로 과합니다. 남용으로 보일 수 있습니다.")

    # 소제목 키워드 커버리지 (15점)
    headings = [b.text for b in article.blocks if b.kind == KIND_HEADING]
    if headings:
        hit = sum(1 for h in headings if any(k and k in h for k in [main, *seo.sub_keywords]))
        result.heading_coverage = round(hit / len(headings), 2)
        total += int(15 * min(result.heading_coverage / 0.5, 1.0))
        if result.heading_coverage < 0.25:
            result.notes.append("소제목에 키워드가 거의 없습니다.")
    else:
        result.notes.append("소제목이 없습니다.")

    # 보조 키워드 등장 (15점)
    if seo.sub_keywords:
        used = sum(1 for k in seo.sub_keywords if k and k in body)
        total += int(15 * used / len(seo.sub_keywords))
        missing = [k for k in seo.sub_keywords if k and k not in body]
        if missing:
            result.notes.append(f"본문에 없는 보조 키워드: {', '.join(missing[:4])}")

    # 분량 (20점). 네이버는 1500자 이상에서 체류시간이 유의미하게 늘어난다.
    if chars >= 1500:
        total += 20
    else:
        total += int(20 * chars / 1500)
        result.notes.append(f"본문 {chars}자. 1500자 이상을 권장합니다.")

    result.total = min(total, 100)
    return result


def density(text: str, keyword: str) -> float:
    """한국어는 띄어쓰기 단위가 불규칙해 글자 수 기준으로 계산한다."""
    chars = len(re.sub(r"\s", "", text))
    if not chars or not keyword:
        return 0.0
    return text.count(keyword) * len(keyword) / chars * 100


def _loose_match(text: str, keyword: str) -> bool:
    """'파인캐디 UPL2000' 처럼 띄어쓰기가 다른 경우도 포함으로 본다."""
    squeeze = lambda s: re.sub(r"\s", "", s)
    return squeeze(keyword) in squeeze(text)


def _strs(value: object) -> list[str]:
    return [str(v).strip() for v in (value or []) if str(v).strip()]
