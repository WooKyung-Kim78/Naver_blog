"""구매 링크와 상품 정보를 네이버 쇼핑 커넥트 카드 데이터로 바꾼다.

카드에 필요한 식별자는 네 개인데, 링크만 보고 알 수 있는 것은 두 개뿐이다.

| 값 | 어디서 오나 |
| --- | --- |
| `affiliateUrl` | 구매 링크에서 에디터가 붙이는 `ac` 파라미터만 뺀 주소 |
| `channelProductNo` | 구매 링크의 질의 문자열 |
| `creatorSpaceId` | 크리에이터마다 고정. `.env` 의 `NAVER_CREATOR_SPACE_ID` |
| `affiliateProductId` | 링크에 없다. 없으면 비운 채로 둔다 |

브랜드 커넥트 링크를 `naver.me` 단축 주소로 받는 경우가 많아서, 앞의 두 값은
단축 주소가 실제로 도착한 `resolved_url` 에서 찾는다. 본문에 넣는 주소는
수익 추적이 걸려 있으므로 원래 구매 링크를 그대로 쓴다.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse, urlencode, urlunparse

from core.models import KIND_CTA, KIND_LINK, Article, Product, ProductCard

#: 에디터가 카드를 만들 때 붙이는 유입경로 표시. affiliateUrl 에는 들어가지 않는다.
_EDITOR_PARAM = "ac"


def build(product: Product, creator_space_id: str = "") -> ProductCard:
    """상품 하나에 대한 카드 정보를 만든다."""
    href = product.url or product.page_url
    source = _affiliate_source(product)
    query = parse_qs(urlparse(source).query)

    return ProductCard(
        href=href,
        title=product.title,
        subtitle=product.brand or product.site_name,
        thumbnail_url=product.thumbnail_urls[0] if product.thumbnail_urls else "",
        link_label=_host(href),
        affiliate_url=_without_editor_param(source),
        channel_product_no=_first(query.get("channelProductNo")),
        affiliate_product_id=_first(query.get("affiliateProductId")),
        creator_space_id=creator_space_id,
    )


def attach(article: Article, products: list[Product], creator_space_id: str = "") -> None:
    """구매 링크 블록마다 그 링크의 상품 카드를 달아 준다.

    묶음 글은 상품마다 링크가 다르므로 주소로 짝을 맞춘다. 상품이 하나뿐이면
    주소가 조금 달라져도(리다이렉트 등) 그 상품 카드를 쓴다.
    """
    if not products:
        return

    cards = {p.url: build(p, creator_space_id) for p in products if p.url}
    fallback = build(products[0], creator_space_id) if len(products) == 1 else None

    for block in article.blocks:
        if block.kind not in (KIND_LINK, KIND_CTA):
            continue
        card = cards.get(block.href) or fallback
        if not card:
            continue
        # 링크 주소는 블록이 들고 있는 것이 정답이다. 카드가 덮어쓰지 않게 한다.
        block.card = _with_href(card, block.href or card.href)


def _affiliate_source(product: Product) -> str:
    """affiliateUrl 과 channelProductNo 를 캐낼 주소를 고른다.

    단축 주소에는 아무 정보가 없으므로, 리다이렉트가 도착한 주소를 먼저 본다.
    """
    for candidate in (product.resolved_url, product.page_url, product.url):
        if candidate and "channelProductNo" in candidate:
            return candidate
    return product.resolved_url or product.url or product.page_url


def _without_editor_param(url: str) -> str:
    if not url:
        return ""
    parts = urlparse(url)
    if not parts.query:
        return url
    kept = [(k, v) for k, v in parse_qs(parts.query, keep_blank_values=True).items() if k != _EDITOR_PARAM]
    query = urlencode([(k, v) for k, values in kept for v in values])
    return urlunparse(parts._replace(query=query))


def _host(url: str) -> str:
    host = urlparse(url).netloc
    return host[4:] if host.startswith("www.") else host


def _first(values: list[str] | None) -> str:
    return values[0] if values else ""


def _with_href(card: ProductCard, href: str) -> ProductCard:
    if href == card.href:
        return card
    return ProductCard(**{**card.__dict__, "href": href, "link_label": _host(href)})
