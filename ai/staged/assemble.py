"""9단계. 섹션 본문을 기존 Article/Block 구조로 조립한다.

이미지 슬롯 이름(hero, product, feature, lifestyle, infographic, cta)과 구매 링크 위치,
총평 박스(summary callout)는 예전 방식과 같게 둔다. 이미지 배치·인포그래픽·렌더러·발행은
이 구조만 보고 동작하므로 손대지 않아도 된다.
"""

from __future__ import annotations

from core.models import (
    KIND_CALLOUT,
    KIND_CHECKLIST,
    KIND_CTA,
    KIND_DIVIDER,
    KIND_HEADING,
    KIND_IMAGE,
    KIND_LINK,
    KIND_PARAGRAPH,
    KIND_TABLE,
    SLOT_CTA,
    SLOT_FEATURE,
    SLOT_HERO,
    SLOT_INFOGRAPHIC,
    SLOT_LIFESTYLE,
    SLOT_PRODUCT,
    Article,
    Block,
    Outline,
    SectionDraft,
)

TABLE_HEADERS = ["특징", "내용", "이런 점이 좋아요"]
DEFAULT_LINK_TEXT = ["가격과 상세 사양 확인하기", "구성과 옵션 살펴보기"]
DEFAULT_CTA = "제품 상세 정보와 구성품은 판매 페이지에서 확인해 보세요."
NOT_FOR_LEAD = "이런 분께는 맞지 않을 수 있어요."


def assemble(
    sections: dict[str, SectionDraft],
    outline: Outline,
    title: str,
    *,
    disclosure: str,
    product_url: str,
) -> Article:
    blocks: list[Block] = []

    def add(kind: str, **kwargs) -> None:
        blocks.append(Block(kind=kind, **kwargs))

    def section(sid: str) -> SectionDraft:
        return sections.get(sid) or SectionDraft(id=sid)

    def heading(draft: SectionDraft) -> None:
        if draft.heading:
            add(KIND_HEADING, text=draft.heading, level=2)

    def paragraphs(values: list[str]) -> None:
        for text in values:
            add(KIND_PARAGRAPH, text=text)

    def bullets(items: list[str], style: str, lead: str = "") -> None:
        if items:
            add(KIND_CALLOUT, text="\n".join([lead, *(f"· {i}" for i in items)]).strip(), style=style)

    cta = section("cta")
    links = (cta.items + DEFAULT_LINK_TEXT)[:2]

    def buy_link(index: int) -> None:
        add(KIND_LINK, text=links[index], href=product_url)

    # 대가성 문구는 법적 의무라 항상 맨 위 고정이다.
    add(KIND_CALLOUT, text=disclosure, style="info")

    intro = section("intro")
    add(KIND_IMAGE, slot=SLOT_HERO)
    paragraphs(intro.paragraphs)

    need = section("need")
    heading(need)
    paragraphs(need.paragraphs)
    add(KIND_IMAGE, slot=SLOT_PRODUCT)

    features = section("features")
    heading(features)
    paragraphs(features.paragraphs[:1])
    if features.rows:
        add(KIND_TABLE, headers=list(TABLE_HEADERS), rows=[list(r) for r in features.rows])
    add(KIND_IMAGE, slot=SLOT_FEATURE)
    paragraphs(features.paragraphs[1:])
    buy_link(0)

    scenes = section("scenes")
    heading(scenes)
    add(KIND_IMAGE, slot=SLOT_LIFESTYLE)
    paragraphs(scenes.paragraphs)
    for scene in scenes.scenes:
        if len(scene) > 1 and scene[1]:
            if scene[0]:
                add(KIND_HEADING, text=scene[0], level=3)
            paragraphs([p for p in scene[1].split("\n") if p.strip()])

    add(KIND_DIVIDER)

    tips = section("tips")
    heading(tips)
    paragraphs(tips.paragraphs)
    bullets(tips.items, "tip")

    audience = section("audience")
    heading(audience)
    paragraphs(audience.paragraphs)
    if audience.items:
        add(KIND_CHECKLIST, items=list(audience.items))
    bullets(audience.extra_items, "warn", NOT_FOR_LEAD)
    buy_link(1)

    checks = section("checks")
    heading(checks)
    paragraphs(checks.paragraphs)
    bullets(checks.items, "warn")

    summary = section("summary")
    heading(summary)
    paragraphs(summary.paragraphs)
    add(KIND_IMAGE, slot=SLOT_INFOGRAPHIC)
    # 인포그래픽이 이 총평을 읽어 간다. 그림이 들어가면 pipeline 이 이 박스를 지운다.
    if summary.closing:
        add(KIND_CALLOUT, text=summary.closing, style="summary")

    heading(cta)
    paragraphs(cta.paragraphs)
    add(KIND_CTA, text=cta.closing or DEFAULT_CTA, href=product_url, slot=SLOT_CTA, style="final")

    return Article(title=title, blocks=blocks, tags=list(outline.hashtags), disclosure=disclosure)
