"""임시저장 전에 HTML 을 보고, 제안 마크다운으로 article 을 다시 만든다."""

from __future__ import annotations

import json
from pathlib import Path

import config
from ai.apply_notes import apply_notes, notes_are_empty
from ai.client import MyGenAssistClient
from core.article_io import article_from_ops, load_article, save_article
from core.models import Article, SeoPlan
from publish.naver_blog import PostBlock
from render import html as html_render
from render import naver_blocks

SUGGESTIONS_FILE = "suggestions.md"

SUGGESTIONS_TEMPLATE = """# 수정 제안

이 파일은 완성 원고가 아닙니다. HTML 미리보기를 보고 고칠 점만 적으세요.
저장한 뒤 프로그램이 이 제안으로 article 을 다시 작성합니다.

## 제안

- 
"""


def ensure_suggestions(run_dir: Path) -> Path:
    path = run_dir / SUGGESTIONS_FILE
    if not path.exists():
        path.write_text(SUGGESTIONS_TEMPLATE, encoding="utf-8")
    return path


def resolve_article(run_dir: Path, title: str, tags: list[str], payload: list[PostBlock]) -> Article:
    article = load_article(run_dir)
    if article:
        return article
    disclosure = config.load_post_config().disclosure
    return article_from_ops(title, tags, payload, disclosure=disclosure)


def rebuild_from_notes(
    run_dir: Path,
    notes_path: Path,
    title: str,
    tags: list[str],
    payload: list[PostBlock],
    *,
    report=None,
) -> tuple[str, list[str], list[PostBlock]]:
    notes_path = Path(notes_path)
    if not notes_path.exists():
        raise FileNotFoundError(f"제안 마크다운이 없습니다: {notes_path}")
    notes = notes_path.read_text(encoding="utf-8")
    if notes_are_empty(notes):
        raise RuntimeError("제안 마크다운에 고칠 내용이 없습니다. 제안만 적은 뒤 다시 시도하세요.")

    article = resolve_article(run_dir, title, tags, payload)
    post_cfg = config.load_post_config()
    seo = _load_seo(run_dir)
    if report:
        report("제안사항을 반영해 원고를 다시 작성하는 중")
    client = MyGenAssistClient(config.load_ai_config())
    rebuilt = apply_notes(
        client,
        article,
        notes,
        persona=post_cfg.persona,
        disclosure=post_cfg.disclosure,
        seo=seo,
    )
    dest = run_dir / SUGGESTIONS_FILE
    if notes_path.resolve() != dest.resolve():
        dest.write_text(notes, encoding="utf-8")
    persist_article(run_dir, rebuilt, seo)
    ops = naver_blocks.render(rebuilt)
    return rebuilt.title, rebuilt.tags, [PostBlock(kind=op.kind, value=op.value) for op in ops]


def persist_article(run_dir: Path, article: Article, seo: SeoPlan | None = None) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    save_article(run_dir, article)
    (run_dir / "article.html").write_text(html_render.render(article, seo), encoding="utf-8")
    ops = naver_blocks.render(article)
    (run_dir / "naver.txt").write_text(
        "\n\n".join(f"[{op.kind}] {op.value}" for op in ops),
        encoding="utf-8",
    )
    (run_dir / "upload.json").write_text(
        json.dumps(
            {
                "title": article.title,
                "tags": article.tags,
                "ops": [{"kind": op.kind, "value": op.value} for op in ops],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _load_seo(run_dir: Path) -> SeoPlan | None:
    path = run_dir / "report.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8")).get("seo_plan") or {}
    if not raw:
        return None
    allowed = SeoPlan.__dataclass_fields__
    return SeoPlan(**{k: raw[k] for k in raw if k in allowed})
