"""사람이 직접 통과시키는 브라우저 수집.

네이버가 자동 접근을 로그인 화면이나 캡차로 돌려보내면 headless 로는 더 해볼 것이
없다. 이때 창을 띄워 두고 사용자가 직접 로그인·인증을 끝낸 뒤 상품 페이지를 띄워
주면, 그 화면 그대로 본문과 이미지를 읽는다.

통과한 세션은 저장해 두고 다음 실행부터 자동 수집이 다시 쓴다.
"""

from __future__ import annotations

import json
from typing import Callable

from config import SESSION_FILE
from core.models import Product
from scrape import product as product_scraper

#: 네이버 로그인 쿠키. 이게 없는 세션으로 저장 파일을 덮으면 블로그 발행 쪽이
#: 자동 로그인에 실패한다. 로그인까지 마친 창에서만 세션을 남긴다.
LOGIN_COOKIES = {"NID_AUT", "NID_SES"}


class ManualUnavailable(RuntimeError):
    """수동 수집 창을 띄우지 못했을 때."""


class ManualCancelled(ManualUnavailable):
    """사용자가 창에서 그만두겠다고 한 경우. 자동 재시도로 더 붙잡지 않는다."""


def fetch(
    url: str,
    *,
    wait: Callable[[], bool],
    collect_images: bool = True,
    report: Callable[[str], None] = lambda _: None,
) -> Product:
    """창을 띄워 url 을 열고, wait() 가 돌아오면 그 화면에서 상품 정보를 읽는다.

    wait 는 사용자에게 안내를 보여주고 준비될 때까지 막아 두는 콜백이다.
    False 를 돌려주면 수집을 포기한다.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ManualUnavailable(
            "playwright 가 없어 브라우저를 띄울 수 없습니다.\n"
            "  pip install playwright && playwright install chromium"
        ) from exc

    from scrape import naver_state
    from scrape.browser import new_context

    with sync_playwright() as p:
        browser, context = new_context(p, headed=True)
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        except Exception:
            # 주소가 막혀도 창은 살아 있다. 사용자가 직접 찾아 들어가면 된다.
            report("  페이지를 자동으로 열지 못했습니다. 창에서 직접 상품 페이지로 가주세요.")

        if wait() is False:
            browser.close()
            raise ManualCancelled("브라우저에서 직접 수집하기를 중단했습니다.")

        # 사용자가 창에서 다른 주소로 옮겨갔을 수 있으니 지금 화면을 기준으로 삼는다.
        html = _settled_content(page)
        landed = page.url or url
        state = naver_state.read_from_page(page)
        product = product_scraper.parse(landed, html)

        detail_urls: list[str] = []
        thumb_urls: list[str] = []
        if collect_images:
            report("  상세 이미지 수집 중 (창을 닫지 마세요)")
            from media import product_images

            thumbs, details = product_images.collect_from_page(page, landed)
            thumb_urls = [a.url for a in thumbs]
            detail_urls = [a.url for a in details]

            # 이미지 수집이 상세설명을 펼치고 끝까지 스크롤한 뒤라 본문이 더 길다.
            try:
                grown = _settled_content(page)
            except Exception:
                grown = ""
            if grown and not product_scraper.looks_dead(grown):
                product = product_scraper.merge(product, product_scraper.parse(landed, grown))

        if state:
            naver_state.apply(product, state)

        _save_session(context, report)
        browser.close()

    product.url = url
    product.resolved_url = landed

    seen = set(product.thumbnail_urls)
    product.thumbnail_urls.extend(u for u in thumb_urls if u not in seen and not seen.add(u))
    product.detail_image_urls = detail_urls

    return product


def _settled_content(page, attempts: int = 10) -> str:
    """로그인 뒤 리다이렉트처럼 페이지가 아직 이동 중이면 멈출 때까지 기다렸다 읽는다."""
    for _ in range(attempts - 1):
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15_000)
        except Exception:
            pass
        try:
            return page.content()
        except Exception as exc:
            if "navigating" not in str(exc):
                raise
            page.wait_for_timeout(1000)
    return page.content()


def _save_session(context, report: Callable[[str], None]) -> None:
    """사람이 통과시킨 로그인 세션을 남겨 둔다.

    로그인하지 않은 채 상품 페이지만 본 경우에는 저장하지 않는다. 그런 세션으로
    덮으면 블로그 발행 쪽이 쓰던 로그인 상태까지 날아간다.
    """
    try:
        state = context.storage_state()
    except Exception:
        return

    names = {cookie.get("name") for cookie in state.get("cookies") or []}
    if not LOGIN_COOKIES <= names:
        return

    try:
        SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        SESSION_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return
    report("  로그인 세션을 저장했습니다. 다음 실행은 이 상태로 시작합니다.")
