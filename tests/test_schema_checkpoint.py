"""JSON 응답 처리와 단계 저장본."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai.staged.schema import StageError, ask_json
from core.checkpoint import Checkpoint, digest, from_data, to_data
from core.models import Fact, ImageAsset, ProductBrief, SectionDraft
from fakes import BRIEF, SECRET, FakeClient, Script, make_product


def test_broken_json_is_retried_once():
    client = FakeClient({"facts": Script(["이건 JSON 이 아님", {"facts": [{"text": "a"}]}])})
    data = ask_json(client, "facts", "sys", "[단계] facts\n...", required=("facts",))
    assert data["facts"] == [{"text": "a"}]
    assert client.calls == 2
    assert "유효한 JSON 이 아니었다" in client.log[1][1]


def test_missing_required_field_asks_again_then_fails():
    client = FakeClient({"message": {"customer": "누군가"}})
    with pytest.raises(StageError) as err:
        ask_json(client, "message", "sys", "[단계] message", required=("message", "customer"))
    assert err.value.stage == "message"
    assert "message" in str(err.value)
    assert client.calls == 2


def test_ai_failure_becomes_stage_error():
    client = FakeClient(fail={"outline": 1})
    with pytest.raises(StageError, match="outline"):
        ask_json(client, "outline", "sys", "[단계] outline")


def test_from_data_coerces_wrong_types():
    brief = from_data(ProductBrief, {
        "category": 3,
        "features": [{"name": "a"}, "문자열은 버린다", {"name": "b", "detail": ["목록"]}],
        "pros": "하나뿐인 장점",
        "cons": None,
    })
    assert brief.category == "3"
    assert [f.name for f in brief.features] == ["a", "b"]
    assert brief.features[1].detail == ""
    assert brief.pros == ["하나뿐인 장점"]
    assert brief.cons == []

    draft = from_data(SectionDraft, {"rows": [["a", "b", "c"]], "paragraphs": ["", "문단"]})
    assert draft.rows == [["a", "b", "c"]] and draft.paragraphs == ["문단"]
    assert from_data(dict[str, SectionDraft], {"x": {"id": "x"}})["x"].id == "x"


def test_checkpoint_hash_controls_reuse(tmp_path):
    ckpt = Checkpoint(tmp_path)
    key = digest("v1", {"a": 1})
    ckpt.save("01_facts", [Fact(id="F1", text="t")], key)

    assert ckpt.load("01_facts", key) == [{"id": "F1", "text": "t", "quote": "", "source": "text"}]
    assert ckpt.load("01_facts", digest("v1", {"a": 2})) is None
    assert ckpt.load("01_facts") is not None
    assert ckpt.load("없는단계") is None
    assert digest({"b": 1, "a": 2}) == digest({"a": 2, "b": 1})


def test_checkpoint_never_writes_secrets(tmp_path):
    ckpt = Checkpoint(tmp_path)
    ckpt.save("leak", {"note": f"key={SECRET}"})
    text = (tmp_path / "stages" / "leak.json").read_text(encoding="utf-8")
    assert SECRET not in text and "***" in text


def test_product_checkpoint_survives_folder_rename(tmp_path):
    run_dir = tmp_path / "20260101_000000_a"
    image = run_dir / "images" / "product" / "t0.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"x")
    Checkpoint(run_dir).save_product(make_product(), [ImageAsset(source="thumbnail", path=image, gallery_index=0)])

    moved = tmp_path / "20260101_000000_b"
    run_dir.rename(moved)
    product, assets = Checkpoint(moved).load_product()
    assert product.title == make_product().title and product.specs == {"용량": "10kg", "재질": "PP"}
    assert assets[0].path == moved / "images" / "product" / "t0.jpg"
    assert assets[0].path.exists()
    saved = json.loads((moved / "stages" / "00_product.json").read_text(encoding="utf-8"))
    assert saved["data"]["assets"][0]["path"] == "images/product/t0.jpg"


def test_to_data_round_trip():
    brief = from_data(ProductBrief, BRIEF)
    assert from_data(ProductBrief, to_data(brief)) == brief
    assert to_data(Path("a/b")) == "a/b"
