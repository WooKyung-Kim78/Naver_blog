"""결과 폴더에 둔 article.json 을 읽고 쓴다."""

from __future__ import annotations

import json
import re
from pathlib import Path

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
    Article,
    Block,
    FAQ,
    ImageAsset,
)
from publish.naver_blog import PostBlock

ARTICLE_JSON = "article.json"
_URL_RE = re.compile(r"https?://\S+")


def save_article(run_dir: Path, article: Article) -> None:
    (run_dir / ARTICLE_JSON).write_text(
        json.dumps(to_dict(article), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_article(run_dir: Path) -> Article | None:
    path = run_dir / ARTICLE_JSON
    if not path.exists():
        return None
    return from_dict(json.loads(path.read_text(encoding="utf-8")))


def to_dict(article: Article) -> dict:
    return {
        "title": article.title,
        "tags": article.tags,
        "disclosure": article.disclosure,
        "blocks": [_block_to_dict(b) for b in article.blocks],
    }


def from_dict(data: dict) -> Article:
    return Article(
        title=str(data.get("title") or ""),
        tags=list(data.get("tags") or []),
        disclosure=str(data.get("disclosure") or ""),
        blocks=[_block_from_dict(b) for b in data.get("blocks") or []],
    )


def article_from_ops(title: str, tags: list[str], payload: list[PostBlock], disclosure: str = "") -> Article:
    """예전 결과물처럼 article.json 이 없을 때 업로드 묶음으로 뼈대를 복원한다."""
    blocks: list[Block] = []
    i = 0
    while i < len(payload):
        op = payload[i]
        if op.kind == "image":
            image = ImageAsset(source="detail", path=Path(op.value) if op.value else None)
            nxt = payload[i + 1] if i + 1 < len(payload) else None
            if nxt and nxt.kind == "text" and "👉" in nxt.value and _URL_RE.search(nxt.value):
                href = _first_url(nxt.value)
                text = _link_label(nxt.value)
                kind = KIND_CTA if "─" in nxt.value else KIND_LINK
                blocks.append(Block(kind=kind, text=text, href=href, image=image if kind == KIND_CTA else None))
                i += 2
                continue
            blocks.append(Block(kind=KIND_IMAGE, image=image))
            i += 1
            continue
        if op.kind == "quote":
            blocks.append(Block(kind=KIND_QUOTE, text=op.value))
        elif op.kind == "divider":
            blocks.append(Block(kind=KIND_DIVIDER))
        else:
            blocks.append(_text_block(op.value, disclosure))
        i += 1
    return Article(title=title, tags=tags, blocks=blocks, disclosure=disclosure)


def _text_block(value: str, disclosure: str) -> Block:
    text = (value or "").strip()
    if text.startswith("■ "):
        return Block(kind=KIND_HEADING, text=text[2:].strip(), level=2)
    if text.startswith("▸ "):
        return Block(kind=KIND_HEADING, text=text[2:].strip(), level=3)
    if text.startswith("✅ "):
        items = [row[2:].strip() for row in text.splitlines() if row.startswith("✅ ")]
        return Block(kind=KIND_CHECKLIST, items=items)
    if "👉" in text and _URL_RE.search(text):
        return Block(kind=KIND_LINK, text=_link_label(text), href=_first_url(text))
    if disclosure and disclosure in text:
        return Block(kind=KIND_CALLOUT, text=disclosure, style="info")
    return Block(kind=KIND_PARAGRAPH, text=text)


def _first_url(text: str) -> str:
    match = _URL_RE.search(text)
    return match.group(0).rstrip(").,]") if match else ""


def _link_label(text: str) -> str:
    for row in text.splitlines():
        row = row.strip().lstrip("👉").strip()
        if row and not row.startswith("http") and "─" not in row:
            return row
    return "상품 보러 가기"


def _block_to_dict(block: Block) -> dict:
    data = {
        "kind": block.kind,
        "text": block.text,
        "level": block.level,
        "style": block.style,
        "items": block.items,
        "headers": block.headers,
        "rows": block.rows,
        "slot": block.slot,
        "href": block.href,
        "qa": [{"question": f.question, "answer": f.answer} for f in block.qa],
    }
    if block.image:
        img = block.image
        data["image"] = {
            "source": img.source,
            "url": img.url,
            "path": str(img.path) if img.path else "",
            "width": img.width,
            "height": img.height,
            "score": img.score,
            "caption": img.caption,
            "credit": img.credit,
            "slot": img.slot,
            "owner": img.owner,
        }
    return data


def _block_from_dict(data: dict) -> Block:
    image = None
    raw = data.get("image")
    if isinstance(raw, dict):
        path = Path(raw["path"]) if raw.get("path") else None
        image = ImageAsset(
            source=str(raw.get("source") or "detail"),
            url=str(raw.get("url") or ""),
            path=path,
            width=int(raw.get("width") or 0),
            height=int(raw.get("height") or 0),
            score=float(raw.get("score") or 0),
            caption=str(raw.get("caption") or ""),
            credit=str(raw.get("credit") or ""),
            slot=str(raw.get("slot") or ""),
            owner=str(raw.get("owner") or ""),
        )
    return Block(
        kind=str(data.get("kind") or KIND_PARAGRAPH),
        text=str(data.get("text") or ""),
        level=int(data.get("level") or 2),
        style=str(data.get("style") or "info"),
        items=list(data.get("items") or []),
        headers=list(data.get("headers") or []),
        rows=[list(row) for row in data.get("rows") or []],
        qa=[FAQ(question=str(f.get("question") or ""), answer=str(f.get("answer") or "")) for f in data.get("qa") or []],
        image=image,
        slot=str(data.get("slot") or ""),
        href=str(data.get("href") or ""),
    )
