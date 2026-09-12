"""HTML 렌더러.

미리보기용이자 티스토리/워드프레스처럼 HTML 을 받는 플랫폼용 출력이다.
네이버 블로그는 임의 HTML 을 받지 않으므로 여기 출력을 그대로 쓸 수 없고,
render/naver_blocks.py 가 에디터 컴포넌트로 따로 변환한다.
"""

from __future__ import annotations

from html import escape

from core.models import (
    KIND_CALLOUT,
    KIND_CHECKLIST,
    KIND_CTA,
    KIND_DIVIDER,
    KIND_FAQ,
    KIND_HEADING,
    KIND_IMAGE,
    KIND_PARAGRAPH,
    KIND_QUOTE,
    KIND_TABLE,
    Article,
    SeoPlan,
)

CALLOUT_LABEL = {"info": "안내", "tip": "팁", "warn": "체크포인트", "summary": "3줄 요약"}

STYLESHEET = """
:root { --line:#e5e7eb; --ink:#1f2937; --muted:#6b7280; --accent:#03c75a; }
* { box-sizing:border-box; }
body { margin:0; padding:32px 16px; background:#f8fafc; color:var(--ink);
  font-family:"Pretendard","Malgun Gothic",system-ui,sans-serif; line-height:1.85; }
article { max-width:760px; margin:0 auto; background:#fff; padding:48px 44px;
  border-radius:16px; box-shadow:0 1px 3px rgba(0,0,0,.08); }
h1 { font-size:30px; line-height:1.35; margin:0 0 28px; }
h2 { font-size:22px; margin:44px 0 14px; padding-left:12px; border-left:4px solid var(--accent); }
h3 { font-size:18px; margin:28px 0 10px; color:#374151; }
p { margin:0 0 18px; }
img { width:100%; border-radius:10px; display:block; margin:24px 0 8px; }
figcaption { font-size:13px; color:var(--muted); text-align:center; margin-bottom:24px; }
blockquote { margin:24px 0; padding:18px 22px; background:#f1f5f9;
  border-left:4px solid #94a3b8; border-radius:0 8px 8px 0; font-size:17px; }
.callout { margin:24px 0; padding:18px 22px; border-radius:10px; border:1px solid var(--line); }
.callout .label { display:inline-block; font-size:12px; font-weight:700; letter-spacing:.04em;
  padding:3px 10px; border-radius:999px; margin-bottom:10px; }
.callout-info { background:#f8fafc; } .callout-info .label { background:#e2e8f0; color:#475569; }
.callout-tip { background:#f0fdf4; } .callout-tip .label { background:#bbf7d0; color:#166534; }
.callout-warn { background:#fffbeb; } .callout-warn .label { background:#fde68a; color:#92400e; }
.callout-summary { background:#eff6ff; } .callout-summary .label { background:#bfdbfe; color:#1e40af; }
ul.check { list-style:none; padding:0; margin:20px 0; }
ul.check li { position:relative; padding:9px 0 9px 32px; border-bottom:1px dashed var(--line); }
ul.check li:before { content:"✓"; position:absolute; left:6px; color:var(--accent); font-weight:700; }
table { width:100%; border-collapse:collapse; margin:24px 0; font-size:15px; }
th,td { border:1px solid var(--line); padding:11px 13px; text-align:left; vertical-align:top; }
th { background:#f8fafc; font-weight:600; }
details { border:1px solid var(--line); border-radius:8px; margin-bottom:10px; }
details summary { padding:14px 18px; cursor:pointer; font-weight:600; }
details p { padding:0 18px 16px; margin:0; color:#374151; }
.cta { margin:32px 0 8px; padding:22px; text-align:center; background:var(--accent);
  color:#fff; border-radius:12px; font-size:17px; font-weight:600; }
.cta a { color:#fff; text-decoration:none; }
.tags { margin-top:36px; padding-top:20px; border-top:1px solid var(--line);
  color:var(--muted); font-size:14px; }
.seo { max-width:760px; margin:24px auto 0; padding:20px 24px; background:#fff;
  border-radius:12px; font-size:14px; color:var(--muted); }
.seo b { color:var(--ink); }
"""


def render(article: Article, seo: SeoPlan | None = None) -> str:
    body = "\n".join(_block(b) for b in article.blocks)
    tags = " ".join(f"#{escape(t)}" for t in article.tags)

    seo_panel = ""
    if seo:
        seo_panel = f"""
<section class="seo">
  <b>메인 키워드</b> {escape(seo.main_keyword)} &nbsp;·&nbsp;
  <b>검색 타입</b> {escape(seo.search_type)} &nbsp;·&nbsp;
  <b>경쟁도</b> {escape(seo.competition)}<br>
  <b>검색 의도</b> {escape(seo.search_intent)}<br>
  <b>보조 키워드</b> {escape(', '.join(seo.sub_keywords))}
</section>"""

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(article.title)}</title>
<meta name="description" content="{escape(seo.meta_description if seo else '')}">
<style>{STYLESHEET}</style>
</head>
<body>
<article>
<h1>{escape(article.title)}</h1>
{body}
<div class="tags">{tags}</div>
</article>{seo_panel}
</body>
</html>"""


def _block(block) -> str:
    kind = block.kind

    if kind == KIND_HEADING:
        level = max(2, min(block.level, 4))
        return f"<h{level}>{escape(block.text)}</h{level}>"

    if kind == KIND_PARAGRAPH:
        return f"<p>{escape(block.text)}</p>"

    if kind == KIND_QUOTE:
        return f"<blockquote>{escape(block.text)}</blockquote>"

    if kind == KIND_CALLOUT:
        label = CALLOUT_LABEL.get(block.style, "안내")
        lines = "<br>".join(escape(line) for line in block.text.split("\n") if line.strip())
        return (
            f'<div class="callout callout-{escape(block.style)}">'
            f'<span class="label">{label}</span><div>{lines}</div></div>'
        )

    if kind == KIND_CHECKLIST:
        items = "".join(f"<li>{escape(i)}</li>" for i in block.items)
        return f'<ul class="check">{items}</ul>'

    if kind == KIND_TABLE:
        head = "".join(f"<th>{escape(h)}</th>" for h in block.headers)
        rows = "".join(
            "<tr>" + "".join(f"<td>{escape(c)}</td>" for c in row) + "</tr>" for row in block.rows
        )
        return f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>"

    if kind == KIND_FAQ:
        return "".join(
            f"<details><summary>Q. {escape(f.question)}</summary>"
            f"<p>{escape(f.answer)}</p></details>"
            for f in block.qa
        )

    if kind == KIND_DIVIDER:
        return "<hr>"

    if kind == KIND_IMAGE:
        if not block.image or not block.image.path:
            return ""
        src = block.image.path.as_posix()
        caption = block.image.credit or block.image.caption
        cap_html = f"<figcaption>{escape(caption)}</figcaption>" if caption else ""
        return f'<figure><img src="{escape(src)}" alt="{escape(block.image.caption)}">{cap_html}</figure>'

    if kind == KIND_CTA:
        inner = escape(block.text)
        if block.href:
            inner = f'<a href="{escape(block.href)}">{inner}</a>'
        return f'<div class="cta">{inner}</div>'

    return ""
