"""스마트스토어/브랜드스토어 페이지에 심어 둔 상품 JSON 을 읽는다.

네이버는 자동 접근을 막으면 화면 본문을 비우거나 '상품이 없습니다'로 바꾸지만,
첫 응답의 window.__PRELOADED_STATE__ 에는 상품명·가격·이미지가 남아 있는 경우가 많다.
화면을 스크롤하기 전에 여기를 읽으면 차단을 우회하려고 페이지를 억지로 뚫을 필요가 없다.
"""

from __future__ import annotations

import re

from core.models import Product

GENERIC = ("네이버 브랜드 커넥트", "네이버플러스 스토어", "네이버쇼핑", "스마트스토어")


def _clean(raw: str) -> str:
    title = re.sub(r"\s*[:|]\s*[^:|]{1,30}$", "", (raw or "").strip())
    title = re.sub(r"^\s*[\[(][^\])]{1,20}[\])]\s*", "", title)
    return title.strip()


def _generic(title: str) -> bool:
    lowered = (title or "").lower()
    return not lowered or any(g in lowered for g in GENERIC)


def read_from_page(page) -> dict:
    try:
        return (
            page.evaluate(
                """() => (window.__PRELOADED_STATE__
                    && window.__PRELOADED_STATE__.simpleProductForDetailPage) || null"""
            )
            or {}
        )
    except Exception:
        return {}


def apply(product: Product, state: dict) -> Product:
    if not state:
        return product

    name = _clean(str(state.get("name") or state.get("dispName") or ""))
    if name and not _generic(name):
        product.title = name

    benefits = state.get("benefitsView") or {}
    price = benefits.get("discountedSalePrice") or state.get("salePrice") or state.get("dispSalePrice")
    if price and not product.price:
        product.price = f"{price} KRW"

    channel = state.get("channel") or {}
    if channel.get("channelName"):
        product.site_name = product.site_name or str(channel["channelName"])
    if channel.get("accountId"):
        product.brand = product.brand or str(channel.get("channelName") or channel["accountId"])

    category = state.get("category") or {}
    category_name = category.get("wholeCategoryName") or category.get("categoryName") or ""

    images: list[str] = []
    if state.get("representativeImageUrl"):
        images.append(str(state["representativeImageUrl"]))
    for url in state.get("optionalImageUrls") or []:
        if url:
            images.append(str(url))
    seen = set(product.thumbnail_urls)
    product.thumbnail_urls.extend(u for u in images if u not in seen and not seen.add(u))

    tags = []
    seo = state.get("seoInfo") or {}
    for tag in seo.get("sellerTags") or []:
        text = tag.get("text") if isinstance(tag, dict) else str(tag)
        if text:
            tags.append(str(text))

    facts = _facts(name or product.title, product.price, category_name, product.site_name, tags)
    body = product.body_text or ""
    dead = any(
        m in body[:800]
        for m in ("상품이 존재하지 않습니다", "삭제되었거나", "에러페이지", "시스템 오류")
    )
    if dead or len(facts) > len(body):
        product.body_text = facts
    elif facts and facts not in body:
        product.body_text = f"{facts}\n\n{body}"

    if category_name and "카테고리" not in product.specs:
        product.specs["카테고리"] = category_name
    if tags and "판매 태그" not in product.specs:
        product.specs["판매 태그"] = ", ".join(tags[:12])

    return product


def _facts(title: str, price: str, category: str, store: str, tags: list[str]) -> str:
    lines = ["[스마트스토어 상품 정보]"]
    if title:
        lines.append(f"상품명: {title}")
    if price:
        lines.append(f"판매가: {price}")
    if store:
        lines.append(f"판매처: {store}")
    if category:
        lines.append(f"카테고리: {category}")
    if tags:
        lines.append(f"태그: {', '.join(tags[:12])}")
    return "\n".join(lines)
