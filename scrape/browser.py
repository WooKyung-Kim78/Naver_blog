"""네이버용 Playwright 컨텍스트.

블로그 발행에 쓰는 로그인 세션이 있으면 상품 페이지에도 그대로 쓴다.
세션이 있을 때 스마트스토어가 로그인으로 튕기는 일이 줄어든다.
"""

from __future__ import annotations

from pathlib import Path

from config import SESSION_FILE

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

STEALTH = (
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    "Object.defineProperty(navigator, 'languages', {get: () => ['ko-KR', 'ko']});"
    "Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});"
)


def new_context(playwright, *, headed: bool = False):
    """chromium + 컨텍스트를 연다. 호출한 쪽이 browser.close() 한다."""
    browser = playwright.chromium.launch(
        headless=not headed,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    )
    kwargs: dict = {
        "user_agent": UA,
        "locale": "ko-KR",
        "timezone_id": "Asia/Seoul",
        "viewport": {"width": 1440, "height": 950},
        "extra_http_headers": {"Accept-Language": "ko-KR,ko;q=0.9"},
    }
    if SESSION_FILE.exists():
        kwargs["storage_state"] = str(SESSION_FILE)
    context = browser.new_context(**kwargs)
    context.add_init_script(STEALTH)
    return browser, context


def session_path() -> Path:
    return SESSION_FILE
