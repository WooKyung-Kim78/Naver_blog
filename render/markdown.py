"""미리보기용 원고를 마크다운으로 주고받는다.

사람이 HTML 을 보고 Copilot 으로 article.md 를 고치면, 그 파일을 다시 읽어
네이버 업로드용 조작 목록으로 바꾼다.
"""

from __future__ import annotations

import re
from pathlib import Path

from render.naver_blocks import CALLOUT_MARK, CALLOUT_TITLE, RULE, Op

_IMAGE_RE = re.compile(r"^!\[([^\]]*)\]\((<[^>]+>|[^)]+)\)$")
_URL_RE = re.compile(r"^https?://\S+$", re.I)
_TAG_LINE_RE = re.compile(r"^(?:#[\w가-힣]+(?:\s+#[\w가-힣]+)*)$")
_CALLOUT_LABEL = {title: key for key, title in CALLOUT_TITLE.items()}


def from_ops(title: str, tags: list[str], ops: list[Op], *, base_dir: Path) -> str:
    """업로드 묶음을 Copilot 이 고치기 쉬운 마크다운으로 푼다."""
    lines = [
        "---",
        f"title: {title}",
        f"tags: {', '.join(tags)}",
        "---",
        "",
    ]
    for op in ops:
        if op.kind == "image":
            lines += [f"![]({_rel_image(op.value, base_dir)})", ""]
            continue
        if op.kind == "quote":
            for row in (op.value or "").splitlines() or [""]:
                lines.append(f"> {row}" if row.strip() else ">")
            lines.append("")
            continue
        if op.kind == "divider":
            lines += ["---", ""]
            continue
        lines.extend(_text_to_md(op.value or ""))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse(text: str, *, base_dir: Path) -> tuple[str, list[str], list[Op]]:
    """마크다운을 (제목, 태그, 업로드 조작)으로 읽는다."""
    body, title, tags = _split_frontmatter(text)
    lines = body.replace("\r\n", "\n").split("\n")
    ops: list[Op] = []
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        if stripped == "---":
            ops.append(Op("divider"))
            i += 1
            continue

        image = _IMAGE_RE.match(stripped)
        if image:
            ops.append(Op("image", _abs_image(image.group(2), base_dir)))
            i += 1
            continue

        if stripped.startswith(">"):
            quote, i = _collect_quote(lines, i)
            ops.append(_quote_or_callout(quote))
            continue

        if stripped.startswith("### "):
            ops.append(Op("text", f"\n▸ {stripped[4:].strip()}"))
            i += 1
            continue
        if stripped.startswith("## "):
            ops.append(Op("text", f"\n■ {stripped[3:].strip()}"))
            i += 1
            continue
        if stripped.startswith("# ") and not title:
            title = stripped[2:].strip()
            i += 1
            continue

        if _TAG_LINE_RE.match(stripped.replace("　", " ")):
            tags = [t[1:] for t in stripped.split() if t.startswith("#") and t[1:]]
            i += 1
            continue

        if stripped.startswith(("- ", "* ", "✅ ")):
            items, i = _collect_list(lines, i)
            ops.append(Op("text", "\n".join(f"✅ {item}" for item in items)))
            continue

        para, i = _collect_paragraph(lines, i)
        if para:
            ops.append(Op("text", para))

    return title.strip(), tags, _merge_text(ops)


def _text_to_md(value: str) -> list[str]:
    text = value.strip("\n")
    if not text.strip():
        return []

    if text.startswith(RULE) and text.endswith(RULE):
        inner = text.strip().strip(RULE).strip().splitlines()
        if inner:
            first = inner[0].strip()
            for key, mark in CALLOUT_MARK.items():
                title = CALLOUT_TITLE[key]
                prefix = f"{mark} {title}"
                if first == prefix:
                    body = inner[1:]
                    out = [f"> **{title}**"]
                    out += [f"> {row}" if row.strip() else ">" for row in body] or [">"]
                    return out

    rows = text.splitlines()
    if len(rows) == 1 and rows[0].startswith("■ "):
        return [f"## {rows[0][2:].strip()}"]
    if len(rows) == 1 and rows[0].startswith("▸ "):
        return [f"### {rows[0][2:].strip()}"]
    if all(row.startswith("✅ ") for row in rows if row.strip()):
        return [f"- {row[2:].strip()}" if row.startswith("✅ ") else row for row in rows]

    return rows


def _split_frontmatter(text: str) -> tuple[str, str, list[str]]:
    text = text.replace("\r\n", "\n")
    title, tags = "", []
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            matter = text[4:end]
            body = text[end + 5 :]
            for row in matter.splitlines():
                if row.startswith("title:"):
                    title = row.split(":", 1)[1].strip().strip('"').strip("'")
                elif row.startswith("tags:"):
                    raw = row.split(":", 1)[1].strip()
                    tags = [t.strip() for t in raw.split(",") if t.strip()]
            return body, title, tags
    return text, title, tags


def _collect_quote(lines: list[str], start: int) -> tuple[str, int]:
    rows = []
    i = start
    while i < len(lines) and lines[i].strip().startswith(">"):
        row = lines[i].strip()
        rows.append(row[1:].lstrip() if row.startswith(">") else "")
        i += 1
    return "\n".join(rows).strip("\n"), i


def _quote_or_callout(text: str) -> Op:
    rows = [r for r in text.splitlines()]
    if rows:
        head = rows[0].strip()
        for title, key in _CALLOUT_LABEL.items():
            if head in (f"**{title}**", title, f"{CALLOUT_MARK[key]} {title}"):
                body = [r for r in rows[1:] if r.strip()]
                mark = CALLOUT_MARK[key]
                return Op("text", "\n".join([RULE, f"{mark} {title}", *body, RULE]))
    return Op("quote", text.strip())


def _collect_list(lines: list[str], start: int) -> tuple[list[str], int]:
    items = []
    i = start
    while i < len(lines):
        row = lines[i].rstrip()
        if row.startswith("- "):
            items.append(row[2:].strip())
        elif row.startswith("* "):
            items.append(row[2:].strip())
        elif row.startswith("✅ "):
            items.append(row[2:].strip())
        elif not row.strip():
            break
        else:
            break
        i += 1
    return items, i


def _collect_paragraph(lines: list[str], start: int) -> tuple[str, int]:
    rows = []
    i = start
    while i < len(lines):
        row = lines[i].rstrip()
        if not row.strip():
            break
        if row.strip() == "---" or _IMAGE_RE.match(row.strip()) or row.strip().startswith(">"):
            break
        if row.startswith("## ") or row.startswith("### "):
            break
        if row.startswith(("- ", "* ", "✅ ")):
            break
        rows.append(row)
        i += 1
        if _URL_RE.match(row.strip()) and rows:
            break
    return "\n".join(rows).strip(), i


def _merge_text(ops: list[Op]) -> list[Op]:
    return [op for op in ops if op.kind != "text" or op.value.strip()]


def _rel_image(value: str, base_dir: Path) -> str:
    path = Path(value)
    try:
        return path.resolve().relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _abs_image(raw: str, base_dir: Path) -> str:
    src = raw.strip().strip("<>").strip()
    path = Path(src)
    if not path.is_absolute():
        path = (base_dir / src).resolve()
    return str(path)
