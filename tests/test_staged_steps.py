"""단계별 집필의 각 단계와 코드 검수."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from ai.staged import checks, steps
from ai.staged.assemble import assemble
from ai.staged.prompts import SECTION_ORDER
from ai.staged.schema import StageError
from ai.staged.units import apply_changes, iter_units
from core.checkpoint import from_data
from core.models import (
    KIND_CALLOUT,
    KIND_CTA,
    KIND_HEADING,
    KIND_IMAGE,
    KIND_LINK,
    KIND_TABLE,
    FocusPoint,
    MessagePlan,
    ProductBrief,
    ProductFacts,
    ReviewIssue,
    SectionDraft,
    SeoPlan,
)
from fakes import BRIEF, FACTS, FOCUS, MAIN, MESSAGE, SECTION_BODIES, SEO, FakeClient, Script, make_product

SEO_PLAN = from_data(SeoPlan, SEO)


def _facts(client=None) -> ProductFacts:
    return steps.extract_facts(client or FakeClient(), make_product())


def _outline(client=None):
    client = client or FakeClient()
    return steps.plan_outline(client, _facts(), from_data(MessagePlan, MESSAGE), SEO_PLAN)


def _sections() -> dict[str, SectionDraft]:
    client = FakeClient()
    return steps.write_sections(
        client, list(SECTION_ORDER), product=make_product(), facts=_facts(),
        message=from_data(MessagePlan, MESSAGE), outline=_outline(), persona="담백한 말투",
    )


# ------------------------------------------------------------- 1. 사실


def test_facts_keep_only_claims_found_in_source():
    facts = _facts()
    texts = [f.text for f in facts.facts]
    assert "식약처 인증을 받은 세계 최초 진공 쌀통" not in texts
    assert facts.dropped == ["식약처 인증을 받은 세계 최초 진공 쌀통"]
    # 스펙과 가격은 코드가 직접 사실로 넣는다.
    assert "용량: 10kg" in texts and any("29,900원" in t for t in texts)
    assert [f.id for f in facts.facts] == [f"F{i}" for i in range(1, len(facts.facts) + 1)]


def test_image_facts_need_images():
    data = {"name": "x", "facts": [
        {"text": "이미지 속 문구", "quote": "이미지에만 있는 글자", "source": "image"},
        {"text": "투명 창이 있다", "quote": "투명 창으로 남은 쌀의 양을 확인할 수 있습니다", "source": "text"},
    ]}
    no_images = steps.extract_facts(FakeClient({"facts": data}), make_product(specs={}, price=""))
    assert no_images.dropped == ["이미지 속 문구"]
    assert [f.text for f in no_images.facts] == ["투명 창이 있다"]

    with_images = steps.extract_facts(FakeClient({"facts": data}), make_product(specs={}, price=""),
                                      images=[Path("d0.jpg")])
    assert [f.source for f in with_images.facts] == ["image", "text"]


def test_facts_fail_when_nothing_is_confirmed():
    data = {"name": "x", "facts": [{"text": "지어낸 말", "quote": "원문에 없는 문장입니다", "source": "text"}]}
    with pytest.raises(StageError, match="facts"):
        steps.extract_facts(FakeClient({"facts": data}), make_product(specs={}, price=""))


# ------------------------------------------------------------- 3·4. 메시지, 개요


def test_message_drops_unknown_fact_ids():
    message = steps.plan_message(
        FakeClient(), _facts(), from_data(ProductBrief, BRIEF),
        from_data(FocusPoint, FOCUS["focus_points"][0]), SEO_PLAN,
    )
    assert message.message and message.fact_ids == ["F1"]


def test_message_prompt_carries_focus():
    client = FakeClient()
    steps.plan_message(client, _facts(), from_data(ProductBrief, BRIEF),
                       from_data(FocusPoint, FOCUS["focus_points"][0]), SEO_PLAN)
    assert "[집중 포인트] 진공 버튼" in client.log[-1][1]


def test_outline_fixes_section_order_and_spreads_keywords():
    outline = _outline()
    assert [s.id for s in outline.sections] == list(SECTION_ORDER)
    assert outline.get("intro").heading == ""
    assert outline.get("tips").heading == "사용 방법과 활용 팁"  # 빠진 섹션은 기본값
    assert outline.get("need").heading == "쌀벌레 방지가 어려운 이유"
    assert outline.hashtags == ["진공쌀통", "쌀통추천"]
    main_hits = [s.id for s in outline.sections if MAIN in s.keywords]
    assert main_hits[0] == "intro" and "summary" not in main_hits
    for keyword in SEO["sub_keywords"]:
        assert sum(keyword in s.keywords for s in outline.sections) == 1


# ------------------------------------------------------------- 5. 섹션


def test_sections_written_in_requested_batch():
    client = FakeClient()
    drafts = steps.write_sections(
        client, ["intro", "need", "features"], product=make_product(), facts=_facts(),
        message=from_data(MessagePlan, MESSAGE), outline=_outline(), persona="말투",
    )
    assert list(drafts) == ["intro", "need", "features"]
    assert drafts["features"].rows[0] == ["진공 버튼", "버튼 한 번으로 공기를 뺍니다", "매번 꾹 눌러 닫지 않아도 돼요"]
    assert client.stages() == ["sections"]
    prompt = client.log[0][1]
    assert "### intro (도입부)" in prompt and "### tips" not in prompt


def test_missing_section_is_requested_again_then_fails():
    partial = Script([{"sections": {"intro": SECTION_BODIES["intro"]}},
                      {"sections": {"need": SECTION_BODIES["need"]}}])
    client = FakeClient({"sections": partial})
    drafts = steps.write_sections(
        client, ["intro", "need"], product=make_product(), facts=_facts(),
        message=from_data(MessagePlan, MESSAGE), outline=_outline(), persona="말투",
    )
    assert set(drafts) == {"intro", "need"}
    assert "### need" in client.log[1][1] and "### intro" not in client.log[1][1]

    client = FakeClient({"sections": {"sections": {"intro": {"paragraphs": []}}}})
    with pytest.raises(StageError, match="intro"):
        steps.write_sections(
            client, ["intro"], product=make_product(), facts=_facts(),
            message=from_data(MessagePlan, MESSAGE), outline=_outline(), persona="말투",
        )


def test_cta_text_never_contains_urls():
    assert "http" not in _sections()["cta"].closing


# ------------------------------------------------------------- 6. 통일


def test_unify_keeps_keywords_and_ignores_unknown_keys():
    sections = _sections()
    unified = steps.unify(FakeClient(), sections, message=from_data(MessagePlan, MESSAGE),
                          seo=SEO_PLAN, persona="말투")
    assert unified["need"].paragraphs[1] == "그래서 무엇보다 밀폐가 중요합니다."
    # 메인 키워드를 지우는 변경은 버린다.
    assert MAIN in unified["intro"].paragraphs[0]
    assert sections["need"].paragraphs[1] == "그래서 밀폐가 중요합니다."  # 원본은 그대로


def test_unify_only_touches_requested_sections():
    sections = _sections()
    unified = steps.unify(FakeClient(), sections, message=from_data(MessagePlan, MESSAGE),
                          seo=SEO_PLAN, persona="말투", only={"tips"})
    assert unified["need"].paragraphs == sections["need"].paragraphs


# ------------------------------------------------------------- 7. 검수


def _check(sections):
    return {i.key: i for i in checks.run(sections, source_text=steps.source_text(make_product(), SEO_PLAN), seo=SEO_PLAN)}


def test_clean_draft_passes_code_checks():
    assert _check(_sections()) == {}


@pytest.mark.parametrize("text, kind", [
    ("이 쌀통은 역대급 성능이에요.", "exaggeration"),
    ("품절 임박이니 지금 바로 담아 두세요.", "pressure"),
    ("제가 한 달 동안 직접 써 보니 좋았어요.", "experience"),
    ("진공 상태가 6개월 동안 유지돼요.", "number"),
    ("쌀벌레를 막아 질병 예방 효과가 있어요.", "exaggeration"),
    ("가" * 260, "readability"),
    (f"{MAIN} 하면 {MAIN} 이죠.", "keyword"),
])
def test_code_checks_catch_problems(text, kind):
    sections = _sections()
    sections["need"].paragraphs[1] = text
    issues = _check(sections)
    assert issues["need/p1"].type == kind


def test_numbers_from_source_are_allowed_and_scenes_allow_time():
    sections = _sections()
    sections["tips"].items[0] = "10kg 쌀을 붓고 30회 정도 쓸 수 있어요"
    sections["scenes"].scenes[0][1] = "출근 전 10분, 쌀을 덜어 내기 좋아요."
    assert _check(sections) == {}
    sections["scenes"].scenes[0][1] = "500W 모터로 진공을 만들어요."
    assert _check(sections)["scenes/s0.1"].type == "number"


def test_review_merges_ai_issues_with_known_keys_only():
    issues = steps.review(FakeClient(), _sections(), facts=_facts(), seo=SEO_PLAN,
                          source=steps.source_text(make_product(), SEO_PLAN))
    assert [(i.key, i.origin) for i in issues] == [("checks/i1", "ai")]

    client = FakeClient()
    assert steps.review(client, _sections(), facts=_facts(), seo=SEO_PLAN,
                        source=steps.source_text(make_product(), SEO_PLAN), use_ai=False) == []
    assert "review" not in client.stages()


# ------------------------------------------------------------- 8. 부분 수정


def test_fix_changes_only_flagged_units():
    sections = _sections()
    issue = ReviewIssue(key="checks/i1", type="flow", reason="이어지지 않음")
    fixed = steps.fix(FakeClient(), sections, [issue], facts=_facts(), seo=SEO_PLAN, persona="말투")
    before, after = dict(iter_units(sections)), dict(iter_units(fixed))
    changed = {k for k in before if before[k] != after.get(k)}
    assert changed == {"checks/i1"}
    assert after["checks/i1"] == "확인된 사실 안에서 다시 쓴 문장이에요."


def test_apply_changes_splits_and_deletes():
    sections = _sections()
    result = apply_changes(sections, {
        "need/p0": "첫 문장.\n둘째 문장.", "checks/i0": "", "features/h": "", "nope/p0": "x",
    })
    assert result["need"].paragraphs[:2] == ["첫 문장.", "둘째 문장."]
    assert len(result["checks"].items) == len(sections["checks"].items) - 1
    assert result["features"].heading == sections["features"].heading  # 소제목은 지우지 않는다


# ------------------------------------------------------------- 제목


def test_titles_pick_first_passing_option():
    source = steps.source_text(make_product(), SEO_PLAN)
    options, chosen = steps.propose_titles(FakeClient(), _sections(), message=from_data(MessagePlan, MESSAGE),
                                           seo=SEO_PLAN, source=source)
    assert len(options) == 3 and chosen == 0

    bad_first = {"titles": [{"title": f"{MAIN} 내돈내산 솔직후기"}, {"title": f"{MAIN} 고르는 기준"}]}
    options, chosen = steps.propose_titles(FakeClient({"titles": bad_first}), _sections(),
                                           message=from_data(MessagePlan, MESSAGE), seo=SEO_PLAN, source=source)
    assert chosen == 1


# ------------------------------------------------------------- 9. 조립


def test_assemble_keeps_slots_links_and_order():
    sections = _sections()
    article = assemble(sections, _outline(), "제목", disclosure="대가성 문구", product_url="https://naver.me/x")

    assert article.blocks[0].kind == KIND_CALLOUT and article.blocks[0].text == "대가성 문구"
    assert article.blocks[1].kind == KIND_IMAGE and article.blocks[1].slot == "hero"
    assert [b.slot for b in article.blocks if b.kind == KIND_IMAGE] == [
        "hero", "product", "feature", "lifestyle", "infographic",
    ]
    links = [b for b in article.blocks if b.kind == KIND_LINK]
    assert [b.text for b in links] == ["가격 확인하기", "옵션 살펴보기"]
    assert all(b.href == "https://naver.me/x" for b in links)
    assert article.blocks[-1].kind == KIND_CTA and article.blocks[-1].href == "https://naver.me/x"
    assert any(b.kind == KIND_TABLE for b in article.blocks)
    assert any(b.kind == KIND_CALLOUT and b.style == "summary" for b in article.blocks)
    h2 = [b.text for b in article.blocks if b.kind == KIND_HEADING and b.level == 2]
    assert h2[0] == "쌀벌레 방지가 어려운 이유" and "구매 전에 확인하세요" in h2
    assert article.tags == ["진공쌀통", "쌀통추천"]


def test_assemble_survives_missing_sections():
    sections = copy.deepcopy(_sections())
    for sid in ("tips", "audience", "cta"):
        sections.pop(sid)
    article = assemble(sections, _outline(), "제목", disclosure="d", product_url="u")
    assert article.blocks[-1].kind == KIND_CTA
    assert len([b for b in article.blocks if b.kind == KIND_LINK]) == 2
