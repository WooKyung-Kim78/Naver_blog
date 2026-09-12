"""상품 페이지에서 썸네일과 상세설명 이미지를 긁어온다.

네이버 스마트스토어를 포함한 대부분의 쇼핑몰은 상세설명 이미지를 스크롤할 때 지연
로딩한다. 그래서 정적 HTML 파싱으로는 잡히지 않고, 브라우저로 끝까지 스크롤한 뒤
실제로 렌더된 이미지의 naturalWidth 를 읽어야 한다.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from core.models import ImageAsset

#: 로고, 아이콘, 배송안내 배너 등 콘텐츠 가치가 없는 이미지를 걸러내는 URL 패턴.
JUNK_PATTERNS = re.compile(
    r"(logo|icon|btn_|button|sprite|blank|spacer|dummy|avatar|profile|badge|"
    r"banner|event|coupon|delivery|refund|exchange|notice|cs_|footer|header)",
    re.I,
)

MIN_EDGE = 300  # 가로세로 중 하나라도 이 값보다 작으면 본문용으로 못 쓴다

# 상세설명 영역 밖의 길쭉한 이미지는 배너일 확률이 높다. 반면 상세설명 안에서는
# 세로로 아주 긴 인포그래픽이 정상이므로(860x5000 같은) 훨씬 느슨하게 본다.
MAX_ASPECT = 4.0
MAX_ASPECT_DETAIL = 15.0

_DETAIL_CONTAINERS = ".se-main-container, .se-module-image, .detail_area, #productDetail, .product_detail"

# 상세설명 이미지는 지연 로딩 자리표시자(data: URI)를 src 에 달고 실제 주소를 data-* 에
# 숨겨둔다. src 만 보면 전부 걸러지므로 data-* 를 먼저 확인한다. 이때 naturalWidth 는
# 자리표시자 크기라 믿을 수 없어 0 으로 두고, 실제 크기는 내려받은 뒤에 판단한다.
_COLLECT_JS = (
    """
() => {
  const DETAIL = '%s';
  const pick = (img) => {
    const candidates = [
      img.dataset.src, img.dataset.original, img.dataset.lazySrc,
      img.getAttribute('data-lazy-src'), img.getAttribute('data-original-src'),
      img.currentSrc, img.src,
    ];
    for (const c of candidates) {
      if (c && !c.startsWith('data:')) return c;
    }
    return '';
  };
  const out = [];
  for (const img of document.querySelectorAll('img')) {
    const src = pick(img);
    if (!src) continue;
    const loaded = (img.currentSrc === src || img.src === src);
    out.push({
      src,
      width: loaded ? (img.naturalWidth || 0) : 0,
      height: loaded ? (img.naturalHeight || 0) : 0,
      alt: img.alt || '',
      inDetail: !!img.closest(DETAIL),
    });
  }
  return out;
}
"""
    % _DETAIL_CONTAINERS
)


def collect(url: str, *, max_detail: int = 12) -> tuple[list[ImageAsset], list[ImageAsset]]:
    """(썸네일, 상세이미지) 를 돌려준다. 실패해도 예외를 던지지 않는다."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return [], []

    raw: list[dict] = []
    og_images: list[str] = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
                locale="ko-KR",
                viewport={"width": 1440, "height": 950},
            )
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(3000)

            _open_detail_tab(page)
            _scroll_to_bottom(page)

            raw = page.evaluate(_COLLECT_JS) or []
            og_images = page.evaluate(
                "() => Array.from(document.querySelectorAll('meta[property=\"og:image\"]'))"
                ".map(m => m.content).filter(Boolean)"
            ) or []
            browser.close()
    except Exception:
        return [], []

    thumbnails: list[ImageAsset] = [
        ImageAsset(source="thumbnail", url=urljoin(url, src)) for src in og_images[:3]
    ]

    seen = {a.url for a in thumbnails}
    details: list[ImageAsset] = []
    for item in raw:
        src = urljoin(url, str(item.get("src", "")))
        if src in seen or not _is_usable(src, item):
            continue
        seen.add(src)
        details.append(
            ImageAsset(
                source="detail" if item.get("inDetail") else "thumbnail",
                url=src,
                width=int(item.get("width") or 0),
                height=int(item.get("height") or 0),
                caption=str(item.get("alt") or "").strip()[:100],
            )
        )

    # 큰 이미지가 대체로 본문용이다.
    details.sort(key=lambda a: a.width * a.height, reverse=True)

    extra_thumbs = [a for a in details if a.source == "thumbnail"][:4]
    detail_only = [a for a in details if a.source == "detail"][:max_detail]

    return thumbnails + extra_thumbs, detail_only


def _open_detail_tab(page) -> None:
    """스마트스토어는 상세정보가 탭 뒤에 숨어 있는 경우가 있다."""
    for name in ("상세정보", "상품정보", "상세설명"):
        try:
            tab = page.get_by_role("tab", name=name).first
            if tab.is_visible(timeout=1500):
                tab.click()
                page.wait_for_timeout(1500)
                return
        except Exception:
            continue


def _scroll_to_bottom(page, steps: int = 14) -> None:
    for _ in range(steps):
        page.mouse.wheel(0, 2200)
        page.wait_for_timeout(700)
    page.wait_for_timeout(1500)


def _is_usable(src: str, item: dict) -> bool:
    width = int(item.get("width") or 0)
    height = int(item.get("height") or 0)

    if JUNK_PATTERNS.search(urlparse(src).path):
        return False

    # 지연 로딩 이미지는 브라우저에서 크기를 알 수 없다. 일단 통과시키고
    # 내려받은 뒤 evaluator 가 실제 해상도로 거른다.
    if width == 0 and height == 0:
        return bool(item.get("inDetail"))

    if width < MIN_EDGE or height < MIN_EDGE:
        return False
    if not item.get("inDetail") and height > width * 1.5 and width < 500:
        return False  # 좁고 긴 사이드 배너

    longer, shorter = max(width, height), min(width, height)
    limit = MAX_ASPECT_DETAIL if item.get("inDetail") else MAX_ASPECT
    return shorter > 0 and longer / shorter <= limit
