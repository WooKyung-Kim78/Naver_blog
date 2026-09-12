"""11단 구조로 블로그 원고를 집필한다.

역할 분담이 핵심이다. AI 는 산문만 쓰고, 구조와 서식과 이미지 슬롯 배치는 파이썬이
결정론적으로 조립한다. 서식까지 AI 에게 맡기면 실행할 때마다 결과가 달라진다.

구조: 제목 - 도입부 - 문제제기 - 상품소개 - 주요특징 - 실사용 시나리오 -
      장점 - 아쉬운 점 - FAQ - 총평 - CTA
"""

from __future__ import annotations

import re

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
    KIND_LINK,
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
    FocusPoint,
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
  "mid_ctas": ["본문 중간에 넣을 링크 안내 문구", "2개. 각 30자 내외. 서로 다른 표현. URL 은 쓰지 마라"],
  "cta": "구매 페이지로 유도하는 마지막 문장. 강매하지 않고 담백하게. URL 은 쓰지 마라",
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
    focus: FocusPoint | None = None,
    feedback: str = "",
) -> Article:
    """앞부분과 뒷부분을 나눠 두 번 호출한다.

    추론 모델은 토큰 한도를 추론에 먼저 쓰기 때문에, 한 번에 긴 글을 요구하면
    중간에 잘린다. 나눠 쓰면 각 호출이 짧아지고 집중도도 올라간다.
    """
    context = _context(product, brief, seo, persona, focus, feedback)

    front = client.chat_json(
        WRITER_RULES,
        f"""{context}

지금은 글의 앞부분만 쓴다. 도입부부터 주요 특징까지다.

- hook 은 상품 자랑으로 시작하지 않는다. 독자의 상황이나 내 경험으로 연다.
- hook 또는 intro 의 첫 문단 안에 메인 키워드 '{seo.main_keyword}' 가
  이 글자 그대로 반드시 들어가야 한다. 검색 노출이 여기서 갈린다.
- 메인 키워드 '{seo.main_keyword}' 는 이 구간 전체에서 정확히 {_front_quota(seo)}번만 쓴다.
  같은 형태를 그대로 쓰되, 한 문단에 두 번 넣지 않는다. 더 많이 쓰면 검색엔진이
  남용으로 보고 순위를 내린다.
- 이 구간의 본문은 공백 제외 {int(TARGET_CHARS * 0.45):,}자 이상 써라. 짧으면 정보가 부실해진다.
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
- 메인 키워드 '{seo.main_keyword}' 는 이 구간에서 정확히 {_back_quota(seo)}번만 쓴다.
  한 문단에 두 번 넣지 않는다. 남용하면 오히려 순위가 내려간다.
- 이 구간의 본문은 공백 제외 {int(TARGET_CHARS * 0.55):,}자 이상 써라. 짧으면 정보가 부실해진다.

아래 JSON 형식으로만 출력한다.

{_BACK_SCHEMA}""",
        temperature=0.9,
        max_tokens=10000,
        websearch=False,
    )

    return _assemble(front, back, brief, seo, disclosure, product.url)


#: 메인 키워드가 노리는 밀도(%)와 완성 원고의 목표 길이. 이 둘로 반복 횟수를 역산한다.
#: "자연스럽게 넣어라" 같은 말은 모델이 무시하지만, 횟수를 주면 대체로 지킨다.
#: 다만 모델이 요구 횟수를 넘겨 쓰는 경향이 있어, 허용 구간(1.0~2.5%)의 아래쪽을 겨눈다.
TARGET_DENSITY = 1.15
TARGET_CHARS = 3200


def _main_quota(seo: SeoPlan) -> int:
    length = max(len(seo.main_keyword), 1)
    return max(3, min(round(TARGET_DENSITY * TARGET_CHARS / (100 * length)), 8))


def _front_quota(seo: SeoPlan) -> int:
    return max(2, round(_main_quota(seo) * 0.45))


def _back_quota(seo: SeoPlan) -> int:
    return max(1, _main_quota(seo) - _front_quota(seo))


def _context(
    product: Product,
    brief: ProductBrief,
    seo: SeoPlan,
    persona: str,
    focus: FocusPoint | None,
    feedback: str,
) -> str:
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
    if focus:
        parts += [
            "",
            "[이 글이 집중할 한 가지. 글 전체가 여기로 수렴해야 한다]",
            focus.as_prompt(),
            "장점을 골고루 나열하지 말고 위 포인트를 중심으로 끌고 간다.",
            "나머지 특징은 이 포인트를 뒷받침하는 근거로만 쓴다.",
        ]
    if feedback:
        parts += ["", "[직전 원고의 문제점. 이번에는 반드시 고칠 것]", feedback]
    return "\n".join(parts)


def _assemble(
    front: dict, back: dict, brief: ProductBrief, seo: SeoPlan, disclosure: str, product_url: str
) -> Article:
    h2 = _heading_picker(seo.h2s)
    blocks: list[Block] = []
    # 링크는 렌더러가 따로 붙이므로, 모델이 문장에 끼워 넣은 URL 은 걷어낸다.
    mid_ctas = [_no_url(t) for t in _strs(back.get("mid_ctas"))] or ["가격과 상세 사양 확인하기"]

    def add(kind: str, **kwargs) -> None:
        blocks.append(Block(kind=kind, **kwargs))

    def buy_link(index: int) -> None:
        """본문 중간 구매 링크. 독자가 읽다가 궁금해지는 지점마다 놓는다."""
        add(KIND_LINK, text=mid_ctas[index % len(mid_ctas)], href=product_url)

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
    buy_link(0)

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
    buy_link(1)

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

    # CTA. 마지막 구매 링크는 썸네일과 함께 보여준다. 이미지는 planner 가 채운다.
    add(
        KIND_CTA,
        text=_no_url(_s(back.get("cta"))) or "제품 상세 정보와 구성품은 판매 페이지에서 확인해 보세요.",
        href=product_url,
        slot=SLOT_CTA,
        style="final",
    )

    return Article(
        title=_s(front.get("title")) or seo.h1 or seo.main_keyword,
        blocks=blocks,
        # 네이버는 띄어쓰기를 태그 구분자로 읽어 "#골프 거리측정기" 를 두 태그로 쪼갠다.
        tags=list(dict.fromkeys(t.lstrip("#").replace(" ", "") for t in _strs(back.get("tags")))),
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


def _no_url(text: str) -> str:
    """문장에 섞여 들어온 URL 과 그 앞의 안내 꼬리('구매 페이지:')를 떼어낸다."""
    cleaned = re.sub(r"\s*\S*(?:https?://|www\.)\S+", "", text)
    cleaned = re.sub(r"[\s,]*(?:구매|판매|상품|제품)\s*(?:페이지|링크)\s*[:：]?\s*$", "", cleaned)
    return cleaned.strip(" ,·-—:：")


def _s(value: object) -> str:
    return str(value or "").strip()


def _strs(value: object) -> list[str]:
    return [str(v).strip() for v in (value or []) if str(v).strip()]
