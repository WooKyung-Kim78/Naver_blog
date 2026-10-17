"""기존 기능 회귀 테스트. 예전 집필 방식과 렌더러·저장 형식이 그대로 동작하는지 본다."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai import humanizer, seo as seo_mod, writer
from core.article_io import from_dict, load_article, to_dict
from core.checkpoint import from_data
from core.models import (
    KIND_CALLOUT,
    KIND_CTA,
    KIND_IMAGE,
    KIND_LINK,
    ProductBrief,
    SeoPlan,
)
from fakes import BRIEF, SEO, FakeClient, make_product
from render import html as html_render
from render import naver_blocks

ROOT = Path(__file__).resolve().parents[1]
EXISTING = sorted((ROOT / "output").glob("*/article.json"))


def _legacy_article():
    client = FakeClient()
    article = writer.write(
        client, make_product(), from_data(ProductBrief, BRIEF), from_data(SeoPlan, SEO),
        "담백한 말투", "대가성 문구",
    )
    return client, article


def test_legacy_writer_structure_unchanged():
    client, article = _legacy_article()

    assert client.stages() == ["legacy_front", "legacy_back"]
    assert article.blocks[0].kind == KIND_CALLOUT and article.blocks[0].text == "대가성 문구"
    assert [b.slot for b in article.blocks if b.kind == KIND_IMAGE] == [
        "hero", "product", "feature", "lifestyle", "infographic",
    ]
    assert [b.text for b in article.blocks if b.kind == KIND_LINK] == ["가격 보기", "옵션 보기"]
    assert article.blocks[-1].kind == KIND_CTA and article.blocks[-1].slot == "cta"
    assert any(b.kind == KIND_CALLOUT and b.style == "summary" for b in article.blocks)
    assert article.tags == ["진공쌀통", "쌀통"]


def test_seo_score_and_humanizer_still_work():
    _, article = _legacy_article()
    score = seo_mod.score(article, from_data(SeoPlan, SEO))
    assert 0 < score.total <= 100
    assert score.title_has_main

    report = humanizer.measure("짧게 끝낸다. 이 문장은 조금 더 깁니다. 그리고 이 문장은 그보다 훨씬 더 길게 이어집니다.")
    assert report.sentence_count == 3


def test_article_round_trip_and_renderers():
    _, article = _legacy_article()
    restored = from_dict(to_dict(article))
    assert to_dict(restored) == to_dict(article)

    ops = naver_blocks.render(restored)
    assert ops and ops[-1].value.strip() == "#진공쌀통 #쌀통"
    assert "<h1>" in html_render.render(restored)
    assert html_render.render_tistory(restored, lambda asset: "")


@pytest.mark.skipif(not EXISTING, reason="기존 결과물이 없습니다")
@pytest.mark.parametrize("path", EXISTING, ids=lambda p: p.parent.name[:24])
def test_existing_outputs_still_render(path):
    article = load_article(path.parent)
    assert article is not None and article.blocks
    assert to_dict(from_dict(to_dict(article))) == to_dict(article)
    assert naver_blocks.render(article)
    assert html_render.render(article)
    assert html_render.render_tistory(article, lambda asset: "")
