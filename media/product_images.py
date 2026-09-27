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

#: 상세설명 안에서는 파일명에 banner·event 가 들어가도 본문 이미지인 경우가 흔하다
#: (예: 260401_BB3LSDU001_banner.jpg). 여기서는 UI 부품만 걸러낸다.
JUNK_PATTERNS_DETAIL = re.compile(
    r"(logo|icon|btn_|button|sprite|blank|spacer|dummy|avatar|profile)", re.I
)

MIN_EDGE = 300  # 가로세로 중 하나라도 이 값보다 작으면 본문용으로 못 쓴다

#: 대표 이미지 갤러리는 보통 5~10장이다. 앞의 몇 장만 남기면 색상·디테일 컷이 통째로 빠진다.
MAX_THUMBNAILS = 10

# 상세설명 영역 밖의 길쭉한 이미지는 배너일 확률이 높다. 반면 상세설명 안에서는
# 여러 장을 한 장으로 이어붙인 이미지(848x54000 같은)가 정상이라 제한을 두지 않는다.
# 지나치게 긴 이미지는 evaluator 가 패널로 잘라 쓴다.
MAX_ASPECT = 4.0

# 스마트스토어 DOM 클래스명이 자주 바뀐다. 고정 셀렉터 + 부분 일치로 잡는다.
_DETAIL_CONTAINERS = ", ".join(
    (
        ".se-main-container",
        ".se-module-image",
        ".se-viewer",
        ".detail_area",
        "#productDetail",
        ".product_detail",
        ".product_detail_area",
        "[class*='product_detail']",
        "[class*='ProductDetail']",
        "[class*='detail_content']",
        "[class*='DetailContent']",
        "[class*='detailContent']",
        "[id*='productDetail']",
        "[id*='ProductDetail']",
        "[class*='se-component']",
    )
)

# 상세설명 이미지는 지연 로딩 자리표시자(data: URI)를 src 에 달고 실제 주소를 data-* 에
# 숨겨둔다. src 만 보면 전부 걸러지므로 data-* 를 먼저 확인한다. 이때 naturalWidth 는
# 자리표시자 크기라 믿을 수 없어 0 으로 두고, 실제 크기는 내려받은 뒤에 판단한다.
_COLLECT_JS = (
    """
() => {
  const DETAIL = `%s`;
  const pick = (img) => {
    const candidates = [
      img.dataset.src, img.dataset.original, img.dataset.lazySrc,
      img.getAttribute('data-lazy-src'), img.getAttribute('data-original-src'),
      img.getAttribute('data-src'), img.currentSrc, img.src,
    ];
    for (const c of candidates) {
      if (c && !c.startsWith('data:')) return c;
    }
    return '';
  };
  const looksDetailUrl = (src) => {
    // 대표/썸네일형 type=f 파라미터나 작은 리사이즈가 아닌 본문 CDN 이미지
    if (/shop-phinf|shopping-phinf|shopkeep|contents\\.cloud|phinf\\.pstatic/.test(src)) {
      if (/[?&]type=f\\d+/i.test(src)) return false;
      if (/thumbnail|thumb|profile/i.test(src)) return false;
      return true;
    }
    return false;
  };
  const out = [];
  for (const img of document.querySelectorAll('img')) {
    const src = pick(img);
    if (!src) continue;
    const loaded = (img.currentSrc === src || img.src === src);
    const w = loaded ? (img.naturalWidth || 0) : 0;
    const h = loaded ? (img.naturalHeight || 0) : 0;
    const inBox = !!img.closest(DETAIL);
    // 컨테이너를 못 잡아도 세로로 긴 본문형·아직 안 뜬 CDN 이미지는 상세로 본다.
    const tallBody = w >= 600 && h >= w * 1.2;
    const lazyDetail = w === 0 && h === 0 && looksDetailUrl(src);
    out.push({
      src,
      width: w,
      height: h,
      alt: img.alt || '',
      inDetail: inBox || tallBody || lazyDetail,
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

    try:
        from scrape.browser import new_context

        with sync_playwright() as p:
            browser, context = new_context(p)
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(3000)
            found = collect_from_page(page, url, max_detail=max_detail)
            browser.close()
            return found
    except Exception:
        return [], []


def collect_from_page(
    page, url: str, *, max_detail: int = 12
) -> tuple[list[ImageAsset], list[ImageAsset]]:
    """이미 열려 있는 페이지에서 이미지를 모은다.

    사용자가 직접 로그인·캡차를 통과시킨 창(scrape/manual.py)에서도 같은 절차를
    쓰기 때문에, 브라우저를 여는 일과 긁는 일을 나눠 둔다.
    """
    raw: list[dict] = []
    og_images: list[str] = []

    try:
        from scrape import naver_state
        from scrape.detail_ui import expand_collapsed_detail, open_detail_tab

        state = naver_state.read_from_page(page)
        if state.get("representativeImageUrl"):
            og_images.append(str(state["representativeImageUrl"]))
        og_images.extend(str(u) for u in (state.get("optionalImageUrls") or []) if u)

        open_detail_tab(page)
        expand_collapsed_detail(page)
        _scroll_to_bottom(page)

        raw = page.evaluate(_COLLECT_JS) or []
        og_images += page.evaluate(
            "() => Array.from(document.querySelectorAll('meta[property=\"og:image\"]'))"
            ".map(m => m.content).filter(Boolean)"
        ) or []
    except Exception:
        pass  # 도중에 막혀도 그때까지 모은 것은 살린다

    thumbnails: list[ImageAsset] = []
    seen: set[str] = set()
    for src in og_images:
        absolute = urljoin(url, src)
        key = _dedupe_key(absolute)
        if key in seen:
            continue
        seen.add(key)
        thumbnails.append(ImageAsset(source="thumbnail", url=absolute))
        if len(thumbnails) >= MAX_THUMBNAILS:
            break

    details: list[ImageAsset] = []
    for item in raw:
        src = urljoin(url, str(item.get("src", "")))
        key = _dedupe_key(src)
        if key in seen or not _is_usable(src, item):
            continue
        seen.add(key)
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


def _scroll_to_bottom(page, steps: int = 14) -> None:
    """끝까지 훑되, 페이지가 오류 화면으로 바뀌면 즉시 멈춘다.

    네이버가 접속을 제한하는 동안에는 스크롤이 유발한 지연 로딩 요청이 실패하면서
    페이지 전체가 '상품이 존재하지 않습니다' 로 갈아치워진다. 계속 스크롤하면
    그때까지 잡아둔 이미지까지 날아간다.

    펼쳐 보기 버튼은 여러 개일 수 있고 스크롤·펼침 후에야 나타나기도 해서,
    매 스텝마다 보이는 버튼을 모두 누른다.
    """
    from scrape.detail_ui import expand_collapsed_detail

    for _ in range(steps):
        expand_collapsed_detail(page)
        page.mouse.wheel(0, 2200)
        page.wait_for_timeout(700)
        if _page_died(page):
            return
    page.wait_for_timeout(1500)


def _page_died(page) -> bool:
    try:
        title = page.title() or ""
        if any(m in title for m in ("상품이 존재하지 않습니다", "에러", "오류")):
            return True
        body = page.locator("body").inner_text(timeout=1000)[:500]
        return any(
            m in body
            for m in ("현재 서비스 접속이 불가", "상품이 존재하지 않습니다", "시스템 오류")
        )
    except Exception:
        return False


def _dedupe_key(src: str) -> str:
    """같은 원본을 크기만 바꿔 쓰는 경우가 많아 리사이즈 파라미터는 떼고 비교한다."""
    return src.split("?", 1)[0]


def _is_usable(src: str, item: dict) -> bool:
    width = int(item.get("width") or 0)
    height = int(item.get("height") or 0)
    in_detail = bool(item.get("inDetail"))

    junk = JUNK_PATTERNS_DETAIL if in_detail else JUNK_PATTERNS
    if junk.search(urlparse(src).path):
        return False

    # 지연 로딩 이미지는 브라우저에서 크기를 알 수 없다. 일단 통과시키고
    # 내려받은 뒤 evaluator 가 실제 해상도로 거른다.
    if width == 0 and height == 0:
        return in_detail

    if width < MIN_EDGE or height < MIN_EDGE:
        return False

    if in_detail:
        return True  # 이어붙인 긴 상세 이미지는 evaluator 가 패널로 나눈다

    if height > width * 1.5 and width < 500:
        return False  # 좁고 긴 사이드 배너

    longer, shorter = max(width, height), min(width, height)
    return shorter > 0 and longer / shorter <= MAX_ASPECT
