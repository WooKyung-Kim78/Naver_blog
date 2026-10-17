"""파이프라인 전체를 가짜 AI 로 돌린다. 수집·이미지 내려받기는 건너뛴다."""

from __future__ import annotations

import json
import sys

import pytest

import config
import pipeline as pipeline_mod
from ai.staged import StageError
from core.article_io import load_article
from core.models import KIND_LINK
from fakes import SECRET, SECTION_BODIES, TITLES, FakeClient, Script, make_product, sections_response

STAGE_FILES = [
    "00_product", "01_facts", "02_brief", "02_focus", "02_seo", "03_message", "04_outline",
    "05_sections", "06_unified", "07_review", "08_fixed", "09_titles",
]


def make_pipeline(monkeypatch, client, mode="staged"):
    monkeypatch.setattr(pipeline_mod, "MyGenAssistClient", lambda cfg: client)
    p = pipeline_mod.Pipeline(writer_mode=mode)
    monkeypatch.setattr(p, "_collect_product", lambda *a, **k: make_product())
    monkeypatch.setattr(p, "_prepare_images", lambda product, run_dir, owner="": [])
    return p


def only_run_dir():
    dirs = [d for d in config.OUTPUT_DIR.iterdir() if d.is_dir()]
    assert len(dirs) == 1
    return dirs[0]


def all_text(root):
    return "\n".join(
        p.read_text(encoding="utf-8", errors="ignore") for p in root.rglob("*") if p.is_file() and p.suffix in (".json", ".html", ".txt", ".md")
    )


def test_staged_run_end_to_end(monkeypatch):
    client = FakeClient()
    result = make_pipeline(monkeypatch, client).run("https://naver.me/test-buy")

    assert client.stages() == [
        "analyst", "focus", "seo", "facts", "message", "outline",
        "sections", "sections", "sections", "unify", "review", "fix", "titles", "critic",
    ]
    article = result.draft.article
    assert article.title == TITLES["titles"][0]["title"]
    assert result.staged.titles.chosen == 0
    assert result.run_dir.exists() and result.run_dir.parent == config.OUTPUT_DIR
    assert "여름_쌀_보관" in result.run_dir.name

    for name in ("article.json", "article.html", "upload.json", "report.json", "naver.txt", "suggestions.md"):
        assert (result.run_dir / name).exists(), name
    for name in STAGE_FILES:
        assert (result.run_dir / "stages" / f"{name}.json").exists(), name

    report = json.loads((result.run_dir / "report.json").read_text(encoding="utf-8"))
    assert report["writer_mode"] == "staged"
    assert len(report["staged"]["title_options"]) == 3
    assert report["staged"]["meta_description"].startswith("진공 쌀통 추천")
    assert report["ai_calls"] == client.calls
    assert report["brief"]["recommended_for"] == SECTION_BODIES["audience"]["items"]
    assert "버튼 한 번 진공 보관" in (result.run_dir / "article.html").read_text(encoding="utf-8")

    saved = load_article(result.run_dir)
    assert [b.href for b in saved.blocks if b.kind == KIND_LINK] == ["https://naver.me/test-buy"] * 2
    assert SECRET not in all_text(config.OUTPUT_DIR)


def test_legacy_mode_still_available(monkeypatch):
    client = FakeClient()
    result = make_pipeline(monkeypatch, client, mode="legacy").run("https://naver.me/test-buy")

    assert "facts" not in client.stages() and "legacy_front" in client.stages()
    assert result.staged is None
    report = json.loads((result.run_dir / "report.json").read_text(encoding="utf-8"))
    assert report["writer_mode"] == "legacy" and report["staged"] is None
    # 예전 방식에서도 분석 저장본은 남아 비교에 쓸 수 있다.
    assert (result.run_dir / "stages" / "02_seo.json").exists()


def test_resume_after_failure_skips_finished_stages(monkeypatch):
    broken = FakeClient({"sections": Script([sections_response, "JSON 아님", "JSON 아님"])})
    with pytest.raises(StageError, match="sections"):
        make_pipeline(monkeypatch, broken).run("https://naver.me/test-buy")
    run_dir = only_run_dir()
    cached = json.loads((run_dir / "stages" / "05_sections.json").read_text(encoding="utf-8"))
    assert list(cached["data"]["items"]) == ["intro", "need", "features"]

    client = FakeClient()
    result = make_pipeline(monkeypatch, client).resume(run_dir)
    assert client.stages() == ["sections", "sections", "unify", "review", "fix", "titles", "critic"]
    assert "### intro" not in client.log[0][1] and "### scenes" in client.log[0][1]
    assert result.draft.article.title == TITLES["titles"][0]["title"]


def test_rewrite_one_section_leaves_others(monkeypatch):
    first = make_pipeline(monkeypatch, FakeClient()).run("https://naver.me/test-buy")
    before = first.staged.sections

    def new_tips(user):
        data = sections_response(user)
        data["sections"]["tips"] = {**SECTION_BODIES["tips"], "items": ["버튼을 누르고 뚜껑을 닫아요", "투명 창으로 양을 봐요"]}
        return data

    client = FakeClient({"sections": new_tips})
    result = make_pipeline(monkeypatch, client).resume(first.run_dir, sections=["tips"])

    assert client.stages() == ["sections", "unify", "review", "critic"]
    assert "### tips" in client.log[0][1] and "### intro" not in client.log[0][1]
    after = result.staged.sections
    assert after["tips"].items == ["버튼을 누르고 뚜껑을 닫아요", "투명 창으로 양을 봐요"]
    for sid in before:
        if sid != "tips":
            assert after[sid] == before[sid], sid
    assert result.draft.article.title == first.draft.article.title


def test_rewrite_title_only(monkeypatch):
    first = make_pipeline(monkeypatch, FakeClient()).run("https://naver.me/test-buy")
    titles = {"titles": [{"title": "진공 쌀통 추천 장마철 쌀 보관법"}, {"title": "진공 쌀통 추천 둘째"}]}
    client = FakeClient({"titles": titles})
    result = make_pipeline(monkeypatch, client).resume(first.run_dir, title=True)

    assert client.stages() == ["titles", "critic"]
    assert "[이미 쓴 제목" in client.log[0][1]
    assert result.draft.article.title == "진공 쌀통 추천 장마철 쌀 보관법"
    assert result.run_dir.exists() and not first.run_dir.exists()
    assert load_article(result.run_dir).title == "진공 쌀통 추천 장마철 쌀 보관법"


def test_resume_needs_checkpoints(monkeypatch, tmp_path):
    with pytest.raises(RuntimeError, match="00_product"):
        make_pipeline(monkeypatch, FakeClient()).resume(tmp_path)


def test_compare_runs_legacy_on_same_inputs(monkeypatch):
    first = make_pipeline(monkeypatch, FakeClient()).run("https://naver.me/test-buy")
    client = FakeClient()
    rows = make_pipeline(monkeypatch, client, mode="legacy").compare(first.run_dir)

    assert "analyst" not in client.stages() and "legacy_front" in client.stages()
    assert set(rows) == {"staged", "legacy"}
    assert rows["legacy"]["check_issue_kinds"].get("experience")
    assert rows["staged"]["check_issues"] == 0
    assert rows["legacy"]["ai_calls"] == client.calls
    assert (first.run_dir / "compare.json").exists() and (first.run_dir / "compare_legacy.html").exists()
    assert load_article(first.run_dir).title == first.draft.article.title


def test_cli_rewrite_and_post_arguments(monkeypatch):
    import main

    monkeypatch.setattr(pipeline_mod, "MyGenAssistClient", lambda cfg: FakeClient())
    monkeypatch.setattr(sys, "argv", ["main.py", "rewrite", "--from", "x"])
    assert main.main() == 1
    monkeypatch.setattr(sys, "argv", ["main.py", "post"])
    assert main.main() == 1


def test_invalid_writer_mode(monkeypatch):
    monkeypatch.setenv("WRITER_MODE", "magic")
    monkeypatch.setattr(pipeline_mod, "MyGenAssistClient", lambda cfg: FakeClient())
    with pytest.raises(config.ConfigError):
        pipeline_mod.Pipeline()
