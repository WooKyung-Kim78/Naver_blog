"""여러 상품을 한 글로 묶을 때 쓰는 분석·집필.

사용자가 조합을 고른다. 이쪽은 공통점과 같이 쓰는 이유를 찾고, 상품마다 소개와
구매 링크가 들어간 한 편의 글을 만든다. 한 상품 리뷰 구조를 억지로 늘리지 않는다.
"""

from __future__ import annotations

from pathlib import Path

from ai.client import MyGenAssistClient
from ai.prompts import BASE_RULES, WRITER_RULES
from ai.writer import _add_paragraphs, _heading_picker, _no_url, _s, _strs
from core.models import (
    KIND_CALLOUT,
    KIND_CHECKLIST,
    KIND_CTA,
    KIND_DIVIDER,
    KIND_FAQ,
    KIND_HEADING,
    KIND_IMAGE,
    KIND_LINK,
    KIND_PARAGRAPH,
    KIND_QUOTE,
    KIND_TABLE,
    SLOT_HERO,
    SLOT_INFOGRAPHIC,
    SLOT_LIFESTYLE,
    Article,
    Block,
    FAQ,
    FocusPoint,
    Product,
    ProductBrief,
    RoundupBrief,
    SeoPlan,
)

MAX_VISION_IMAGES = 4

_ANALYZE_SCHEMA = """{
  "theme": "이 조합을 한 줄로 부르는 이름. 상품명이 아니라 쓰임새",
  "one_liner": "왜 같이 소개하는지 한 문장",
  "commonalities": ["실제로 겹치는 점", "3~5개. 페이지에 근거가 있어야 한다"],
  "differences": ["서로 다른 점. 역할이 어떻게 나뉘는지", "2~4개"],
  "how_together": "실제로 같이 쓰는 순서나 장면. 2~4문장",
  "recommended_for": ["이 조합이 맞는 사람", "2~4개"],
  "not_recommended_for": ["이 조합이 과한 사람", "2개"],
  "faqs": [{"question": "조합을 살 때 나올 법한 질문", "answer": "2~3문장"}]
}"""

_THEME_SCHEMA = """{
  "focus_points": [
    {
      "title": "이 글이 밀 공통점. 상품 기능이나 쓰임새의 이름",
      "angle": "그 공통점으로 글을 어떻게 끌어갈지",
      "evidence": "각 상품 페이지에서 읽은 근거를 짧게",
      "target": "그 공통점이 꽂히는 독자",
      "why_now": "지금 이 조합이 먹히는 이유",
      "keywords": ["검색에 쓸 말", "2~4개"],
      "risk": "이 공통점만 밀면 놓치는 것"
    }
  ]
}"""

_FRONT_SCHEMA = """{
  "title": "최종 제목. 32자 이내, 메인 키워드 포함. 특정 모델명만으로 끝내지 마라",
  "hook": "첫 두 줄. 이 조합이 필요한 상황으로 연다",
  "intro": "도입부. 2문단. 왜 여러 개를 같이 보게 됐는지",
  "common_quote": "공통점을 한 문장으로 요약한 인용구",
  "common_body": "공통점을 풀어 쓴 문단",
  "together": "같이 쓰는 장면. 1~2문단"
}"""

_CARDS_SCHEMA = """{
  "cards": [
    {
      "heading": "이 상품 소제목. 상품의 역할이 드러나게",
      "intro": "이 상품만의 소개. 1문단",
      "rows": [{"item": "특징", "detail": "설명", "point": "이 조합 안에서의 역할"}],
      "link_text": "이 상품 구매 링크 안내. 30자 내외. URL 쓰지 마라"
    }
  ]
}"""

_BACK_SCHEMA = """{
  "compare_headers": ["구분", "상품1 짧은 이름", "상품2 짧은 이름"],
  "compare_rows": [["역할", "...", "..."], ["이런 때", "...", "..."]],
  "who_lead": "누구에게 맞는지 한 문장",
  "who_items": ["추천 대상", "3~4개"],
  "faq_lead": "FAQ 도입 한 문장",
  "verdict": "총평. 2문단. 세트로 살지, 골라 살지 분명히",
  "summary_box": ["3줄 요약", "각 줄 30자 내외", "3개"],
  "cta": "마지막 안내. URL 쓰지 마라",
  "tags": ["태그", "# 없이 8~12개"]
}"""


def analyze(
    client: MyGenAssistClient,
    products: list[Product],
    briefs: list[ProductBrief],
) -> RoundupBrief:
    catalog = _catalog(products, briefs)
    user = f"""아래 상품들을 한 편의 블로그로 묶어 소개하려 한다.
사용자가 이미 조합을 골랐다. 억지로 끼워 맞추지 말고, 페이지에 있는 공통점만 찾아라.

{catalog}

요구사항.
- theme 은 쓰임새다. "골프 입문 필수템", "가벼운 라운딩 준비물"처럼.
  상품명을 나열한 제목은 실패다.
- commonalities 는 모든 상품에 실제로 있는 점이어야 한다. 없으면 솔직히 적게 써라.
- how_together 는 같이 쓰는 순서나 장면을 구체적으로. "잘 어울린다"는 쓰지 마라.
- faqs 는 4개. 세트로 사야 하는지, 하나만 골라도 되는지 같은 질문을 넣어라.

아래 JSON 형식으로만 출력한다.

{_ANALYZE_SCHEMA}"""

    data = client.chat_json(BASE_RULES, user, temperature=0.6, max_tokens=8000, websearch=False)
    if not isinstance(data, dict):
        raise RuntimeError("조합 분석 응답이 JSON 객체가 아닙니다.")

    faqs = [
        FAQ(question=_s(f.get("question")), answer=_s(f.get("answer")))
        for f in (data.get("faqs") or [])
        if isinstance(f, dict) and _s(f.get("question"))
    ]
    return RoundupBrief(
        theme=_s(data.get("theme")),
        one_liner=_s(data.get("one_liner")),
        commonalities=_strs(data.get("commonalities")),
        differences=_strs(data.get("differences")),
        how_together=_s(data.get("how_together")),
        recommended_for=_strs(data.get("recommended_for")),
        not_recommended_for=_strs(data.get("not_recommended_for")),
        faqs=faqs,
    )


def propose_themes(
    client: MyGenAssistClient,
    products: list[Product],
    briefs: list[ProductBrief],
    combo: RoundupBrief,
    images: list[Path] | None = None,
    *,
    count: int = 3,
    avoid: list[str] | None = None,
    hint: str = "",
) -> list[FocusPoint]:
    """글 전체가 밀 공통점 후보를 뽑는다. 한 상품의 기능이 아니라 조합의 축이다."""
    retry = ""
    if avoid:
        retry += "\n[이미 거절된 안. 다시 내지 마라]\n" + "\n".join(f"- {t}" for t in avoid)
    if hint:
        retry += f"\n[사용자가 원하는 방향]\n{hint}\n"

    vision = "\n첨부 이미지는 각 상품 상세페이지다. 이미지 안의 문구까지 읽고 판단해라." if images else ""

    user = f"""아래 상품 조합으로 네이버 블로그를 쓰려 한다.
글 하나에 상품을 나열만 하면 쇼핑 목록이 된다. 이 조합이 공유하는 점 중
글이 집중해서 밀 축을 {count}가지 골라라.{vision}

{_catalog(products, briefs)}

[이미 정리된 테마] {combo.theme}
[한 줄] {combo.one_liner}
[공통점] {", ".join(combo.commonalities) or "(없음)"}
[같이 쓰는 법] {combo.how_together}
{retry}

요구사항.
- title 은 상품들이 실제로 공유하는 기능·쓰임새·상황이어야 한다.
  마케팅 문장("가성비를 어필한다")은 금지.
- 한 상품에만 있는 기능을 공통점인 양 쓰지 마라.
- {count}개는 축이 달라야 한다. 예: 쓰임새 / 휴대성 / 입문자 친화성.
- evidence 는 상품 페이지에 있는 내용을 가져와야 한다.

아래 JSON 형식으로만 출력한다.

{_THEME_SCHEMA}"""

    data = client.chat_json(
        BASE_RULES,
        user,
        temperature=0.85,
        max_tokens=8000,
        websearch=False,
        images=(images or [])[:MAX_VISION_IMAGES] or None,
    )
    raw = data.get("focus_points", data) if isinstance(data, dict) else data
    points = [
        FocusPoint(
            title=_s(p.get("title")),
            angle=_s(p.get("angle")),
            evidence=_s(p.get("evidence")),
            target=_s(p.get("target")),
            why_now=_s(p.get("why_now")),
            keywords=[str(k).strip() for k in (p.get("keywords") or []) if str(k).strip()],
            risk=_s(p.get("risk")),
        )
        for p in (raw or [])
        if isinstance(p, dict) and _s(p.get("title"))
    ]
    if not points:
        raise RuntimeError("AI 가 조합의 집중 포인트를 만들지 못했습니다.")
    return points


def write(
    client: MyGenAssistClient,
    products: list[Product],
    briefs: list[ProductBrief],
    combo: RoundupBrief,
    seo: SeoPlan,
    persona: str,
    disclosure: str,
    *,
    focus: FocusPoint | None = None,
) -> Article:
    context = _context(products, briefs, combo, seo, persona, focus)

    front = client.chat_json(
        WRITER_RULES,
        f"""{context}

지금은 글의 앞부분만 쓴다. 제목, 도입, 공통점, 같이 쓰는 장면이다.

- hook 또는 intro 첫 문단에 메인 키워드 '{seo.main_keyword}' 가 글자 그대로 들어가야 한다.
- 상품을 아직 하나씩 소개하지 마라. 왜 같이 보는지에 집중한다.
- 아래 키워드를 한 번씩 녹여 쓴다: {", ".join(seo.sub_keywords[:3]) or "(없음)"}

아래 JSON 형식으로만 출력한다.

{_FRONT_SCHEMA}""",
        temperature=0.9,
        max_tokens=8000,
        websearch=False,
    )

    names = "\n".join(
        f"{i+1}. {p.title or f'상품 {i+1}'} / {briefs[i].one_liner}"
        for i, p in enumerate(products)
    )
    cards = client.chat_json(
        WRITER_RULES,
        f"""{context}

지금은 상품별 소개 카드만 쓴다. 순서는 아래와 같고, 개수도 같아야 한다.

{names}

- cards 배열 길이는 반드시 {len(products)}개.
- 각 카드는 그 상품만 다룬다. 다른 상품 이야기를 섞지 마라.
- rows 는 3~4개. 이 조합 안에서의 역할이 보이게.
- link_text 에 URL 을 넣지 마라.

아래 JSON 형식으로만 출력한다.

{_CARDS_SCHEMA}""",
        temperature=0.85,
        max_tokens=10000,
        websearch=False,
    )

    short = ", ".join((p.title or f"상품{i+1}")[:18] for i, p in enumerate(products))
    back = client.chat_json(
        WRITER_RULES,
        f"""{context}

지금은 글의 뒷부분만 쓴다. 비교표, 추천 대상, FAQ, 총평이다.

- compare_headers 첫 칸은 '구분', 나머지는 상품 짧은 이름. 상품 수는 {len(products)}개.
  예: ["구분", {short}]
- compare_rows 는 4~6행. 역할 / 이런 때 / 아쉬운 점 을 넣는다.
- 메인 키워드 '{seo.main_keyword}' 를 두 번만 쓴다. 남용하지 마라.
- 아래 키워드를 한 번씩: {", ".join(seo.sub_keywords[3:]) or "(없음)"}

아래 JSON 형식으로만 출력한다.

{_BACK_SCHEMA}""",
        temperature=0.85,
        max_tokens=8000,
        websearch=False,
    )

    return _assemble(front, cards, back, products, briefs, combo, seo, disclosure)


def _assemble(
    front: dict,
    cards_data: dict,
    back: dict,
    products: list[Product],
    briefs: list[ProductBrief],
    combo: RoundupBrief,
    seo: SeoPlan,
    disclosure: str,
) -> Article:
    h2 = _heading_picker(seo.h2s)
    blocks: list[Block] = []

    def add(kind: str, **kwargs) -> None:
        blocks.append(Block(kind=kind, **kwargs))

    add(KIND_CALLOUT, text=disclosure, style="info")
    add(KIND_IMAGE, slot=SLOT_HERO)
    if _s(front.get("hook")):
        add(KIND_PARAGRAPH, text=_s(front["hook"]))
    _add_paragraphs(blocks, front.get("intro"))

    add(KIND_HEADING, text=h2("공통", "이 조합의 공통점"), level=2)
    if _s(front.get("common_quote")):
        add(KIND_QUOTE, text=_s(front["common_quote"]))
    _add_paragraphs(blocks, front.get("common_body"))
    if combo.commonalities:
        add(KIND_CHECKLIST, items=combo.commonalities)

    add(KIND_HEADING, text=h2("함께", "같이 쓰는 장면"), level=2)
    add(KIND_IMAGE, slot=SLOT_LIFESTYLE)
    _add_paragraphs(blocks, front.get("together") or combo.how_together)

    raw_cards = cards_data.get("cards", cards_data) if isinstance(cards_data, dict) else cards_data
    cards = [c for c in (raw_cards or []) if isinstance(c, dict)]

    add(KIND_HEADING, text=h2("소개", "하나씩 보면"), level=2)
    for i, product in enumerate(products):
        card = cards[i] if i < len(cards) else {}
        heading = _s(card.get("heading")) or (product.title or f"상품 {i+1}")
        add(KIND_HEADING, text=heading, level=3)
        add(KIND_IMAGE, slot=f"item{i}")
        _add_paragraphs(blocks, card.get("intro") or (briefs[i].one_liner if i < len(briefs) else ""))
        rows = [
            [_s(r.get("item")), _s(r.get("detail")), _s(r.get("point"))]
            for r in card.get("rows") or []
            if isinstance(r, dict) and _s(r.get("item"))
        ]
        if rows:
            add(KIND_TABLE, headers=["항목", "내용", "이 조합에서"], rows=rows)
        add(
            KIND_LINK,
            text=_no_url(_s(card.get("link_text"))) or "상세 정보와 구성 확인하기",
            href=product.url,
        )

    add(KIND_DIVIDER)
    add(KIND_HEADING, text=h2("비교", "한눈에 비교"), level=2)
    headers = _strs(back.get("compare_headers"))
    rows = [
        [str(c) for c in row]
        for row in (back.get("compare_rows") or [])
        if isinstance(row, list) and row
    ]
    if headers and rows:
        add(KIND_TABLE, headers=headers, rows=rows)
    elif combo.differences:
        add(KIND_CHECKLIST, items=combo.differences)

    add(KIND_HEADING, text=h2("대상", "이런 분께"), level=2)
    _add_paragraphs(blocks, back.get("who_lead"))
    who = _strs(back.get("who_items")) or combo.recommended_for
    if who:
        add(KIND_CHECKLIST, items=who)
    if combo.not_recommended_for:
        add(KIND_CALLOUT, text="\n".join(f"· {c}" for c in combo.not_recommended_for), style="warn")

    add(KIND_HEADING, text=h2("FAQ", "자주 묻는 질문"), level=2)
    add(KIND_IMAGE, slot=SLOT_INFOGRAPHIC)
    _add_paragraphs(blocks, back.get("faq_lead"))
    faqs = combo.faqs
    if faqs:
        add(KIND_FAQ, qa=faqs)

    add(KIND_HEADING, text=h2("총평", "총평"), level=2)
    _add_paragraphs(blocks, back.get("verdict"))
    summary = _strs(back.get("summary_box"))
    if summary:
        add(KIND_CALLOUT, text="\n".join(summary), style="summary")

    cta_text = _no_url(_s(back.get("cta"))) or "각 상품의 상세 정보와 구성은 판매 페이지에서 확인할 수 있다."
    add(KIND_PARAGRAPH, text=cta_text)
    for i, product in enumerate(products):
        add(
            KIND_CTA,
            text=product.title or f"상품 {i+1} 보러 가기",
            href=product.url,
            slot=f"cta{i}",
            style="final",
        )

    return Article(
        title=_s(front.get("title")) or seo.h1 or combo.theme or seo.main_keyword,
        blocks=blocks,
        tags=list(dict.fromkeys(t.lstrip("#").replace(" ", "") for t in _strs(back.get("tags")))),
        disclosure=disclosure,
    )


def _catalog(products: list[Product], briefs: list[ProductBrief]) -> str:
    parts = []
    for i, product in enumerate(products):
        brief = briefs[i] if i < len(briefs) else ProductBrief()
        feats = ", ".join(f.name for f in brief.features[:6]) or "(없음)"
        parts.append(
            f"[상품 {i+1}]\n"
            f"  이름: {product.title or '(없음)'}\n"
            f"  가격: {product.price or '(없음)'}\n"
            f"  한 줄: {brief.one_liner}\n"
            f"  카테고리: {brief.category}\n"
            f"  특징: {feats}\n"
            f"  장점: {' / '.join(brief.pros[:4])}\n"
            f"  단점: {' / '.join(brief.cons[:3])}\n"
            f"{product.as_prompt_context(limit=1200)}"
        )
    return "\n\n".join(parts)


def _context(
    products: list[Product],
    briefs: list[ProductBrief],
    combo: RoundupBrief,
    seo: SeoPlan,
    persona: str,
    focus: FocusPoint | None,
) -> str:
    names = " / ".join(p.title or f"상품{i+1}" for i, p in enumerate(products))
    parts = [
        f"[묶어 소개할 상품] {names}",
        f"[조합 테마] {combo.theme}",
        f"[한 줄] {combo.one_liner}",
        f"[공통점] {' / '.join(combo.commonalities)}",
        f"[다른 점] {' / '.join(combo.differences)}",
        f"[같이 쓰는 법] {combo.how_together}",
        f"[추천] {' / '.join(combo.recommended_for)}",
        f"[비추천] {' / '.join(combo.not_recommended_for)}",
        "",
        f"[메인 키워드] {seo.main_keyword}",
        f"[보조 키워드] {', '.join(seo.sub_keywords)}",
        f"[검색 의도] {seo.search_intent}",
        f"[글쓴이 페르소나] {persona}",
    ]
    if focus:
        parts += ["", "[이 글이 집중할 공통점]", focus.as_prompt()]
    return "\n".join(parts)
