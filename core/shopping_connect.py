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

PLATFORM_NAVER = "naver"
PLATFORM_TOSS = "toss"

#: 토스쇼핑 쉐어링크 글 상단에 회색으로 넣는 대가성 문구.
TOSS_DISCLOSURE = "[광고] 토스쇼핑 쉐어링크 활동으로, 링크 구매 시 수수료를 지급받습니다."


def platform(url: str) -> str:
    """링크 도메인으로 제휴 플랫폼을 가린다. 모르는 도메인이면 빈 문자열."""
    host = urlparse(url or "").netloc.lower()
    if "naver" in host:
        return PLATFORM_NAVER
    if "toss" in host:
        return PLATFORM_TOSS
    return ""


def is_toss(url: str) -> bool:
    return platform(url) == PLATFORM_TOSS


def disclosure_for(urls: list[str], default: str) -> str:
    """토스 링크가 하나라도 있으면 토스 문구를, 아니면 기본 문구를 쓴다."""
    return TOSS_DISCLOSURE if any(is_toss(u) for u in urls) else default


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

    # 토스 링크는 쇼핑 커넥트 카드가 아니라 일반 링크로 넣는다.
    cards = {p.url: build(p, creator_space_id) for p in products if p.url and not is_toss(p.url)}
    single = products[0] if len(products) == 1 else None
    fallback = build(single, creator_space_id) if single and not is_toss(single.url) else None

    for block in article.blocks:
        if block.kind not in (KIND_LINK, KIND_CTA):
            continue
        if is_toss(block.href):
            block.card = None
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
