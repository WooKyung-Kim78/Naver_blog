"""상품 페이지에서 텍스트 정보와 이미지 URL 을 뽑아낸다.

먼저 requests 로 시도하고, 자바스크립트로 렌더링되는 쇼핑몰(스마트스토어, 쿠팡 등)이라
본문이 거의 없으면 Playwright 로 다시 가져온다. 이미지는 media/product_images.py 가
브라우저를 띄워 따로 수집한다.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from core.models import Product

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

MIN_TEXT_LENGTH = 400

#: 쇼핑몰 페이지 텍스트의 절반 이상은 전역 메뉴와 UI 라벨이다. 이런 줄은 짧은 것들이
#: 줄줄이 이어지는 형태로 나타나므로, 그 덩어리째 걷어낸다.
SHORT_LINE = 14
MENU_RUN = 4

#: 상품이 아니라 판매자·사업자 정보라서 스펙표에 들어가면 안 되는 항목들.
SELLER_INFO_KEYS = (
    "상호", "대표자", "사업자", "통신판매", "사업장", "연락처", "고객센터", "이메일",
    "e-mail", "주소", "반품", "교환", "배송", "결제", "환불", "as 안내", "a/s 안내",
)

#: 상품이 내려갔거나 링크가 만료된 페이지. 이걸 그냥 넘기면 메뉴와 푸터만 보고
#: 있지도 않은 상품의 글을 지어내게 된다.
DEAD_PAGE_MARKERS = (
    "상품이 존재하지 않습니다",
    "존재하지 않는 상품",
    "상품을 찾을 수 없",
    "삭제되었거나",
    "판매가 중지",
    "판매중지된 상품",
    "페이지를 찾을 수 없",
    "요청하신 페이지를 찾을 수 없",
    "잘못된 접근",
    "access denied",
    "page not found",
)

#: og:title 이 사이트 이름으로 채워져 있는 경우가 있어, 상품명으로 쓰면 안 된다.
GENERIC_TITLES = (
    "네이버 브랜드 커넥트", "네이버플러스 스토어", "네이버쇼핑", "네이버 쇼핑",
    "스마트스토어", "쿠팡", "11번가", "g마켓", "옥션", "쇼핑", "상품상세", "상품 상세",
)


class ProductUnavailable(RuntimeError):
    """상품 페이지가 죽었거나 상품 정보를 얻지 못했을 때."""


def fetch(url: str, *, verify_ssl: bool = True, proxies: dict | None = None) -> Product:
    html = _fetch_static(url, verify_ssl=verify_ssl, proxies=proxies)
    product = _parse(url, html) if html else Product(url=url)

    if len(product.body_text) < MIN_TEXT_LENGTH:
        rendered, final_url = _fetch_rendered(url)
        if rendered:
            product = _merge(product, _parse(url, rendered))
        product.resolved_url = final_url

    _reject_dead_page(product)
    return product


def _reject_dead_page(product: Product) -> None:
    """'상품이 존재하지 않습니다' 류의 안내가 떠 있으면 거기서 멈춘다.

    안내문은 페이지 맨 앞에 뜨므로 본문 앞부분만 본다. 뒤쪽 후기까지 뒤지면
    "찾을 수 없었는데" 같은 평범한 문장에 걸려 멀쩡한 상품을 막게 된다.
    """
    haystack = f"{product.title}\n{product.body_text[:800]}".lower()
    for marker in DEAD_PAGE_MARKERS:
        if marker in haystack:
            where = product.resolved_url or product.page_url or product.url
            raise ProductUnavailable(
                f"상품 페이지가 열리지 않습니다. 페이지에 '{marker}' 안내가 떠 있습니다.\n"
                f"  들어간 주소: {where}\n"
                "  링크가 만료되었거나 상품이 내려간 것 같습니다. 판매 페이지를 직접 열어 "
                "확인한 뒤 유효한 링크로 다시 실행하세요."
            )


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


def _fetch_rendered(url: str) -> tuple[str, str]:
    """렌더링한 HTML 과, 리다이렉트를 다 따라간 최종 주소를 함께 돌려준다."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "", ""

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
            html, final_url = page.content(), page.url
            browser.close()
            return html, final_url
    except Exception:
        return "", ""


def _parse(url: str, html: str) -> Product:
    soup = BeautifulSoup(html, "lxml")
    product = Product(url=url)

    def meta(*names: str) -> str:
        for name in names:
            tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
            if tag and tag.get("content"):
                return tag["content"].strip()
        return ""

    product.title = _pick_title(
        meta("og:title", "twitter:title"),
        soup.title.get_text(strip=True) if soup.title else "",
    )
    product.description = meta("og:description", "description", "twitter:description")
    product.site_name = meta("og:site_name")
    product.price = meta("product:price:amount", "og:price:amount")

    for tag in soup.find_all("meta", attrs={"property": "og:image"}):
        if tag.get("content"):
            product.thumbnail_urls.append(urljoin(url, tag["content"]))

    _apply_json_ld(soup, product, url)
    product.specs = _extract_specs(soup)

    for junk in soup(["script", "style", "noscript", "header", "footer", "nav", "iframe", "svg"]):
        junk.decompose()
    text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True))
    product.body_text = _denoise(re.sub(r"[ \t]{2,}", " ", text))

    return product


def _denoise(text: str) -> str:
    """전역 메뉴와 UI 라벨을 걷어내, 모델에게 상품 내용만 보낸다.

    메뉴는 '짧은 줄이 여러 개 연달아' 나오는 모양이고 상품 설명은 긴 문장이다.
    그 차이만으로도 대부분 걸러진다. 'A : B' 꼴은 사양일 수 있어 살려둔다.
    """
    lines = [l.strip() for l in text.split("\n")]

    def is_menu_ish(line: str) -> bool:
        if not line or len(line) > SHORT_LINE:
            return False
        return ":" not in line and "：" not in line

    kept: list[str] = []
    run: list[str] = []
    for line in [*lines, "\x00"]:  # 보초값으로 마지막 덩어리까지 처리한다
        if is_menu_ish(line):
            run.append(line)
            continue
        if len(run) < MENU_RUN:
            kept.extend(run)
        run = []
        if line != "\x00":
            kept.append(line)

    # 같은 라벨이 반복되는 것(대표이미지 x9 등)도 내용이 아니다.
    seen: dict[str, int] = {}
    result: list[str] = []
    for line in kept:
        if not line:
            continue
        seen[line] = seen.get(line, 0) + 1
        if len(line) > SHORT_LINE or seen[line] <= 2:
            result.append(line)

    return re.sub(r"\n{3,}", "\n\n", "\n".join(result)).strip()


def _pick_title(*candidates: str) -> str:
    """사이트 이름이 og:title 로 들어오는 경우가 있어, 상품명다운 것을 고른다."""
    cleaned = [t for t in (_clean_title(c) for c in candidates) if t and not _is_generic_title(t)]
    return cleaned[0] if cleaned else next((_clean_title(c) for c in candidates if c.strip()), "")


def _clean_title(raw: str) -> str:
    title = re.sub(r"\s*[:|]\s*[^:|]{1,30}$", "", (raw or "").strip())  # " : 판매처" 꼬리 제거
    title = re.sub(r"^\s*[\[(][^\])]{1,20}[\])]\s*", "", title)  # "[추석세일]" 머리 제거
    return title.strip()


def _is_generic_title(title: str) -> bool:
    lowered = (title or "").lower()
    return not lowered or any(g in lowered for g in GENERIC_TITLES)


def _extract_specs(soup: BeautifulSoup) -> dict[str, str]:
    """상품 고시정보나 사양표는 보통 th/td 또는 dt/dd 쌍으로 되어 있다."""
    specs: dict[str, str] = {}

    def add(k: str, v: str) -> None:
        if k and v and len(k) < 40 and len(v) < 200 and not _is_seller_info(k):
            specs.setdefault(k, v)

    for row in soup.find_all("tr"):
        key = row.find("th")
        value = row.find("td")
        if key and value:
            add(key.get_text(" ", strip=True), value.get_text(" ", strip=True))

    for dl in soup.find_all("dl"):
        terms = dl.find_all("dt")
        definitions = dl.find_all("dd")
        for term, definition in zip(terms, definitions):
            add(term.get_text(" ", strip=True), definition.get_text(" ", strip=True))

    return dict(list(specs.items())[:40])


def _is_seller_info(key: str) -> bool:
    """상품 사양이 아니라 판매자·배송·환불 안내인 항목을 걸러낸다."""
    lowered = key.lower()
    return any(word in lowered for word in SELLER_INFO_KEYS)


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
                    product.thumbnail_urls.append(urljoin(url, img))


def _merge(base: Product, extra: Product) -> Product:
    for attr in ("title", "description", "price", "brand", "site_name"):
        if not getattr(base, attr):
            setattr(base, attr, getattr(extra, attr))

    # 정적 요청에서는 og:title 이 사이트 이름뿐인 경우가 있다. 렌더링한 쪽에 진짜
    # 상품명이 있으면 그걸 쓴다.
    if extra.title and _is_generic_title(base.title) and not _is_generic_title(extra.title):
        base.title = extra.title
    if len(extra.body_text) > len(base.body_text):
        base.body_text = extra.body_text
    if not base.specs:
        base.specs = extra.specs

    seen = set(base.thumbnail_urls)
    base.thumbnail_urls.extend(u for u in extra.thumbnail_urls if u not in seen and not seen.add(u))
    return base
