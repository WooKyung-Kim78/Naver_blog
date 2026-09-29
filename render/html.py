"""HTML 렌더러.

미리보기용이자 티스토리/워드프레스처럼 HTML 을 받는 플랫폼용 출력이다.
네이버 블로그는 임의 HTML 을 받지 않으므로 여기 출력을 그대로 쓸 수 없고,
render/naver_blocks.py 가 에디터 컴포넌트로 따로 변환한다.
"""

from __future__ import annotations

import json
import uuid
from html import escape

from core.models import (
    KIND_CALLOUT,
    KIND_CHECKLIST,
    KIND_CTA,
    KIND_DIVIDER,
    KIND_HEADING,
    KIND_IMAGE,
    KIND_LINK,
    KIND_PARAGRAPH,
    KIND_QUOTE,
    KIND_TABLE,
    Article,
    ProductCard,
    SeoPlan,
)

CALLOUT_LABEL = {"info": "안내", "tip": "팁", "warn": "체크포인트", "summary": "총평"}

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
.buy-inline { margin:22px 0; padding:14px 18px; background:#f0fdf4; border:1px solid #bbf7d0;
  border-radius:10px; text-align:center; }
.buy-inline a { color:#166534; font-weight:600; text-decoration:none; }
.buy-inline a:hover { text-decoration:underline; }
/* 쇼핑 커넥트 상품 카드. 네이버 본문에 실제로 붙는 마크업과 클래스명을 맞춘다. */
.cta-thumb { margin:36px 0 0; }
.cta-thumb img { width:360px; margin:0 auto; }
.cta-thumb + .se-component.se-shopping-connect { margin-top:12px; }
.se-component.se-shopping-connect { margin:28px 0; }
.se-section-shopping-connect .__se_link { display:block; text-decoration:none; color:inherit; }
.se-shopping-connect-content { display:flex; align-items:stretch; overflow:hidden;
  border:1px solid var(--line); border-radius:12px; background:#fff; }
.se-shopping-connect-content:hover { border-color:#c7cdd4; }
.se-shopping-connect-thumbnail { flex:0 0 132px; background:#f4f6f8; }
.se-shopping-connect-thumbnail-resource { width:132px; height:132px; object-fit:cover;
  margin:0; border-radius:0; display:block; }
.se-shopping-connect-detail { flex:1 1 auto; min-width:0; display:flex; align-items:center; }
.se-shopping-connect-detail-inner { padding:16px 20px; width:100%; }
.se-shopping-connect-title { font-size:16px; line-height:1.45; font-weight:600; margin:0;
  padding:0; border:0; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
  overflow:hidden; }
.se-shopping-connect-subtitle { font-size:14px; color:var(--muted); margin:6px 0 0; }
.se-shopping-connect-link { font-size:13px; color:#9aa3ac; margin:10px 0 0; }
.cta-card { display:block; margin:36px 0 8px; padding:24px; text-align:center;
  border:2px solid var(--accent); border-radius:14px; text-decoration:none; color:var(--ink); }
.cta-card img { width:260px; margin:0 auto 16px; border-radius:10px; }
.cta-text { display:block; font-size:17px; margin-bottom:16px; }
.cta-button { display:inline-block; padding:12px 32px; background:var(--accent); color:#fff;
  border-radius:999px; font-weight:700; }
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

    if kind == KIND_DIVIDER:
        return "<hr>"

    if kind == KIND_IMAGE:
        if not block.image or not block.image.path:
            return ""
        src = block.image.path.as_posix()
        # 인포그래픽은 제목을 그림 안에 이미 그려 넣었다. 밑에 또 달면 겹친다.
        caption = "" if block.image.source == "infographic" else (block.image.credit or block.image.caption)
        cap_html = f"<figcaption>{escape(caption)}</figcaption>" if caption else ""
        return f'<figure><img src="{escape(src)}" alt="{escape(block.image.caption)}">{cap_html}</figure>'

    if kind == KIND_LINK:
        if block.card:
            return shopping_connect(block.card)
        return (
            f'<div class="buy-inline"><a href="{escape(block.href)}" target="_blank" rel="nofollow">'
            f"👉 {escape(block.text)}</a></div>"
        )

    if kind == KIND_CTA:
        if block.card:
            # 마무리는 큰 썸네일을 먼저 보여주고 그 아래에 상품 카드를 붙인다.
            hero = ""
            if block.image and block.image.path:
                hero = (
                    f'<figure class="cta-thumb"><img src="{escape(block.image.path.as_posix())}" '
                    'alt="상품 이미지"></figure>'
                )
            return hero + shopping_connect(block.card)
        thumb = ""
        if block.image and block.image.path:
            thumb = f'<img src="{escape(block.image.path.as_posix())}" alt="상품 이미지">'
        return (
            f'<a class="cta-card" href="{escape(block.href)}" target="_blank" rel="nofollow">'
            f'{thumb}<span class="cta-text">{escape(block.text)}</span>'
            f'<span class="cta-button">상품 보러 가기</span></a>'
        )

    return ""


def shopping_connect(card: ProductCard, *, fallback_thumbnail: str = "") -> str:
    """네이버 본문에 실제로 들어가는 쇼핑 커넥트 상품 카드 마크업을 만든다.

    component_id 는 네이버가 컴포넌트마다 새로 발급하는 값이라 여기서도 매번 새로 만든다.
    """
    component_id = f"SE-{uuid.uuid4()}".upper()
    src = card.thumbnail_url or fallback_thumbnail
    title = card.title or "상품 보러 가기"

    linkdata = {"id": component_id, "affiliateUrl": card.affiliate_url or card.href}
    for key, value in (
        ("creatorSpaceId", card.creator_space_id),
        ("channelProductNo", card.channel_product_no),
        ("affiliateProductId", card.affiliate_product_id),
    ):
        if value:
            linkdata[key] = value

    thumbnail = ""
    if src:
        thumbnail = (
            '<div class="se-shopping-connect-thumbnail se-image-loaded">'
            f'<img class="se-shopping-connect-thumbnail-resource egjs-visible" '
            f'src="{escape(src)}" alt="{escape(title)}">'
            "</div>"
        )
    subtitle = (
        f'<p class="se-shopping-connect-subtitle">{escape(card.subtitle)}</p>' if card.subtitle else ""
    )
    link_label = (
        f'<p class="se-shopping-connect-link">{escape(card.link_label)}</p>' if card.link_label else ""
    )

    return (
        f'<div class="se-component se-shopping-connect se-l-default" id="{component_id}">'
        '<div class="se-component-content se-component-content-normal">'
        '<div class="se-section se-section-shopping-connect se-section-align-center se-l-default">'
        f'<a href="{escape(card.href)}" target="_blank" rel="noopener noreferrer" class="__se_link" '
        f'data-linktype="shoppingConnect" '
        f'data-linkdata="{escape(json.dumps(linkdata, ensure_ascii=False))}">'
        '<div class="se-shopping-connect-content">'
        f"{thumbnail}"
        '<div class="se-shopping-connect-detail">'
        '<div class="se-shopping-connect-detail-inner">'
        '<div class="se-shopping-connect-info">'
        f'<h2 class="se-shopping-connect-title">{escape(title)}</h2>'
        f"{subtitle}{link_label}"
        "</div></div></div></div></a></div></div></div>"
    )


def render_tistory(article: Article, image_url) -> str:
    """티스토리 본문 HTML.

    쇼핑 커넥트 카드는 네이버 에디터 안에서만 동작한다. 티스토리에서는 같은 구매
    링크를 버튼으로 넣어서, 구글에서 들어온 독자가 그 버튼으로 상품 페이지에 간다.
    image_url(asset) 은 로컬 이미지를 공개 주소로 바꾼다. 빈 문자열이면 그 그림은 빠진다.
    """
    parts = [_tistory_block(block, image_url) for block in article.blocks]
    return "\n".join(part for part in parts if part)


def _tistory_block(block, image_url) -> str:
    kind = block.kind

    if kind == KIND_HEADING:
        level = max(2, min(block.level, 4))
        return f"<h{level}>{escape(block.text)}</h{level}>"

    if kind == KIND_PARAGRAPH:
        return f"<p>{_br(block.text)}</p>"

    if kind == KIND_QUOTE:
        return f"<blockquote>{_br(block.text)}</blockquote>"

    if kind == KIND_CALLOUT:
        label = CALLOUT_LABEL.get(block.style, "안내")
        return (
            '<div style="margin:24px 0;padding:16px 18px;background:#f8fafc;'
            'border:1px solid #e5e7eb;border-radius:10px;">'
            f"<strong>{escape(label)}</strong><br>{_br(block.text)}</div>"
        )

    if kind == KIND_CHECKLIST:
        items = "".join(f"<li>{escape(item)}</li>" for item in block.items)
        return f"<ul>{items}</ul>"

    if kind == KIND_TABLE:
        head = "".join(f"<th>{escape(h)}</th>" for h in block.headers)
        rows = "".join(
            "<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in row) + "</tr>" for row in block.rows
        )
        return (
            '<table style="width:100%;border-collapse:collapse;">'
            f"<thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>"
        )

    if kind == KIND_DIVIDER:
        return "<hr>"

    if kind == KIND_IMAGE:
        return _tistory_image(block.image, image_url)

    if kind in (KIND_LINK, KIND_CTA):
        image = _tistory_image(block.image, image_url) if kind == KIND_CTA else ""
        return image + _tistory_buy(block)

    return ""


def _tistory_image(image, image_url) -> str:
    if not image:
        return ""
    src = image_url(image) if image_url else ""
    if not src:
        return ""
    alt = image.caption or "상품 이미지"
    caption = "" if image.source == "infographic" else (image.credit or image.caption)
    cap_html = (
        f'<figcaption style="font-size:13px;color:#6b7280;text-align:center;">{escape(caption)}</figcaption>'
        if caption
        else ""
    )
    return (
        f'<figure style="margin:24px 0;"><img src="{escape(src)}" alt="{escape(alt)}" '
        f'style="max-width:100%;height:auto;">{cap_html}</figure>'
    )


def _tistory_buy(block) -> str:
    """구매 버튼. href 는 제휴 링크 그대로라 추적 주소가 바뀌지 않는다."""
    href = ""
    if block.card and block.card.href:
        href = block.card.href
    href = href or block.href
    label = (block.text or "").strip()
    if not href:
        return f"<p>{_br(label)}</p>" if label else ""

    button = label if label and len(label) <= 36 else "상품 보러 가기"
    lead = f"<p>{_br(label)}</p>" if label and label != button else ""
    title = ""
    if block.card and block.card.title and block.card.title not in (label or ""):
        title = (
            '<p style="text-align:center;font-weight:700;margin:0 0 8px;">'
            f"{escape(block.card.title)}</p>"
        )
    return (
        f"{lead}{title}"
        '<p style="text-align:center;margin:28px 0;">'
        f'<a href="{escape(href)}" target="_blank" rel="sponsored noopener" '
        'style="display:inline-block;padding:12px 28px;background:#03c75a;color:#ffffff;'
        'text-decoration:none;border-radius:8px;font-weight:700;">'
        f"{escape(button)}</a></p>"
    )


def _br(text: str) -> str:
    return "<br>".join(escape(line) for line in (text or "").split("\n"))
