"""상품 페이지에서 홍보 글 작성에 필요한 정보를 뽑아낸다.

먼저 requests 로 시도하고, 자바스크립트로 렌더링되는 쇼핑몰(스마트스토어, 쿠팡 등)이라
본문이 거의 없으면 Playwright 로 다시 가져온다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

MIN_TEXT_LENGTH = 400


@dataclass
class Product:
    url: str
    title: str = ""
    description: str = ""
    price: str = ""
    brand: str = ""
    site_name: str = ""
    image_urls: list[str] = field(default_factory=list)
    body_text: str = ""

    def as_prompt_context(self, limit: int = 6000) -> str:
        parts = [f"[상품 페이지 URL] {self.url}"]
        for label, value in (
            ("상품명", self.title),
            ("브랜드", self.brand),
            ("판매처", self.site_name),
            ("가격", self.price),
            ("요약", self.description),
        ):
            if value:
                parts.append(f"[{label}] {value}")
        if self.body_text:
            parts.append(f"[페이지 본문]\n{self.body_text[:limit]}")
        return "\n".join(parts)


def fetch(url: str, *, verify_ssl: bool = True, proxies: dict | None = None) -> Product:
    html = _fetch_static(url, verify_ssl=verify_ssl, proxies=proxies)
    product = _parse(url, html) if html else Product(url=url)

    if len(product.body_text) < MIN_TEXT_LENGTH:
        rendered = _fetch_rendered(url)
        if rendered:
            product = _merge(product, _parse(url, rendered))

    return product


def _fetch_static(url: str, *, verify_ssl: bool, proxies: dict | None) -> str:
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"},
            timeout=20,
            verify=verify_ssl,
            proxies=proxies or None,
        )
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or resp.encoding
        return resp.text
    except requests.RequestException:
        return ""


def _fetch_rendered(url: str) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ""

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            context = browser.new_context(
                user_agent=UA,
                locale="ko-KR",
                timezone_id="Asia/Seoul",
                viewport={"width": 1440, "height": 950},
                extra_http_headers={"Accept-Language": "ko-KR,ko;q=0.9"},
            )
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                "Object.defineProperty(navigator, 'languages', {get: () => ['ko-KR', 'ko']});"
                "Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});"
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(3000)
            for _ in range(4):
                page.mouse.wheel(0, 2500)
                page.wait_for_timeout(1200)
            html = page.content()
            browser.close()
            return html
    except Exception:
        return ""


def _parse(url: str, html: str) -> Product:
    soup = BeautifulSoup(html, "lxml")
    product = Product(url=url)

    def meta(*names: str) -> str:
        for name in names:
            tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
            if tag and tag.get("content"):
                return tag["content"].strip()
        return ""

    product.title = meta("og:title", "twitter:title") or (soup.title.get_text(strip=True) if soup.title else "")
    product.description = meta("og:description", "description", "twitter:description")
    product.site_name = meta("og:site_name")
    product.price = meta("product:price:amount", "og:price:amount")

    for tag in soup.find_all("meta", attrs={"property": "og:image"}):
        if tag.get("content"):
            product.image_urls.append(urljoin(url, tag["content"]))

    _apply_json_ld(soup, product, url)

    for junk in soup(["script", "style", "noscript", "header", "footer", "nav", "iframe", "svg"]):
        junk.decompose()
    text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True))
    product.body_text = re.sub(r"[ \t]{2,}", " ", text)

    return product


def _apply_json_ld(soup: BeautifulSoup, product: Product, url: str) -> None:
    """schema.org Product 구조화 데이터가 있으면 가격/브랜드를 정확히 가져온다."""
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue

        for node in data if isinstance(data, list) else [data]:
            if not isinstance(node, dict) or "Product" not in str(node.get("@type", "")):
                continue

            product.title = product.title or str(node.get("name", ""))
            product.description = product.description or str(node.get("description", ""))

            brand = node.get("brand")
            if isinstance(brand, dict):
                brand = brand.get("name")
            product.brand = product.brand or str(brand or "")

            offers = node.get("offers")
            if isinstance(offers, list):
                offers = offers[0] if offers else None
            if isinstance(offers, dict) and offers.get("price"):
                currency = offers.get("priceCurrency", "KRW")
                product.price = product.price or f"{offers['price']} {currency}"

            images = node.get("image")
            for img in [images] if isinstance(images, str) else (images or []):
                if isinstance(img, str):
                    product.image_urls.append(urljoin(url, img))


def _merge(base: Product, extra: Product) -> Product:
    for attr in ("title", "description", "price", "brand", "site_name"):
        if not getattr(base, attr):
            setattr(base, attr, getattr(extra, attr))
    if len(extra.body_text) > len(base.body_text):
        base.body_text = extra.body_text
    seen = set(base.image_urls)
    base.image_urls.extend(u for u in extra.image_urls if u not in seen and not seen.add(u))
    return base
