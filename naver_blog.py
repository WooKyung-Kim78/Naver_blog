"""Playwright 로 네이버 블로그에 글을 작성한다.

네이버 블로그 글쓰기 공식 API 는 종료되어 브라우저 자동화만 가능하다.
네이버는 자동 입력을 탐지하므로 본문은 키보드 타이핑 대신 클립보드 붙여넣기로 넣는다.

스마트에디터 ONE 의 CSS 클래스는 수시로 바뀌기 때문에, 선택자마다 여러 후보를
순서대로 시도하고 모두 실패하면 어떤 후보를 시도했는지 알려준다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import pyperclip
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import FrameLocator, Page, TimeoutError as PlaywrightTimeout, sync_playwright

from config import SESSION_FILE, STORAGE_DIR, NaverConfig

LOGIN_URL = "https://nid.naver.com/nidlogin.login?mode=form&url=https%3A%2F%2Fwww.naver.com"

# 네이버 로그인 페이지는 반응형이라 가로(row)/세로(column) 두 벌의 버튼이 함께 존재하고
# 화면 폭에 따라 한쪽만 보인다. 패스키 버튼과 헷갈리지 않도록 id 를 먼저 시도한다.
LOGIN_BUTTON_SELECTORS = [
    "#loginBtn_row",
    "#loginBtn_column",
    "#log\\.login",
    "button.btn_done:has-text('로그인')",
]

TITLE_SELECTORS = [
    ".se-section-documentTitle .se-text-paragraph",
    ".se-documentTitle .se-text-paragraph",
    ".se-placeholder.__se_placeholder",
]
BODY_SELECTORS = [
    ".se-component.se-text .se-text-paragraph",
    ".se-section-text .se-text-paragraph",
]
IMAGE_BUTTON_SELECTORS = [
    "button.se-image-toolbar-button",
    "button[data-name='image']",
    ".se-toolbar-item-image button",
]
CLOSE_POPUP_SELECTORS = [
    ".se-popup-button-cancel",
    ".se-popup-button-close",
    "button.se-help-panel-close-button",
    ".se-help-panel-close-button",
]


class NaverBlogError(RuntimeError):
    pass


@dataclass
class PostBlock:
    kind: str  # "text" 또는 "image"
    value: str  # 텍스트 내용 또는 이미지 파일 경로


class NaverBlogPublisher:
    def __init__(self, cfg: NaverConfig, *, debug: bool = False):
        cfg.validate()
        self.cfg = cfg
        self.debug = debug
        self._pw = None
        self._browser = None
        self._context = None
        self.page: Page | None = None

    def __enter__(self) -> "NaverBlogPublisher":
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=not self.cfg.headed,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        self._context = self._browser.new_context(
            storage_state=str(SESSION_FILE) if SESSION_FILE.exists() else None,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            viewport={"width": 1440, "height": 950},
        )
        self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        self.page = self._context.new_page()
        self.page.set_default_timeout(30_000)
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self._context:
                self._context.storage_state(path=str(SESSION_FILE))
        except PlaywrightError:
            pass
        for closer in (self._context, self._browser):
            try:
                closer and closer.close()
            except PlaywrightError:
                pass
        if self._pw:
            self._pw.stop()

    # ------------------------------------------------------------------ 로그인

    def ensure_login(self) -> None:
        if self._is_logged_in():
            return
        self._login_with_credentials()
        if not self._is_logged_in():
            raise NaverBlogError(
                "네이버 로그인에 실패했습니다. 캡차가 떴거나 2단계 인증이 걸렸을 수 있습니다.\n"
                "BROWSER_HEADED=true 로 두고 다시 실행한 뒤, 창이 뜨면 직접 로그인을 완료해 주세요."
            )

    def _is_logged_in(self) -> bool:
        self.page.goto("https://www.naver.com", wait_until="domcontentloaded")
        names = {c["name"] for c in self._context.cookies()}
        return "NID_AUT" in names and "NID_SES" in names

    def _login_with_credentials(self) -> None:
        page = self.page
        page.goto(LOGIN_URL, wait_until="domcontentloaded")

        self._paste_into(page, "#id", self.cfg.user_id)
        self._paste_into(page, "#pw", self.cfg.password)

        try:
            self._click_first(page, LOGIN_BUTTON_SELECTORS, "로그인 버튼")
        except NaverBlogError:
            page.locator("#pw").press("Enter")
        page.wait_for_timeout(5000)

        if "deviceConfirm" in page.url:
            try:
                page.locator("#new\\.dontsave").click(timeout=5000)
            except PlaywrightTimeout:
                pass
            page.wait_for_timeout(2000)

        if "nidlogin" in page.url and self.cfg.headed:
            print("\n  [!] 캡차 또는 추가 인증이 감지되었습니다.")
            print("      브라우저 창에서 직접 로그인을 완료한 뒤 이 창에서 Enter 를 눌러주세요.")
            input("      완료했으면 Enter > ")

        self._snap("after_login")

    @staticmethod
    def _paste_into(page: Page, selector: str, value: str) -> None:
        """네이버의 자동 입력 탐지를 피하려면 타이핑이 아닌 클립보드 붙여넣기를 써야 한다."""
        pyperclip.copy(value)
        page.click(selector)
        page.keyboard.press("Control+V")
        page.wait_for_timeout(400)

    # ------------------------------------------------------------------ 글쓰기

    def publish(self, title: str, blocks: list[PostBlock], tags: list[str]) -> str:
        page = self.page
        page.goto(f"https://blog.naver.com/{self.cfg.blog_id}?Redirect=Write&", wait_until="domcontentloaded")
        page.wait_for_timeout(5000)

        frame = page.frame_locator("#mainFrame")
        self._dismiss_popups(frame)
        self._snap("editor_opened")

        self._click_first(frame, TITLE_SELECTORS, "제목 입력란")
        self._paste_text(page, title)
        page.wait_for_timeout(500)

        self._click_first(frame, BODY_SELECTORS, "본문 입력란")
        page.wait_for_timeout(500)

        for block in blocks:
            if block.kind == "image":
                self._insert_image(page, frame, Path(block.value))
            else:
                self._paste_text(page, block.value)
                page.keyboard.press("Enter")
            page.wait_for_timeout(700)

        self._snap("content_filled")

        if self.cfg.post_mode == "draft":
            return self._save_draft(page, frame)
        return self._publish_now(page, frame, tags)

    def _dismiss_popups(self, frame: FrameLocator) -> None:
        """작성 중이던 글 복구 알림, 도움말 패널 등을 닫는다."""
        for selector in CLOSE_POPUP_SELECTORS:
            try:
                element = frame.locator(selector).first
                if element.is_visible(timeout=2500):
                    element.click()
                    self.page.wait_for_timeout(800)
            except (PlaywrightTimeout, PlaywrightError):
                continue

    def _paste_text(self, page: Page, text: str) -> None:
        pyperclip.copy(text)
        page.keyboard.press("Control+V")
        page.wait_for_timeout(500)

    def _insert_image(self, page: Page, frame: FrameLocator, path: Path) -> None:
        if not path.exists():
            print(f"  [!] 이미지 파일을 찾을 수 없어 건너뜁니다: {path}")
            return
        try:
            with page.expect_file_chooser(timeout=15_000) as chooser:
                self._click_first(frame, IMAGE_BUTTON_SELECTORS, "사진 첨부 버튼")
            chooser.value.set_files(str(path))
            page.wait_for_timeout(4000)
        except (PlaywrightTimeout, NaverBlogError) as exc:
            print(f"  [!] 이미지 삽입 실패({path.name}): {exc}")

    def _save_draft(self, page: Page, frame: FrameLocator) -> str:
        self._click_first(
            frame,
            ["button:has-text('저장')", ".save_btn__bzc5B", "button.save_btn__bzc5B"],
            "임시저장 버튼",
        )
        page.wait_for_timeout(4000)
        self._snap("draft_saved")
        return "임시저장 완료. 네이버 블로그 글쓰기 화면에서 내용을 확인한 뒤 직접 발행하세요."

    def _publish_now(self, page: Page, frame: FrameLocator, tags: list[str]) -> str:
        self._click_first(
            frame,
            ["button:has-text('발행')", ".publish_btn__m9KHH", "[data-testid='publishButton']"],
            "발행 버튼",
        )
        page.wait_for_timeout(2500)

        if self.cfg.category:
            self._select_category(page, frame, self.cfg.category)

        if tags:
            try:
                tag_input = frame.locator("#tag-input, input.tag_input__rvUB5").first
                tag_input.click(timeout=8000)
                for tag in tags:
                    self._paste_text(page, tag)
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(400)
            except (PlaywrightTimeout, PlaywrightError) as exc:
                print(f"  [!] 태그 입력 실패(본문은 정상): {exc}")

        self._snap("publish_dialog")

        self._click_first(
            frame,
            [
                ".confirm_btn__WEaBq",
                "button.confirm_btn__WEaBq",
                ".layer_btn_area button:has-text('발행')",
                "button:has-text('발행')",
            ],
            "최종 발행 버튼",
        )
        page.wait_for_timeout(7000)
        self._snap("published")
        return f"발행 완료: https://blog.naver.com/{self.cfg.blog_id}"

    def _select_category(self, page: Page, frame: FrameLocator, category: str) -> None:
        try:
            frame.locator(".selectbox_button__jb1Dt, button.selectbox_button__jb1Dt").first.click(timeout=8000)
            page.wait_for_timeout(1000)
            frame.locator(f"label:has-text('{category}'), span:has-text('{category}')").first.click(timeout=8000)
            page.wait_for_timeout(800)
        except (PlaywrightTimeout, PlaywrightError) as exc:
            print(f"  [!] 카테고리 '{category}' 선택 실패, 기본 카테고리로 발행합니다: {exc}")

    # ------------------------------------------------------------------ 유틸

    def _click_first(self, scope: FrameLocator | Page, selectors: list[str], label: str) -> None:
        for selector in selectors:
            try:
                # 네이버는 반응형 레이아웃 때문에 같은 id 의 숨겨진 요소를 함께 두는 경우가
                # 있으므로, 보이는 요소만 고른다.
                element = scope.locator(f"{selector} >> visible=true").first
                element.wait_for(state="visible", timeout=6000)
                element.click()
                return
            except (PlaywrightTimeout, PlaywrightError):
                continue
        self._snap(f"fail_{label}")
        raise NaverBlogError(
            f"{label}을(를) 찾지 못했습니다. 네이버 에디터가 개편되었을 수 있습니다.\n"
            f"  시도한 선택자: {selectors}\n"
            f"  storage/ 폴더의 스크린샷을 확인하고 naver_blog.py 의 선택자 목록을 갱신하세요."
        )

    def _snap(self, name: str) -> None:
        if not self.debug or not self.page:
            return
        shot_dir = STORAGE_DIR / "screenshots"
        shot_dir.mkdir(exist_ok=True)
        try:
            self.page.screenshot(path=str(shot_dir / f"{int(time.time())}_{name}.png"), full_page=True)
        except PlaywrightError:
            pass
