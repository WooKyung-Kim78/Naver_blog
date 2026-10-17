"""Playwright 로 티스토리에 글을 올린다.

티스토리 Open API 는 종료되었다. 네이버와 같은 원고를 HTML 로 바꿔 넣고, 사진은 글쓰기
화면이 쓰는 첨부 주소로 올린다. TISTORY_CONTENT=link 이면 예전처럼 네이버 글 링크만 넣는다.
본문은 글쓰기 화면의 HTML 모드에 넣고, 그 모드 그대로 발행한다.
로그인 세션은 storage/tistory_session.json 에 저장한다. 네이버 세션과는 별개다.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import re
import time
from html import escape
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout, sync_playwright

from config import STORAGE_DIR, TISTORY_SESSION_FILE, TistoryConfig

_POST_URL = re.compile(r"https?://[^\"'\\s<>]+tistory\.com/\d+")


class TistoryError(RuntimeError):
    pass


class TistoryDailyLimit(TistoryError):
    """하루 공개 발행 한도(15개)에 걸렸다. 내일 다시 올리면 된다."""


def _is_daily_limit(text: str) -> bool:
    return "최대" in text and "공개 발행" in text


class TistoryPublisher:
    def __init__(self, cfg: TistoryConfig, *, debug: bool = False):
        cfg.validate()
        self.cfg = cfg
        self.debug = debug
        self._pw = None
        self._browser = None
        self._context = None
        self.page: Page | None = None
        self._logged_in = False
        self._post_url = ""
        self._tags_filled = False
        self._capture_post = False
        self._alerts: list[str] = []
        self._post_error = ""

    def __enter__(self) -> "TistoryPublisher":
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
            storage_state=str(TISTORY_SESSION_FILE) if TISTORY_SESSION_FILE.exists() else None,
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
        self.page.on("dialog", self._on_dialog)
        self.page.on("response", self._on_response)
        return self

    def __exit__(self, exc_type, *exc) -> None:
        self._persist_session()
        if self.cfg.headed and exc_type and self.page and not self.page.is_closed():
            print(f"\n  현재 주소: {self.page.url}")
            print("  브라우저 창을 확인한 뒤 Enter 를 누르면 닫습니다.")
            try:
                input("  Enter > ")
            except EOFError:
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
        """관리 화면이 열려야 로그인한 것이다. 쿠키만 보고 넘어가지 않는다."""
        page = self.page
        self._goto_admin()
        if self._admin_open():
            self._mark_logged_in()
            return

        if not self.cfg.headed:
            raise TistoryError(
                "티스토리 로그인 세션이 없습니다. BROWSER_HEADED=true 로 창을 띄운 뒤\n"
                "python main.py tistory-login 을 실행해 카카오 로그인을 한 번 끝내세요."
            )

        print("\n  티스토리 로그인이 필요합니다. 카카오 계정으로 로그인해 주세요.")
        page.goto("https://www.tistory.com/auth/login", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        self._click_kakao_login()
        print("  창에서 로그인을 끝낸 뒤, 여기로 돌아와 Enter 를 누르세요.")
        try:
            input("  Enter > ")
        except EOFError:
            pass

        self._adopt_newest_page()
        self._goto_admin()
        if not self._admin_open():
            raise TistoryError(
                "티스토리 로그인에 실패했습니다. 관리 글 목록이 열리지 않았습니다.\n"
                f"  현재 주소: {self.page.url}\n"
                "카카오 로그인과 2단계 인증을 끝낸 뒤 python main.py tistory-login 을 다시 실행하세요."
            )
        self._mark_logged_in()

    def _goto_admin(self) -> None:
        """로그인 직후 리다이렉트가 끝나지 않아 이동이 끊기면 기다렸다가 다시 연다."""
        url = f"{self.cfg.home}/manage/posts/"
        for attempt in range(3):
            try:
                self.page.goto(url, wait_until="domcontentloaded")
                break
            except PlaywrightError as exc:
                if "interrupted by another navigation" not in str(exc) or attempt == 2:
                    raise
                try:
                    self.page.wait_for_load_state("load", timeout=10_000)
                except PlaywrightError:
                    pass
                self.page.wait_for_timeout(1500)
        self.page.wait_for_timeout(2000)

    def _click_kakao_login(self) -> None:
        for selector in (
            "a:has-text('카카오계정으로 로그인')",
            "button:has-text('카카오계정으로 로그인')",
            "a:has-text('카카오계정으로 시작하기')",
            "button:has-text('카카오계정으로 시작하기')",
        ):
            try:
                element = self.page.locator(selector).first
                if element.is_visible(timeout=1500):
                    element.click()
                    self.page.wait_for_timeout(1500)
                    return
            except (PlaywrightTimeout, PlaywrightError):
                continue

    def _admin_open(self) -> bool:
        url = (self.page.url or "").lower()
        if any(token in url for token in ("auth/login", "accounts.kakao.com", "nidlogin")):
            return False
        return "/manage" in url and self.cfg.blog_name.lower() in url

    def _mark_logged_in(self) -> None:
        self._logged_in = True
        self._persist_session()

    def _persist_session(self) -> None:
        if not (self._context and self._logged_in):
            return
        try:
            TISTORY_SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
            self._context.storage_state(path=str(TISTORY_SESSION_FILE))
        except PlaywrightError:
            pass

    def _adopt_newest_page(self) -> None:
        if not self._context:
            return
        pages = [p for p in self._context.pages if not p.is_closed()]
        if pages:
            self.page = pages[-1]

    # ------------------------------------------------------------------ 발행

    def publish(self, title: str, link: str, tags: list[str], *, article=None) -> str:
        """글을 올리고 공개 주소(또는 관리 화면 주소)를 돌려준다.

        article 을 주면 네이버와 같은 원고를, 없으면 네이버 글 링크만 넣는다.
        """
        self.ensure_login()
        page = self.page
        write_url = f"{self.cfg.home}/manage/newpost/?type=post&returnURL=/manage/posts/"
        page.goto(write_url, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        self._snap("editor")

        if not self._admin_open() and "newpost" not in (page.url or "") and "/manage/post" not in (page.url or ""):
            raise TistoryError(
                "글쓰기 화면을 열지 못했습니다.\n"
                f"  현재 주소: {page.url}\n"
                "TISTORY_BLOG_NAME 이 본인 블로그 주소와 같은지 확인하세요."
            )

        self._wait_for_editor()
        title = (title or "").strip() or "제목 없음"
        page.get_by_role("textbox", name="제목을 입력하세요").fill(title)

        href = escape(link)
        body = ""
        if article is not None:
            html, images_ok = self._article_body(article)
            body = html if images_ok or not link else ""
        if not body:
            if not link:
                raise TistoryError("티스토리에 넣을 본문도 네이버 글 주소도 없습니다.")
            body = f'<p><a href="{href}" target="_blank" rel="noopener">{href}</a></p>'
        self._set_body(body)
        self._snap("filled")

        category_set = self._select_category(self.cfg.category) if self.cfg.category else True
        self._fill_tags(tags)
        if self.cfg.wants_draft():
            return self._save_draft()
        return self._finish(tags, category_set)

    def _save_draft(self) -> str:
        """글쓰기 화면 아래의 '임시저장' 을 누르고 저장 응답을 기다린다."""
        page = self.page
        button = page.locator("a.action:has-text('임시저장'), button:has-text('임시저장')").first
        try:
            with page.expect_response(
                lambda r: "/manage/drafts" in r.url and r.request.method in ("POST", "PUT"),
                timeout=30_000,
            ) as saved:
                button.click(timeout=8000)
            response = saved.value
        except (PlaywrightTimeout, PlaywrightError) as exc:
            self._snap("no_draft")
            raise TistoryError(f"티스토리 임시저장을 하지 못했습니다.\n  {exc}") from exc
        if response.status != 200:
            self._snap("draft_rejected")
            raise TistoryError(f"티스토리가 임시저장을 거절했습니다. HTTP {response.status}")
        page.wait_for_timeout(1000)
        return f"{self.cfg.home}/manage/posts/  (글쓰기 화면의 '임시저장' 목록에서 확인·발행하세요)"

    def _article_body(self, article) -> tuple[str, bool]:
        """원고를 티스토리 HTML 로 바꾼다. 사진을 하나도 못 올렸으면 두 번째 값이 False 다."""
        from render.html import render_tistory

        uploaded: dict[Path, str] = {}
        failed: list[str] = []

        def image_url(asset) -> str:
            path = asset.path
            if not path or not Path(path).exists():
                return ""
            path = Path(path).resolve()
            if path not in uploaded:
                try:
                    uploaded[path] = self._upload_image(path)
                except TistoryError as exc:
                    uploaded[path] = ""
                    failed.append(f"{path.name}: {exc}")
            if not uploaded[path]:
                return ""
            caption = "" if asset.source == "infographic" else (asset.credit or asset.caption)
            return _image_replacer(uploaded[path], path, caption)

        html = render_tistory(article, image_url)
        if failed:
            print(f"  [!] 티스토리 사진 {len(failed)}장을 올리지 못해 빼고 넣습니다.")
            for line in failed[:3]:
                print(f"      {line}")
        if uploaded and not any(uploaded.values()):
            print("  [!] 사진을 하나도 올리지 못했습니다. 네이버 글 링크가 있으면 링크만 올립니다.")
            return html, False
        return html, True

    def _upload_image(self, path: Path) -> str:
        """글쓰기 화면이 쓰는 첨부 주소로 올리고 kage@키/파일명 을 돌려준다.

        응답의 url 은 달말에 만료되는 서명 주소라 본문에 쓰지 않는다.
        """
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        try:
            result = self.page.evaluate(
                """async ({url, b64, name, mime}) => {
                    const bin = atob(b64);
                    const bytes = new Uint8Array(bin.length);
                    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
                    const form = new FormData();
                    form.append('file', new Blob([bytes], { type: mime }), name);
                    const headers = { Accept: 'application/json, text/plain, */*' };
                    const xsrf = document.cookie.match(/(?:^|; )XSRF-TOKEN=([^;]*)/);
                    if (xsrf) headers['X-XSRF-TOKEN'] = decodeURIComponent(xsrf[1]);
                    const res = await fetch(url, { method: 'POST', body: form, credentials: 'include', headers });
                    return { status: res.status, text: await res.text() };
                }""",
                {
                    "url": f"{self.cfg.home}/manage/post/attach.json",
                    "b64": base64.b64encode(path.read_bytes()).decode("ascii"),
                    "name": path.name,
                    "mime": mime,
                },
            )
        except (PlaywrightError, OSError) as exc:
            raise TistoryError(str(exc)) from exc
        if result.get("status") != 200:
            raise TistoryError(f"HTTP {result.get('status')}")
        try:
            data = json.loads(result.get("text") or "{}")
        except ValueError as exc:
            raise TistoryError("업로드 응답을 읽지 못했습니다.") from exc
        key = str(data.get("key") or "").strip("/")
        filename = str(data.get("filename") or "")
        if not key or not filename or "|" in key + filename:
            raise TistoryError("업로드 응답에 사진 키가 없습니다.")
        return f"kage@{key}/{filename}"

    def _set_body(self, html: str) -> None:
        """HTML 모드의 코드 입력칸에 본문을 넣는다.

        화면에 CodeMirror 가 두 개라, 먼저 잡히는 쪽에 쓰면 발행되는 본문은
        사진만 남는다. 티스토리 HTML 모드 편집기(.cm-s-tistory-html)에만 쓴다.
        """
        self._ensure_html_mode()
        marker = html[:40]
        try:
            placed = self.page.evaluate(
                """({html, marker}) => {
                    const root = document.querySelector('.CodeMirror.cm-s-tistory-html');
                    const cm = root && root.CodeMirror;
                    if (!cm) return 'missing';
                    // setValue 는 에디터 상태(newPost.content)로 전달되지 않아 빈 글로 발행된다.
                    const last = cm.lastLine();
                    cm.replaceRange(html, { line: 0, ch: 0 }, { line: last, ch: cm.getLine(last).length }, '+input');
                    cm.refresh();
                    return (cm.getValue() || '').includes(marker) ? 'codemirror' : 'mismatch';
                }""",
                {"html": html, "marker": marker},
            )
            if placed == "codemirror":
                self.page.wait_for_timeout(800)
                stuck = self.page.evaluate(
                    """(marker) => {
                        const root = document.querySelector('.CodeMirror.cm-s-tistory-html');
                        const cm = root && root.CodeMirror;
                        return !!cm && (cm.getValue() || '').includes(marker);
                    }""",
                    marker,
                )
                placed = "codemirror" if stuck else "reverted"
        except PlaywrightError as exc:
            self._snap("html_set_failed")
            raise TistoryError(
                "HTML 모드 입력칸에 본문을 넣지 못했습니다.\n"
                f"  {exc}"
            ) from exc
        if placed != "codemirror":
            self._snap("no_html_editor")
            raise TistoryError(
                "HTML 모드 본문이 입력칸에 들어가지 않아 발행을 멈췄습니다.\n"
                f"  현재 주소: {self.page.url}"
            )

    def _ensure_html_mode(self) -> None:
        if self._html_editor_ready():
            return
        self._switch_to_html_mode()
        try:
            self.page.wait_for_selector(".CodeMirror.cm-s-tistory-html", state="visible", timeout=12_000)
        except PlaywrightTimeout as exc:
            self._snap("no_html_editor")
            raise TistoryError("HTML 모드 입력칸이 열리지 않았습니다.") from exc

    def _html_editor_ready(self) -> bool:
        # 기본모드에서도 숨은 HTML 입력칸이 미리 만들어져 있어, 보이는지까지 본다.
        try:
            return bool(
                self.page.evaluate(
                    """() => {
                        const root = document.querySelector('.CodeMirror.cm-s-tistory-html');
                        return !!(root && root.CodeMirror && root.offsetParent);
                    }"""
                )
            )
        except PlaywrightError:
            return False

    def _switch_to_html_mode(self) -> None:
        """상단의 기본모드 메뉴에서 HTML 을 고른다.

        모드 버튼은 바깥 div 와 안쪽 button 이 둘 다 '기본모드' 라서,
        역할 이름으로 찾으면 두 개가 잡혀 클릭이 실패한다. id 로 하나만 누른다.
        """
        page = self.page
        opened = False
        for selector in ("#editor-mode-layer-btn-open", "#editor-mode-layer-btn"):
            button = page.locator(selector)
            try:
                if button.count() and button.first.is_visible():
                    button.first.click(timeout=8000)
                    opened = True
                    break
            except (PlaywrightTimeout, PlaywrightError):
                continue
        if not opened:
            self._snap("no_mode_button")
            raise TistoryError("에디터의 '기본모드' 버튼을 찾지 못해 HTML 모드로 바꾸지 못했습니다.")
        page.wait_for_timeout(500)
        try:
            picked = page.evaluate(
                """() => {
                    const norm = (el) => ((el && (el.innerText || el.textContent)) || '')
                        .replace(/\\s+/g, ' ').trim();
                    const items = [...document.querySelectorAll('.mce-menu-item, [role=menuitem], [role=option]')];
                    const item = items.find((el) => norm(el.querySelector('.mce-text') || el) === 'HTML');
                    if (!item) return items.map((el) => norm(el)).filter(Boolean).slice(0, 12);
                    (item.closest('.mce-menu-item') || item).click();
                    return true;
                }"""
            )
        except PlaywrightError as exc:
            self._snap("no_html_menu")
            raise TistoryError(f"HTML 모드 메뉴를 열지 못했습니다.\n  {exc}") from exc
        if picked is not True:
            self._snap("no_html_menu")
            shown = ", ".join(picked) if isinstance(picked, list) and picked else "(메뉴 항목 없음)"
            raise TistoryError(
                "HTML 모드 메뉴를 찾지 못했습니다.\n"
                f"  열린 항목: {shown}"
            )
        self._confirm_mode_change()
        try:
            page.wait_for_selector(".CodeMirror.cm-s-tistory-html", state="attached", timeout=12_000)
        except PlaywrightTimeout:
            pass

    def _confirm_mode_change(self) -> None:
        """모드를 바꾸겠냐는 창이 뜨면 확인한다. 창이 없으면 넘어간다."""
        page = self.page
        for name in ("확인", "변경"):
            try:
                button = page.get_by_role("button", name=name, exact=True).first
                if button.is_visible(timeout=1200):
                    button.click()
                    page.wait_for_timeout(400)
                    return
            except (PlaywrightTimeout, PlaywrightError):
                continue

    def _wait_for_editor(self) -> None:
        try:
            self.page.wait_for_function(
                "() => window.tinymce && (window.tinymce.activeEditor || window.tinymce.editors.length)",
                timeout=20_000,
            )
            self.page.get_by_role("textbox", name="제목을 입력하세요").wait_for(state="visible", timeout=10_000)
        except PlaywrightTimeout as exc:
            self._snap("no_editor")
            raise TistoryError(
                "티스토리 글쓰기 에디터를 찾지 못했습니다.\n"
                f"  현재 주소: {self.page.url}"
            ) from exc

    def _select_category(self, category: str) -> bool:
        page = self.page
        wanted = _norm_category(category)
        try:
            combo = page.get_by_role("combobox", name="카테고리 선택")
            if not combo.count():
                return self._select_native_category(wanted)
            combo.first.click(timeout=8000)
            page.wait_for_timeout(500)
            index = page.evaluate(
                """(wanted) => {
                    const norm = (text) => (text || '').replace(/^[-\\s]+/, '').replace(/\\s+/g, ' ').trim();
                    const opts = [...document.querySelectorAll('[role=option]')];
                    return opts.findIndex(node => norm(node.textContent) === wanted);
                }""",
                wanted,
            )
            if index is None or index < 0:
                page.keyboard.press("Escape")
                return self._select_native_category(wanted)
            page.get_by_role("option").nth(index).click(timeout=8000)
            page.wait_for_timeout(400)
            return True
        except (PlaywrightTimeout, PlaywrightError) as exc:
            print(f"  [!] 카테고리 선택 중 오류: {exc}")
            return self._select_native_category(wanted)

    def _select_native_category(self, wanted: str) -> bool:
        """커스텀 콤보가 아니면 숨은 select 의 option 텍스트로 고른다."""
        page = self.page
        try:
            selects = page.locator("select")
            for index in range(selects.count()):
                select = selects.nth(index)
                labels = select.locator("option").all_inner_texts()
                for label in labels:
                    if _norm_category(re.sub(r"^[-\s]+", "", label)) == wanted:
                        select.select_option(label=label.strip())
                        return True
        except (PlaywrightTimeout, PlaywrightError):
            return False
        return False

    def _category_names(self) -> list[str]:
        try:
            self.page.get_by_role("combobox", name="카테고리 선택").first.click(timeout=4000)
            self.page.wait_for_timeout(400)
            names = self.page.evaluate(
                """() => [...document.querySelectorAll('[role=option]')]
                    .map(node => (node.textContent || '').replace(/^[-\\s]+/, '').trim())
                    .filter(Boolean)"""
            )
            self.page.keyboard.press("Escape")
            return names
        except (PlaywrightTimeout, PlaywrightError):
            return []

    def _fill_tags(self, tags: list[str]) -> None:
        if self._tags_filled:
            return
        clean = []
        for tag in tags:
            text = tag.lstrip("#").replace(",", " ").strip()
            if text and text not in clean:
                clean.append(text)
        if not clean:
            return
        page = self.page
        try:
            box = page.locator(
                "#tagText, input[placeholder*='태그'], textarea[placeholder*='태그']"
            )
            if not box.count():
                box = page.get_by_role("textbox", name="태그")
            # 숨은 입력란이 같이 잡혀서, 보이는 것만 쓴다.
            field = next((box.nth(i) for i in range(box.count()) if box.nth(i).is_visible()), None)
            if field is None:
                return
            for tag in clean[:20]:
                field.click(timeout=5000)
                field.fill(tag)
                page.keyboard.press("Enter")
                page.wait_for_timeout(200)
            self._tags_filled = True
        except (PlaywrightTimeout, PlaywrightError) as exc:
            print(f"  [!] 태그 입력 실패(본문은 유지): {exc}")

    def _finish(self, tags: list[str], category_set: bool) -> str:
        page = self.page
        public = self.cfg.wants_public()
        try:
            page.get_by_role("button", name="완료", exact=True).click(timeout=8000)
            page.wait_for_timeout(800)
        except (PlaywrightTimeout, PlaywrightError) as exc:
            self._snap("no_done")
            raise TistoryError(f"발행 화면을 여는 '완료' 버튼을 찾지 못했습니다.\n  {exc}") from exc

        if self.cfg.category and not category_set:
            category_set = self._select_category(self.cfg.category)
        if self.cfg.category and not category_set:
            options = self._category_names()
            shown = ", ".join(options[:12]) if options else "(목록을 읽지 못함)"
            raise TistoryError(
                f"카테고리 '{self.cfg.category}' 를 찾지 못했습니다.\n"
                f"  글쓰기 화면에 있는 카테고리: {shown}\n"
                "TISTORY_CATEGORY 를 그 이름과 똑같이 맞춘 뒤 다시 올리세요. 글은 아직 발행되지 않았습니다."
            )
        self._fill_tags(tags)
        if tags and not self._tags_filled:
            print("  [!] 태그 입력란을 찾지 못했습니다. 태그는 빼고 발행합니다.")

        radio = "공개" if public else "비공개"
        confirm_names = ("공개 발행", "발행") if public else ("비공개 저장",)
        try:
            page.get_by_role("radio", name=radio, exact=True).click(timeout=8000)
            page.wait_for_timeout(400)
        except (PlaywrightTimeout, PlaywrightError) as exc:
            self._snap("no_visibility")
            raise TistoryError(f"'{radio}' 선택을 찾지 못했습니다.\n  {exc}") from exc

        clicked = False
        self._capture_post = True
        self._alerts.clear()
        for name in confirm_names:
            try:
                page.get_by_role("button", name=name, exact=True).click(timeout=4000)
                clicked = True
                break
            except (PlaywrightTimeout, PlaywrightError):
                continue
        if not clicked:
            self._snap("no_publish")
            raise TistoryError(
                "발행 버튼을 찾지 못했습니다. 화면의 버튼 이름이 바뀌었을 수 있습니다.\n"
                f"  시도한 이름: {', '.join(confirm_names)}"
            )

        self._wait_published()

        url = self._post_url or self._find_public_url()
        if public and url:
            return url
        if public:
            return f"{self.cfg.home}/  (글 목록에서 방금 글을 확인하세요. 구글 반영은 며칠 걸릴 수 있습니다.)"
        return f"비공개로 저장했습니다. 구글에 노출하려면 TISTORY_VISIBILITY=3 으로 다시 올리세요. {url}".strip()

    def _wait_published(self, timeout_s: float = 180) -> None:
        """글 목록으로 넘어가거나 발행 응답이 올 때까지 기다린다.

        글쓰기 주소에도 returnURL=/manage/posts/ 가 들어 있어 주소만 보면 바로 끝난 것처럼 보인다.
        그대로 창을 닫으면 발행 요청이 끊겨 글이 올라가지 않는다.
        """
        page = self.page
        started = time.monotonic()
        deadline = started + timeout_s
        hinted = False
        while time.monotonic() < deadline:
            if not hinted and time.monotonic() - started > 15:
                hinted = True
                print("  [!] 발행이 늦어지고 있습니다. 브라우저 창에 보안문자(캡차)나 안내가 떠 있으면 처리해 주세요.")
            limit = next((m for m in self._alerts if _is_daily_limit(m)), "")
            if limit or _is_daily_limit(self._post_error):
                raise TistoryDailyLimit(
                    f"{limit or '하루에 새롭게 공개 발행할 수 있는 글은 최대 15개까지입니다.'}\n"
                    "티스토리 하루 한도입니다. 글은 올라가지 않았고, 내일 --tistory-only 로 올리면 됩니다."
                )
            failed = next((m for m in self._alerts if "실패" in m or "오류" in m), "")
            if self._post_error and not failed:
                failed = "발행 요청이 거절되었습니다."
            if failed:
                self._snap("publish_rejected")
                page.wait_for_timeout(500)
                detail = f"\n  서버 응답: {self._post_error}" if self._post_error else ""
                raise TistoryError(f"티스토리가 발행을 거절했습니다.\n  {failed}{detail}")
            if self._post_url:
                return
            try:
                if urlparse(page.url).path.startswith("/manage/posts"):
                    page.wait_for_timeout(1500)
                    return
            except PlaywrightError:
                pass
            page.wait_for_timeout(500)
        self._snap("after_publish")
        raise TistoryError(
            f"발행 버튼을 누른 뒤 {int(timeout_s)}초 동안 완료되지 않았습니다.\n"
            "티스토리 글 목록에서 글이 올라갔는지 확인한 뒤, 없으면 --tistory-only 로 다시 올리세요."
        )

    def _find_public_url(self) -> str:
        if self._post_url:
            return self._post_url
        try:
            found = self.page.evaluate(
                """() => {
                    const links = [...document.querySelectorAll('a')].map(a => a.href || '');
                    return links.find(href => /tistory\\.com\\/\\d+/.test(href)) || '';
                }"""
            )
        except PlaywrightError:
            return ""
        return found or ""

    def _on_dialog(self, dialog) -> None:
        message = dialog.message or ""
        if dialog.type == "alert":
            self._alerts.append(message.strip())
        try:
            if "이어서" in message:
                dialog.dismiss()
            else:
                dialog.accept()
        except PlaywrightError:
            pass

    def _on_response(self, response) -> None:
        if not self._capture_post or self._post_url or "post.json" not in response.url:
            return
        if response.request.method != "POST":
            return
        try:
            body = response.text().replace("\\/", "/")
        except PlaywrightError:
            return
        if response.status != 200:
            try:
                reason = str(json.loads(body).get("message") or body)
            except (ValueError, AttributeError):
                reason = body
            self._post_error = f"HTTP {response.status} {reason.strip()[:300]}"
            return
        match = _POST_URL.search(body)
        if match:
            self._post_url = match.group(0)
            return
        id_match = re.search(r'"(?:entryId|postId)"\s*:\s*"?(\d+)"?', body)
        if id_match and int(id_match.group(1)) > 0:
            self._post_url = f"{self.cfg.home}/{id_match.group(1)}"

    def _snap(self, name: str) -> None:
        if not self.debug or not self.page:
            return
        shot_dir = STORAGE_DIR / "screenshots"
        shot_dir.mkdir(exist_ok=True)
        try:
            self.page.screenshot(path=str(shot_dir / f"{int(time.time())}_tistory_{name}.png"), full_page=True)
        except PlaywrightError:
            pass


def _norm_category(name: str) -> str:
    return re.sub(r"\s+", " ", name.replace("\u00a0", " ")).strip()


def _image_replacer(kage: str, path: Path, caption: str) -> str:
    """티스토리 에디터가 본문에 남기는 사진 표기. 발행 때 첨부파일로 등록되고 만료되지 않는다."""
    from PIL import Image

    try:
        with Image.open(path) as img:
            width, height = img.size
    except OSError:
        width, height = 0, 0
    meta = {"originWidth": width, "originHeight": height, "style": "alignCenter", "filename": path.name}
    if caption:
        meta["caption"] = escape(caption.strip(), quote=False).replace("|", "&#124;")
    return f"[##_Image|{kage}|CDM|1.3|{json.dumps(meta, ensure_ascii=False)}_##]"
