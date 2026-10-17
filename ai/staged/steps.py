"""단계별 집필의 각 단계. 입력을 받아 결과 dataclass 를 돌려줄 뿐, 저장과 재실행은 runner 가 맡는다."""

from __future__ import annotations

import re
from pathlib import Path

from ai import humanizer
from ai.staged import checks
from ai.staged import prompts as P
from ai.staged.schema import StageError, ask_json
from ai.staged.units import apply_changes, iter_units, numbered
from ai.writer import _main_quota, _no_url
from core.checkpoint import from_data
from core.models import (
    Fact,
    FocusPoint,
    MessagePlan,
    Outline,
    Product,
    ProductBrief,
    ProductFacts,
    ReviewIssue,
    SectionDraft,
    SectionPlan,
    SeoPlan,
    TitleOption,
)

#: 원문 인용이 이 비율 이상 원문 낱말과 겹쳐야 확인된 사실로 본다.
QUOTE_MATCH = 0.7
MAX_SPEC_FACTS = 20
MAX_HASHTAGS = 12


def source_text(product: Product, seo: SeoPlan | None = None) -> str:
    """검수 기준이 되는 원문. 검색어는 상품에서 나온 말이라 함께 허용한다."""
    parts = [product.as_prompt_context(limit=max(len(product.body_text), 1))]
    if seo:
        parts += [seo.main_keyword, *seo.sub_keywords]
    return "\n".join(p for p in parts if p)


# ------------------------------------------------------------- 1. 사실 정리


def extract_facts(client, product: Product, images: list[Path] | None = None) -> ProductFacts:
    images = [p for p in images or [] if p]
    note = (
        f"상세페이지 이미지 {len(images)}장을 함께 보낸다. 이미지 속 글자도 상품 정보다."
        if images
        else "이미지는 없다. 아래 글만 근거로 삼는다."
    )
    data = ask_json(
        client, "facts", P.SYSTEM,
        P.FACTS.substitute(image_note=note, product=product.as_prompt_context(limit=8000)),
        required=("facts",), temperature=0.2, max_tokens=8000, images=images or None,
    )

    source = source_text(product)
    facts: list[Fact] = []
    dropped: list[str] = []
    for raw in from_data(list[Fact], data.get("facts")):
        if not raw.text:
            continue
        if raw.source == "image" and images:
            facts.append(Fact(text=raw.text, quote=raw.quote, source="image"))
        elif _supported(raw.quote or raw.text, source):
            facts.append(Fact(text=raw.text, quote=raw.quote, source="text"))
        else:
            dropped.append(raw.text)

    seen = {_squeeze(f.text) for f in facts}
    for key, value in list(product.specs.items())[:MAX_SPEC_FACTS]:
        text = f"{key}: {value}"
        if _squeeze(text) not in seen:
            facts.append(Fact(text=text, quote=text, source="spec"))
    if product.price:
        facts.append(Fact(text=f"판매 페이지 가격 {product.price}", quote=product.price, source="price"))

    if not facts:
        raise StageError("facts", "상품 정보에서 확인된 사실을 하나도 찾지 못했습니다. --desc 로 설명을 넣어 보세요.")
    for i, fact in enumerate(facts, 1):
        fact.id = f"F{i}"

    return ProductFacts(
        name=_s(data.get("name")) or product.title,
        category=_s(data.get("category")),
        summary=_s(data.get("summary")),
        facts=facts,
        unknowns=from_data(list[str], data.get("unknowns")),
        cautions=from_data(list[str], data.get("cautions")),
        dropped=dropped,
    )


def _supported(quote: str, source: str) -> bool:
    squeezed = _squeeze(source)
    if len(_squeeze(quote)) >= 4 and _squeeze(quote) in squeezed:
        return True
    words = re.findall(r"[가-힣A-Za-z0-9]{2,}", quote)
    if not words:
        return False
    hit = sum(1 for w in words if w.lower() in squeezed)
    return hit / len(words) >= QUOTE_MATCH


# ------------------------------------------------------------- 3. 핵심 메시지


def plan_message(
    client, facts: ProductFacts, brief: ProductBrief, focus: FocusPoint | None, seo: SeoPlan
) -> MessagePlan:
    audience = "\n".join([
        "[분석된 고객]",
        *(f"  - {p.who} / 불편: {p.pain} / 맞는 이유: {p.why_fits}" for p in brief.personas),
        "[구매 고민]",
        *(f"  - {c.worry} -> {c.answer}" for c in brief.concerns),
    ])
    focus_text = ""
    if focus:
        focus_text = f"\n[사용자가 고른 집중 포인트. 메시지는 이 점으로 수렴해야 한다]\n{focus.as_prompt()}"

    data = ask_json(
        client, "message", P.SYSTEM,
        P.MESSAGE.substitute(
            facts=facts.as_prompt(), audience=audience,
            main_keyword=seo.main_keyword, sub_keywords=", ".join(seo.sub_keywords) or "(없음)",
            search_intent=seo.search_intent or "(없음)", search_type=seo.search_type or "미정",
            focus=focus_text,
        ),
        required=("message", "customer"), temperature=0.4, max_tokens=4000,
    )
    plan = from_data(MessagePlan, data)
    known = {f.id for f in facts.facts}
    plan.fact_ids = [i for i in plan.fact_ids if i in known]
    return plan


# ------------------------------------------------------------- 4. 개요


def plan_outline(client, facts: ProductFacts, message: MessagePlan, seo: SeoPlan) -> Outline:
    catalog = "\n".join(
        f"- {sid}: {P.SECTION_SPECS[sid]['name']}. {P.SECTION_SPECS[sid]['goal']}" for sid in P.SECTION_ORDER
    )
    data = ask_json(
        client, "outline", P.SYSTEM,
        P.OUTLINE.substitute(
            customer=message.customer, message=message.message, reasons=" / ".join(message.reasons),
            facts=facts.as_prompt(), main_keyword=seo.main_keyword,
            sub_keywords=", ".join(seo.sub_keywords) or "(없음)",
            lsi_keywords=", ".join(seo.lsi_keywords) or "(없음)",
            h2s=" / ".join(seo.h2s) or "(없음)", catalog=catalog,
        ),
        required=("sections",), temperature=0.5, max_tokens=6000,
    )

    given = {p.id.strip(): p for p in from_data(list[SectionPlan], data.get("sections")) if p.id.strip()}
    known = {f.id for f in facts.facts}
    sections = []
    for sid in P.SECTION_ORDER:
        spec = P.SECTION_SPECS[sid]
        plan = given.get(sid) or SectionPlan(id=sid)
        plan.id = sid
        plan.heading = "" if sid == "intro" else (plan.heading.strip() or spec["heading"])
        plan.goal = plan.goal.strip() or spec["goal"]
        plan.fact_ids = [i for i in plan.fact_ids if i in known]
        plan.keywords = []
        sections.append(plan)
    outline = Outline(
        sections=sections,
        meta_description=_s(data.get("meta_description")) or message.message[:120],
        hashtags=_hashtags(from_data(list[str], data.get("hashtags")), seo),
    )
    _allot_keywords(outline, seo)
    return outline


#: 메인 키워드를 한 번씩 나눠 받을 섹션 순서. 총평은 요약 그림 속으로 들어가 본문에서 빠진다.
_MAIN_ORDER = ("intro", "need", "features", "scenes", "tips", "audience", "checks", "cta")
_SUB_ORDER = ("need", "features", "scenes", "tips", "audience", "checks")


def _allot_keywords(outline: Outline, seo: SeoPlan) -> None:
    """검색어를 섹션마다 한 번씩 나눠 맡긴다. 한곳에 몰리지 않고 반복되지도 않는다."""
    if seo.main_keyword:
        for sid in _MAIN_ORDER[: _main_quota(seo)]:
            outline.get(sid).keywords.append(seo.main_keyword)
    for i, keyword in enumerate(k for k in seo.sub_keywords if k):
        outline.get(_SUB_ORDER[i % len(_SUB_ORDER)]).keywords.append(keyword)


def _hashtags(values: list[str], seo: SeoPlan) -> list[str]:
    # 네이버는 띄어쓰기를 태그 구분자로 읽는다.
    cleaned = [v.lstrip("#").replace(" ", "") for v in values or [seo.main_keyword, *seo.sub_keywords]]
    return list(dict.fromkeys(t for t in cleaned if t))[:MAX_HASHTAGS]


# ------------------------------------------------------------- 5. 섹션 집필


def write_sections(
    client,
    ids: list[str],
    *,
    product: Product,
    facts: ProductFacts,
    message: MessagePlan,
    outline: Outline,
    persona: str,
) -> dict[str, SectionDraft]:
    result = _write_batch(client, ids, product, facts, message, outline, persona)
    missing = [sid for sid in ids if sid not in result]
    if missing:
        result.update(_write_batch(client, missing, product, facts, message, outline, persona))
    missing = [sid for sid in ids if sid not in result]
    if missing:
        raise StageError("sections", f"다음 섹션을 쓰지 못했습니다: {', '.join(missing)}")
    return result


def _write_batch(client, ids, product, facts, message, outline, persona) -> dict[str, SectionDraft]:
    overview = "\n".join(
        f"- {p.id} {p.heading or '(소제목 없음)'}: {p.goal}" for p in outline.sections
    )
    targets = []
    for sid in ids:
        spec, plan = P.SECTION_SPECS[sid], outline.get(sid)
        targets.append("\n".join([
            f"### {sid} ({spec['name']})",
            f"[소제목] {plan.heading or '(없음)'}",
            f"[목표] {plan.goal}",
            f"[다룰 내용] {' / '.join(plan.points) or '(자유)'}",
            f"[근거 사실] {', '.join(plan.fact_ids) or '(전체 사실 참고)'}",
            f"[검색어] {', '.join(plan.keywords) or '(없음)'}",
            f"[작성 요령] {spec['guide']}",
        ]))
    schemas = ", ".join(f'"{sid}": {P.SECTION_SPECS[sid]["schema"]}' for sid in ids)

    data = ask_json(
        client, "sections", P.SYSTEM,
        P.SECTIONS.substitute(
            persona=persona, customer=message.customer, message=message.message,
            product_name=facts.name or product.title, price=product.price or "판매 페이지 참고",
            facts=facts.as_prompt(), outline=overview, targets="\n\n".join(targets), schemas=schemas,
        ),
        required=("sections",), temperature=0.8, max_tokens=min(4000 * len(ids), 14000),
    )
    raw_sections = data.get("sections") if isinstance(data.get("sections"), dict) else {}

    result = {}
    for sid in ids:
        raw = raw_sections.get(sid)
        if not isinstance(raw, dict):
            continue
        draft = _parse_section(sid, raw, outline.get(sid))
        if _has_body(draft):
            result[sid] = draft
    return result


def _parse_section(sid: str, raw: dict, plan: SectionPlan) -> SectionDraft:
    rows = []
    for row in raw.get("rows") or []:
        if isinstance(row, dict):
            cells = [_s(row.get("feature")), _s(row.get("detail")), _s(row.get("benefit"))]
        elif isinstance(row, list):
            cells = [_s(c) for c in row[:3]] + [""] * (3 - len(row[:3]))
        else:
            continue
        if cells[0]:
            rows.append(cells)
    scenes = []
    for scene in raw.get("scenes") or []:
        if isinstance(scene, dict) and _s(scene.get("body")):
            scenes.append([_s(scene.get("heading")), _s(scene.get("body"))])

    draft = SectionDraft(
        id=sid,
        heading="" if sid == "intro" else (_s(raw.get("heading")) or plan.heading),
        paragraphs=_paragraphs(raw.get("paragraphs")),
        items=[i.strip() for i in from_data(list[str], raw.get("items"))],
        extra_items=[i.strip() for i in from_data(list[str], raw.get("extra_items"))],
        rows=rows,
        scenes=scenes,
        closing=_s(raw.get("closing")),
    )
    if sid == "cta":
        draft.closing = _no_url(draft.closing)
        draft.items = [t for t in (_no_url(i) for i in draft.items) if t]
    return draft


def _paragraphs(raw: object) -> list[str]:
    values = from_data(list[str], raw)
    return [part.strip() for value in values for part in value.split("\n") if part.strip()]


_REQUIRED_BODY = {
    "intro": "paragraphs", "need": "paragraphs", "features": "rows", "scenes": "scenes",
    "tips": "items", "audience": "items", "checks": "items", "summary": "closing",
}


def _has_body(draft: SectionDraft) -> bool:
    if draft.id == "cta":
        return bool(draft.closing or draft.paragraphs)
    return bool(getattr(draft, _REQUIRED_BODY.get(draft.id, "paragraphs")))


# ------------------------------------------------------------- 6. 흐름·문체 통일


def unify(
    client,
    sections: dict[str, SectionDraft],
    *,
    message: MessagePlan,
    seo: SeoPlan,
    persona: str,
    only: set[str] | None = None,
) -> dict[str, SectionDraft]:
    prose = "\n".join(text for _, text in iter_units(sections, prose_only=True))
    diagnosis = humanizer.measure(prose).as_instructions() or "- 특이 사항 없음"
    data = ask_json(
        client, "unify", P.SYSTEM,
        P.UNIFY.substitute(
            persona=persona, message=message.message, keep=_keep_line(seo),
            diagnosis=diagnosis, units=numbered(sections, prose_only=True),
        ),
        temperature=0.6, max_tokens=12000,
    )
    allowed = {k for k, _ in iter_units(sections, prose_only=True) if _in_scope(k, only)}
    return _apply_guarded(sections, data.get("changes"), allowed, seo)


# ------------------------------------------------------------- 7. 검수


def review(
    client,
    sections: dict[str, SectionDraft],
    *,
    facts: ProductFacts,
    seo: SeoPlan,
    source: str,
    use_ai: bool = True,
    only: set[str] | None = None,
) -> list[ReviewIssue]:
    found = {i.key: i for i in checks.run(sections, source_text=source, seo=seo)}
    if use_ai:
        data = ask_json(
            client, "review", P.SYSTEM,
            P.REVIEW.substitute(
                facts=facts.as_prompt(), main_keyword=seo.main_keyword,
                sub_keywords=", ".join(seo.sub_keywords) or "(없음)", units=numbered(sections),
            ),
            temperature=0.2, max_tokens=6000,
        )
        known = {k for k, _ in iter_units(sections)}
        for issue in from_data(list[ReviewIssue], data.get("issues")):
            issue.key = issue.key.strip()
            if issue.key not in known:
                continue
            issue.origin = "ai"
            if issue.key in found:
                found[issue.key].reason += f" / {issue.reason}"
            else:
                found[issue.key] = issue
    return [i for i in found.values() if _in_scope(i.key, only)]


# ------------------------------------------------------------- 8. 부분 수정


def fix(
    client,
    sections: dict[str, SectionDraft],
    issues: list[ReviewIssue],
    *,
    facts: ProductFacts,
    seo: SeoPlan,
    persona: str,
) -> dict[str, SectionDraft]:
    texts = dict(iter_units(sections))
    targets = [i for i in issues if i.key in texts]
    if not targets:
        return sections
    listing = "\n".join(
        f"[{i.key}] {texts[i.key]}\n  문제: {i.reason}" + (f"\n  고칠 방향: {i.suggestion}" if i.suggestion else "")
        for i in targets
    )
    data = ask_json(
        client, "fix", P.SYSTEM,
        P.FIX.substitute(persona=persona, facts=facts.as_prompt(), keep=_keep_line(seo), issues=listing),
        temperature=0.5, max_tokens=8000,
    )
    return _apply_guarded(sections, data.get("changes"), {i.key for i in targets}, seo, protect=False)


# ------------------------------------------------------------- 제목


def propose_titles(
    client,
    sections: dict[str, SectionDraft],
    *,
    message: MessagePlan,
    seo: SeoPlan,
    source: str,
    avoid: list[str] | None = None,
) -> tuple[list[TitleOption], int]:
    intro = sections.get("intro")
    headings = [d.heading for d in sections.values() if d.heading]
    user = P.TITLES.substitute(
        main_keyword=seo.main_keyword, sub_keywords=", ".join(seo.sub_keywords) or "(없음)",
        customer=message.customer, message=message.message, headings=" / ".join(headings),
        intro="\n".join(intro.paragraphs[:2]) if intro else "",
    )
    if avoid:
        user += f"\n\n[이미 쓴 제목. 이것과 다른 제목을 낸다]\n" + "\n".join(f"- {t}" for t in avoid)
    data = ask_json(client, "titles", P.SYSTEM, user, required=("titles",), temperature=0.8, max_tokens=3000)

    options = [
        TitleOption(title=o.title.strip(), reason=o.reason.strip())
        for o in from_data(list[TitleOption], data.get("titles"))
        if o.title.strip()
    ][:3]
    if not options:
        options = [TitleOption(title=seo.h1 or seo.main_keyword, reason="제목 후보가 없어 SEO 계획의 H1 을 썼다")]
    # 1순위를 쓰되, 코드 검사에 걸리면 다음 순위로 넘어간다.
    chosen = next((i for i, o in enumerate(options) if not checks.check_title(o.title, seo, source)), 0)
    return options, chosen


# ------------------------------------------------------------- 공통


def _apply_guarded(
    sections: dict[str, SectionDraft],
    changes: object,
    allowed: set[str],
    seo: SeoPlan,
    *,
    protect: bool = True,
) -> dict[str, SectionDraft]:
    """허용된 키만 반영한다. 문체만 고치다 검색어를 잃는 일이 잦아, 검색어가 빠지는 변경은 버린다."""
    if not isinstance(changes, dict):
        return sections
    texts = dict(iter_units(sections))
    keywords = [k for k in [seo.main_keyword, *seo.sub_keywords] if k]
    accepted = {}
    for key, value in changes.items():
        if key not in allowed or not isinstance(value, str):
            continue
        old = texts.get(key, "")
        if protect and any(k in old and k not in value for k in keywords):
            continue
        accepted[key] = value
    return apply_changes(sections, accepted)


def _keep_line(seo: SeoPlan) -> str:
    terms = [k for k in [seo.main_keyword, *seo.sub_keywords] if k]
    if not terms:
        return ""
    return f"[글자 그대로 남겨야 하는 검색어. 있던 자리에서 지우지 마라] {', '.join(terms)}"


def _in_scope(key: str, only: set[str] | None) -> bool:
    return not only or key.split("/", 1)[0] in only


def _squeeze(text: str) -> str:
    return re.sub(r"\s", "", text or "").lower()


def _s(value: object) -> str:
    return str(value or "").strip() if not isinstance(value, (dict, list)) else ""
