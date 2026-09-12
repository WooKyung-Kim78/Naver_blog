"""11단 구조로 블로그 원고를 집필한다.

역할 분담이 핵심이다. AI 는 산문만 쓰고, 구조와 서식과 이미지 슬롯 배치는 파이썬이
결정론적으로 조립한다. 서식까지 AI 에게 맡기면 실행할 때마다 결과가 달라진다.

구조: 제목 - 도입부 - 문제제기 - 상품소개 - 주요특징 - 실사용 시나리오 -
      장점 - 아쉬운 점 - FAQ - 총평 - CTA
"""

from __future__ import annotations

from ai.client import MyGenAssistClient
from ai.prompts import WRITER_RULES
from core.models import (
    KIND_CALLOUT,
    KIND_CHECKLIST,
    KIND_CTA,
    KIND_DIVIDER,
    KIND_FAQ,
    KIND_HEADING,
    KIND_IMAGE,
    KIND_PARAGRAPH,
    KIND_QUOTE,
    KIND_TABLE,
    SLOT_CTA,
    SLOT_FEATURE,
    SLOT_HERO,
    SLOT_INFOGRAPHIC,
    SLOT_LIFESTYLE,
    SLOT_PRODUCT,
    Article,
    Block,
    ProductBrief,
    Product,
    SeoPlan,
)

_FRONT_SCHEMA = """{
  "title": "최종 제목. 32자 이내, 메인 키워드 포함",
  "hook": "첫 두 줄. 스크롤을 멈추게 하는 문장",
  "intro": "도입부. 2문단. 왜 이 글을 쓰게 됐는지 개인적 맥락으로 시작",
  "problem": "문제제기. 독자가 겪는 불편을 구체적 상황으로 묘사. 2문단",
  "problem_quote": "문제를 한 문장으로 요약한 인용구",
  "product_intro": "상품 소개. 무엇이고 어떤 점이 다른지. 2문단",
  "feature_lead": "주요 특징 도입 한두 문장",
  "feature_rows": [{"item": "특징명", "detail": "설명", "point": "그래서 좋은 점"}],
  "feature_body": "표에서 특히 체감된 것 한두 가지를 풀어 쓴 문단"
}"""

_BACK_SCHEMA = """{
  "scenarios": [{"heading": "상황을 담은 소제목", "body": "그 상황의 실사용 묘사 문단"}],
  "pros_lead": "장점 문단 도입 한 문장",
  "pros_items": ["체크리스트로 들어갈 장점", "4~6개. 각 40자 내외"],
  "cons_lead": "아쉬운 점 도입 한 문장. 솔직하게 인정하는 톤",
  "cons_items": ["아쉬운 점", "3~4개"],
  "cons_balance": "그럼에도 감수할 만한 이유 한두 문장",
  "faq_lead": "FAQ 도입 한 문장",
  "verdict": "총평. 2문단. 누구에게 맞고 누구에겐 아닌지 분명히",
  "summary_box": ["3줄 요약", "각 줄 30자 내외", "3개"],
  "cta": "구매 페이지로 유도하는 문장. 강매하지 않고 담백하게",
  "tags": ["태그", "# 없이 8~12개"]
}"""


def write(
    client: MyGenAssistClient,
    product: Product,
    brief: ProductBrief,
    seo: SeoPlan,
    persona: str,
    disclosure: str,
    *,
    feedback: str = "",
) -> Article:
    """앞부분과 뒷부분을 나눠 두 번 호출한다.

    추론 모델은 토큰 한도를 추론에 먼저 쓰기 때문에, 한 번에 긴 글을 요구하면
    중간에 잘린다. 나눠 쓰면 각 호출이 짧아지고 집중도도 올라간다.
    """
    context = _context(product, brief, seo, persona, feedback)

    front = client.chat_json(
        WRITER_RULES,
        f"""{context}

지금은 글의 앞부분만 쓴다. 도입부부터 주요 특징까지다.

- hook 은 상품 자랑으로 시작하지 않는다. 독자의 상황이나 내 경험으로 연다.
- intro 에 메인 키워드 '{seo.main_keyword}' 를 한 번 자연스럽게 넣는다.
- 아래 키워드를 이 구간에 반드시 한 번씩 등장시킨다. 문장에 녹여 쓰고 나열하지 않는다.
  {', '.join(seo.sub_keywords[:3]) or '(없음)'}
- feature_rows 는 4~6개. brief 의 features 를 근거로 한다.
- 각 문단은 길이를 다르게 한다. 어떤 문단은 2문장, 어떤 문단은 6문장.

아래 JSON 형식으로만 출력한다.

{_FRONT_SCHEMA}""",
        temperature=0.9,
        max_tokens=10000,
        websearch=False,
    )

    back = client.chat_json(
        WRITER_RULES,
        f"""{context}

지금은 글의 뒷부분만 쓴다. 실사용 시나리오부터 CTA 까지다.

- scenarios 는 3개. brief 의 scenarios 를 근거로 실제 써 본 것처럼 묘사한다.
- cons_items 는 반드시 채운다. 단점 없는 리뷰는 광고로 읽혀 신뢰를 잃는다.
- verdict 에서 추천 대상과 비추천 대상을 모두 밝힌다.
- 아래 키워드를 이 구간에 반드시 한 번씩 등장시킨다. 문장에 녹여 쓰고 나열하지 않는다.
  {', '.join(seo.sub_keywords[3:]) or '(없음)'}
- 메인 키워드 '{seo.main_keyword}' 도 이 구간에 두 번쯤 자연스럽게 넣는다.

아래 JSON 형식으로만 출력한다.

{_BACK_SCHEMA}""",
        temperature=0.9,
        max_tokens=10000,
        websearch=False,
    )

    return _assemble(front, back, brief, seo, disclosure)


def _context(product: Product, brief: ProductBrief, seo: SeoPlan, persona: str, feedback: str) -> str:
    parts = [
        f"[상품] {product.title}",
        f"[가격] {product.price or '페이지 참고'}",
        f"[구매 링크] {product.url}",
        f"[카테고리] {brief.category}",
        f"[한 줄 요약] {brief.one_liner}",
        "",
        "[주요 특징]",
        *(f"  - {f.name}: {f.detail} -> {f.benefit}" for f in brief.features),
        "",
        "[타깃 독자]",
        *(f"  - {p.who} / 불편: {p.pain} / 적합 이유: {p.why_fits}" for p in brief.personas),
        "",
        "[사용 시나리오]",
        *(f"  - {s.when} / {s.situation} -> {s.outcome}" for s in brief.scenarios),
        "",
        "[구매 고민 포인트]",
        *(f"  - {c.worry} -> {c.answer}" for c in brief.concerns),
        "",
        f"[장점] {' / '.join(brief.pros)}",
        f"[단점] {' / '.join(brief.cons)}",
        f"[추천 대상] {' / '.join(brief.recommended_for)}",
        f"[비추천 대상] {' / '.join(brief.not_recommended_for)}",
        "",
        f"[메인 키워드] {seo.main_keyword}",
        f"[보조 키워드] {', '.join(seo.sub_keywords)}",
        f"[연관어] {', '.join(seo.lsi_keywords)}",
        f"[검색 의도] {seo.search_intent}",
        "",
        f"[글쓴이 페르소나] {persona}",
    ]
    if feedback:
        parts += ["", "[직전 원고의 문제점. 이번에는 반드시 고칠 것]", feedback]
    return "\n".join(parts)


def _assemble(front: dict, back: dict, brief: ProductBrief, seo: SeoPlan, disclosure: str) -> Article:
    h2 = _heading_picker(seo.h2s)
    blocks: list[Block] = []

    def add(kind: str, **kwargs) -> None:
        blocks.append(Block(kind=kind, **kwargs))

    # 대가성 문구는 법적 의무라 항상 맨 위 고정이다.
    add(KIND_CALLOUT, text=disclosure, style="info")

    # 도입부
    add(KIND_IMAGE, slot=SLOT_HERO)
    if _s(front.get("hook")):
        add(KIND_PARAGRAPH, text=_s(front["hook"]))
    _add_paragraphs(blocks, front.get("intro"))

    # 문제제기
    add(KIND_HEADING, text=h2("문제제기", "이런 불편, 겪어보셨나요"), level=2)
    _add_paragraphs(blocks, front.get("problem"))
    if _s(front.get("problem_quote")):
        add(KIND_QUOTE, text=_s(front["problem_quote"]))

    # 상품소개
    add(KIND_HEADING, text=h2("상품소개", "그래서 써보게 된 제품"), level=2)
    add(KIND_IMAGE, slot=SLOT_PRODUCT)
    _add_paragraphs(blocks, front.get("product_intro"))

    # 주요 특징
    add(KIND_HEADING, text=h2("주요특징", "핵심 기능 정리"), level=2)
    _add_paragraphs(blocks, front.get("feature_lead"))
    rows = [
        [_s(r.get("item")), _s(r.get("detail")), _s(r.get("point"))]
        for r in front.get("feature_rows") or []
        if isinstance(r, dict) and _s(r.get("item"))
    ]
    if rows:
        add(KIND_TABLE, headers=["항목", "내용", "포인트"], rows=rows)
    add(KIND_IMAGE, slot=SLOT_FEATURE)
    _add_paragraphs(blocks, front.get("feature_body"))

    # 실사용 시나리오
    add(KIND_HEADING, text=h2("실사용", "실제로 써보니"), level=2)
    add(KIND_IMAGE, slot=SLOT_LIFESTYLE)
    for scenario in back.get("scenarios") or []:
        if not isinstance(scenario, dict) or not _s(scenario.get("body")):
            continue
        if _s(scenario.get("heading")):
            add(KIND_HEADING, text=_s(scenario["heading"]), level=3)
        _add_paragraphs(blocks, scenario.get("body"))

    add(KIND_DIVIDER)

    # 장점
    add(KIND_HEADING, text=h2("장점", "좋았던 점"), level=2)
    _add_paragraphs(blocks, back.get("pros_lead"))
    pros = _strs(back.get("pros_items")) or brief.pros
    if pros:
        add(KIND_CHECKLIST, items=pros)

    # 아쉬운 점
    add(KIND_HEADING, text=h2("아쉬운", "아쉬운 점"), level=2)
    _add_paragraphs(blocks, back.get("cons_lead"))
    cons = _strs(back.get("cons_items")) or brief.cons
    if cons:
        add(KIND_CALLOUT, text="\n".join(f"· {c}" for c in cons), style="warn")
    _add_paragraphs(blocks, back.get("cons_balance"))

    # FAQ
    add(KIND_HEADING, text=h2("FAQ", "자주 묻는 질문"), level=2)
    add(KIND_IMAGE, slot=SLOT_INFOGRAPHIC)
    _add_paragraphs(blocks, back.get("faq_lead"))
    if brief.faqs:
        add(KIND_FAQ, qa=brief.faqs)

    # 총평
    add(KIND_HEADING, text=h2("총평", "총평"), level=2)
    _add_paragraphs(blocks, back.get("verdict"))
    summary = _strs(back.get("summary_box"))
    if summary:
        add(KIND_CALLOUT, text="\n".join(summary), style="summary")

    # CTA
    add(KIND_IMAGE, slot=SLOT_CTA)
    if _s(back.get("cta")):
        add(KIND_CTA, text=_s(back["cta"]))

    return Article(
        title=_s(front.get("title")) or seo.h1 or seo.main_keyword,
        blocks=blocks,
        tags=_strs(back.get("tags")),
        disclosure=disclosure,
    )


def _heading_picker(h2s: list[str]):
    """SEO 계획의 H2 중 해당 구간에 맞는 것을 찾고, 없으면 기본값을 쓴다."""
    remaining = list(h2s)

    def pick(hint: str, fallback: str) -> str:
        for candidate in remaining:
            if hint in candidate.replace(" ", ""):
                remaining.remove(candidate)
                return candidate
        return remaining.pop(0) if remaining else fallback

    return pick


def _add_paragraphs(blocks: list[Block], raw: object) -> None:
    for chunk in str(raw or "").split("\n"):
        text = chunk.strip()
        if text:
            blocks.append(Block(kind=KIND_PARAGRAPH, text=text))


def _s(value: object) -> str:
    return str(value or "").strip()


def _strs(value: object) -> list[str]:
    return [str(v).strip() for v in (value or []) if str(v).strip()]
