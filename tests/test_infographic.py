"""인포그래픽 생성 및 2컷 웹툰 총평 테스트."""

from __future__ import annotations

from pathlib import Path
from PIL import Image

from core.models import Article, Block, Feature, KIND_CALLOUT, Product, ProductBrief, RoundupBrief
from media.infographic import InfographicData, _split_cuts, from_brief, from_roundup, render


def test_split_cuts_two_paragraphs():
    summary = ["첫 번째 문단입니다.", "두 번째 문단입니다."]
    cut1, cut2 = _split_cuts(summary)
    assert cut1 == "첫 번째 문단입니다."
    assert cut2 == "두 번째 문단입니다."


def test_split_cuts_single_paragraph_multiple_sentences():
    summary = ["첫 문장입니다. 두 번째 문장입니다. 세 번째 문장입니다."]
    cut1, cut2 = _split_cuts(summary)
    assert "첫 문장입니다." in cut1
    assert "세 번째 문장입니다." in cut2


def test_split_cuts_single_sentence():
    summary = ["단일 문장 총평입니다."]
    cut1, cut2 = _split_cuts(summary)
    assert cut1 == "단일 문장 총평입니다."
    assert len(cut2) > 0


def test_split_cuts_three_paragraphs():
    summary = ["문단 1", "문단 2", "문단 3"]
    cut1, cut2 = _split_cuts(summary)
    assert cut1 == "문단 1"
    assert cut2 == "문단 2"


def test_split_cuts_empty():
    cut1, cut2 = _split_cuts([])
    assert cut1 == ""
    assert cut2 == ""


def test_render_empty_data(tmp_path: Path):
    data = InfographicData()
    assert data.is_empty
    asset = render(data, tmp_path)
    assert asset is None


def test_render_webtoon_summary(tmp_path: Path):
    data = InfographicData(
        title="테스트 상품",
        features=[("기능 1", "이점 1"), ("기능 2", "이점 2")],
        recommended=["추천 대상 1"],
        not_recommended=["비추천 대상 1"],
        summary=["직접 써보니 가볍고 편안했습니다.", "가성비를 생각한다면 강력 추천합니다!"],
    )
    asset = render(data, tmp_path)
    assert asset is not None
    assert asset.path.exists()
    assert asset.source == "infographic"
    assert asset.width == 860 * 2
    assert asset.height > 0

    with Image.open(asset.path) as img:
        assert img.format == "PNG"
        assert img.size == (asset.width, asset.height)


def test_render_single_summary_paragraph(tmp_path: Path):
    data = InfographicData(
        title="단일 문단 테스트",
        features=[("특징", "설명")],
        summary=["통기성이 뛰어나 장거리 러닝에도 편안합니다. 가벼운 운동용으로 알맞은 선택입니다."],
    )
    asset = render(data, tmp_path)
    assert asset is not None
    assert asset.path.exists()


def test_render_without_summary(tmp_path: Path):
    data = InfographicData(
        title="총평 없는 인포그래픽",
        features=[("특징 1", "설명 1")],
        recommended=["추천 1"],
    )
    asset = render(data, tmp_path)
    assert asset is not None
    assert asset.path.exists()


def test_from_brief_and_roundup():
    product = Product(url="https://example.com", title="테스트 상품")
    brief = ProductBrief(
        features=[Feature(name="기능", detail="상세", benefit="이점")],
        recommended_for=["추천"],
        not_recommended_for=["비추천"],
    )
    article = Article(
        title="글 제목",
        blocks=[
            Block(kind=KIND_CALLOUT, style="summary", text="총평 1문단입니다.\n\n총평 2문단입니다."),
        ],
    )
    data = from_brief(product, brief, article)
    assert data.title == "테스트 상품"
    assert len(data.features) == 1
    assert len(data.summary) == 2

    roundup = RoundupBrief(
        theme="묶음 추천 테마",
        commonalities=["공통점 1"],
        recommended_for=["추천"],
        not_recommended_for=["비추천"],
    )
    r_data = from_roundup(roundup, article)
    assert r_data.title == "묶음 추천 테마"
    assert len(r_data.summary) == 2
