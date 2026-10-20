"""상품 URL -> 유튜브 쇼츠(1080x1920, 9:16) 영상 + 업로드 문구 자동 생성.

네이버 블로그 글처럼 구매 링크(스마트스토어·브랜드커넥트 제휴 링크 등)를 주면 블로그 생성기와 같은 방법으로
상품 페이지·썸네일·상세 이미지를 읽는다. 알리익스프레스 주소는 따로 읽는다.

블로그 생성기와 같은 가상환경과 .env 를 쓴다. 프로젝트 폴더(c:\\Temp\\blog)에서:

    pip install "moviepy>=2" edge-tts numpy pillow requests beautifulsoup4 python-dotenv truststore playwright
    python -m playwright install chromium

사용:
    python shorts/app.py "https://naver.me/구매링크"
    python shorts/app.py "https://구매링크" --page-url "https://smartstore.naver.com/..."   # 링크로 안 읽힐 때
    python shorts/app.py "https://ko.aliexpress.com/item/1005001234567890.html"
    python shorts/app.py URL --title "상품명 직접 지정" --font "C:/Windows/Fonts/malgunbd.ttf"
    python shorts/app.py URL --browser                         # 캡차가 뜨면 창에서 직접 풀기
    python shorts/app.py --images-dir 사진폴더 --title "상품명"   # 크롤링이 막혔을 때
    python shorts/app.py URL --no-ai                           # AI 대본 없이 (사진 순서·원래 상품명 그대로)
    python shorts/app.py URL --no-tts                          # 음성 없이 자막만
    python shorts/app.py URL --videos-dir 영상폴더             # 갖고 있는 상품 영상도 같이 쓰기
    python shorts/app.py URL --bgm music.mp3                   # 배경음악
    python shorts/app.py URL --upload                          # 만든 뒤 네이버(동영상 글)·티스토리에 올리기
    python shorts/app.py --each URL1 URL2 URL3 --upload        # 상품마다 쇼츠 하나씩 순서대로 (실패해도 다음 상품 계속)
    python shorts/app.py --upload-from shorts/output/폴더      # 이미 만든 쇼츠만 올리기
    python shorts/app.py --upload-from shorts/output/폴더 --tistory-only

올릴 때는 네이버 블로그 NAVER_VIDEO_CATEGORY(기본 '비디오로그') 카테고리에 동영상을 첨부한 짧은 상품 소개글을
올리고(쇼핑 커넥트 카드 포함. 대가성 문구는 네이버가 자동으로 붙인다), 검색어 조사로 고른 태그 10개를 넣는다. 이어서 티스토리에도
대표 사진·소개글·네이버 동영상 글 링크·구매 버튼을 올린다. 발행/임시저장은 POST_MODE, TISTORY_POST_MODE 를 따른다.

영상은 늘 20초다. 판매 페이지에서 내려받은 상품 영상과 사진만 쓰고, AI 로 상품 그림이나 영상을 만들지 않는다.
첫 장면 가운데에 큰 두 줄 훅(노랑/하양), 그 아래에 내레이션과 맞춘 테두리 자막, 오른쪽 위에 (광고) 표시가 들어간다.
사진과 영상은 자르거나 늘리지 않고 통째로 보여 주며, 남는 곳은 같은 그림을 흐리게 깔아 채운다.
상품 영상의 원래 소리는 끄고 내레이션만 넣는다.

AI 는 블로그와 같은 .env 의 AI 설정으로 두 번 호출한다.
  1. 웹 검색으로 사람들이 실제로 치는 검색어(메인 키워드·함께 노릴 검색어·해시태그)를 조사한다.
  2. 사진과 영상(앞·중간·뒤 세 컷)을 보고 훅, 장면 순서와 대본, 유튜브 제목·설명·태그·고정 댓글을 쓴다.
대본은 edge-tts(ko-KR-SunHiNeural)로 읽는다. 끝나면 이번 실행에 든 AI 비용을 보여 주고 meta.json 에 남긴다.
업로드할 제목·설명(광고 표기·구매 링크·해시태그)·태그·고정 댓글은 결과 폴더의 youtube.txt 에 남는다.

.env (선택):
    NAVER_VIDEO_CATEGORY=비디오로그
    TISTORY_VIDEO_CATEGORY=                # 비우면 TISTORY_CATEGORY
    SHORTS_DISCLOSURE=[광고] 이 영상에는 제휴 링크가 포함되어 있으며, 링크로 구매하면 수수료를 받습니다.
    SHORTS_FONT=C:/Windows/Fonts/malgunbd.ttf
    SHORTS_OUTPUT_DIR=shorts/output
    SHORTS_TTS_VOICE=ko-KR-SunHiNeural
    SHORTS_TTS_RATE=+15%
    SHORTS_TTS_PITCH=-4Hz
    SHORTS_BGM=배경음악.mp3
"""
from __future__ import annotations

import argparse
import asyncio
import bisect
import json
import os
import platform
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

W, H = 1080, 1920
FPS = 30
# 쇼츠 전체 길이. 마지막 구매 안내 장면까지 포함해 정확히 이 길이로 맞춘다
TARGET_SECONDS = 20.0
MIN_SCENES, MAX_SCENES = 4, 6
# 구매 안내를 뺀 장면 대본 글자 수(공백 제외). edge-tts +15% 는 초당 6자쯤 읽는다
SCRIPT_CHARS = 85
# AI 가 고를 수 있도록 넉넉히 받는다
CANDIDATE_IMAGES = 10
MAX_VIDEOS = 4
SILENT_SCENE = 2.5
SILENT_VIDEO = 5.0
SCENE_PAD = 0.25
# 사진은 처음 93% 크기에서 원래 크기로 천천히 다가간다. 잘리거나 늘어나는 곳은 없다
PUSH = 0.93
# 쇼츠 하단 약 450px 은 채널명·설명 UI 가 덮는다
HOOK_Y = int(H * 0.40)
SUB_Y = int(H * 0.62)
HOOK_SIZE = 110
SUB_SIZE = 68
YELLOW = (255, 214, 0)
WHITE = (255, 255, 255)
AD_LABEL = "(광고)"
CTA_NARRATION = "프로모션 코드는 고정 댓글에 있어요. 꼭 확인해 보세요!"
CTA_HOOK = ["프로모션 코드는", "고정 댓글 확인!"]
LINK_CTA = "구매 링크는 고정 댓글과 설명란에 있어요."
LINK_HOOK = ["구매 링크는", "고정 댓글에!"]
DISCLOSURE = os.getenv("SHORTS_DISCLOSURE") or (
    "[광고] 이 영상에는 제휴 링크가 포함되어 있으며, 링크로 구매하면 수수료를 받습니다."
)
DEFAULT_TITLE = "오늘의 추천템"
# 네이버 블로그에서 쇼츠 글을 모아 둘 카테고리. 블로그의 카테고리 이름과 같아야 한다
VIDEO_CATEGORY = os.getenv("NAVER_VIDEO_CATEGORY") or "비디오로그"
# 비우면 TISTORY_CATEGORY 를 따른다
TISTORY_VIDEO_CATEGORY = os.getenv("TISTORY_VIDEO_CATEGORY") or ""
BLOG_TAG_LIMIT = 10
# upload_tistory 반환값: 하루 공개 발행 한도(15개)에 걸렸다
TISTORY_LIMITED = 2

TTS_VOICE = os.getenv("SHORTS_TTS_VOICE") or "ko-KR-SunHiNeural"
TTS_RATE = os.getenv("SHORTS_TTS_RATE") or "+15%"
TTS_PITCH = os.getenv("SHORTS_TTS_PITCH") or "-4Hz"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm"}

FONT_CANDIDATES = {
    "Windows": [
        "C:/Windows/Fonts/malgunbd.ttf",
        "C:/Windows/Fonts/NanumGothicBold.ttf",
        "C:/Windows/Fonts/malgun.ttf",
        "C:/Windows/Fonts/NanumGothic.ttf",
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Windows\Fonts\NanumGothicBold.ttf"),
    ],
    "Darwin": [
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/Library/Fonts/NanumGothicBold.ttf",
        os.path.expanduser("~/Library/Fonts/NanumGothicBold.ttf"),
        "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
        "/Library/Fonts/AppleGothic.ttf",
    ],
    "Linux": [
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    ],
}


def log(msg: str) -> None:
    print(msg, flush=True)


@dataclass
class Visual:
    """화면에 쓸 재료. 사진은 1, 2, ... 영상은 V1, V2, ... 로 부른다."""

    label: str
    path: Path
    video: bool = False
    seconds: float = 0.0


@dataclass
class Scene:
    visual: Visual
    narration: str = ""
    audio: Path | None = None
    duration: float = SILENT_SCENE
    subs: list[tuple[float, float, str]] = field(default_factory=list)
    offset: float = 0.0  # 영상이면 몇 초 지점부터 보여줄지


@dataclass
class Product:
    title: str = ""
    image_urls: list[str] = field(default_factory=list)

    def merge(self, title: str, urls: list[str]) -> None:
        if not self.title and title:
            self.title = title.strip()
        for u in urls:
            n = normalize_image_url(u)
            if n and n not in self.image_urls:
                self.image_urls.append(n)


# ---------------------------------------------------------------- 크롤링

def new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    })
    return s


def normalize_image_url(url: str) -> str:
    if not url or url.startswith("data:"):
        return ""
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith("http"):
        return ""
    # 썸네일 크기 접미사(_220x220q75.jpg_.avif 등)를 떼어 원본 해상도로 받는다
    return re.sub(r"(\.(?:jpe?g|png|webp))_[^/]*$", r"\1", url, flags=re.I)


def canonical_url(url: str, session: requests.Session) -> str:
    m = re.search(r"/item/(\d+)\.html", url) or re.search(r"[?&]productId=(\d+)", url)
    if not m:
        # s.click / a.aliexpress.com 같은 단축·제휴 링크는 도착 주소에서 상품 번호를 찾는다
        try:
            r = session.head(url, allow_redirects=True, timeout=15)
            url = r.url
        except requests.RequestException as e:
            log(f"[경고] 링크를 따라가지 못했습니다: {e}")
            return url
        m = re.search(r"/item/(\d+)\.html", url) or re.search(r"[?&]productId=(\d+)", url)
        if not m:
            return url
    host = urlparse(url).netloc
    if not host.endswith("aliexpress.com"):
        host = "ko.aliexpress.com"
    return f"https://{host}/item/{m.group(1)}.html"


def robots_check(url: str, session: requests.Session) -> tuple[bool, float]:
    """robots.txt 를 직접 받아 판단한다. RobotFileParser.read() 는 418·5xx 를 '전부 허용'으로 오판한다."""
    p = urlparse(url)
    robots_url = f"{p.scheme}://{p.netloc}/robots.txt"
    try:
        r = session.get(robots_url, timeout=10)
    except requests.RequestException as e:
        log(f"[경고] robots.txt 를 읽지 못해 수집을 건너뜁니다: {e}")
        return False, 0.0
    if r.status_code == 404:
        return True, 0.0
    if r.status_code != 200:
        log(f"[경고] robots.txt 응답 {r.status_code} - 안전하게 수집을 건너뜁니다.")
        return False, 0.0
    rp = RobotFileParser()
    rp.parse(r.text.splitlines())
    return rp.can_fetch("*", url), float(rp.crawl_delay("*") or 0)


def parse_html(html: str) -> tuple[str, list[str]]:
    title, images = "", []

    m = re.search(r'"imagePathList"\s*:\s*(\[[^\]]*\])', html)
    if m:
        try:
            images.extend(json.loads(m.group(1)))
        except json.JSONDecodeError:
            pass

    m = re.search(r'"subject"\s*:\s*"((?:[^"\\]|\\.)*)"', html)
    if m:
        try:
            title = json.loads(f'"{m.group(1)}"')
        except json.JSONDecodeError:
            title = m.group(1)

    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        og_title = soup.find("meta", property="og:title")
        og_image = soup.find("meta", property="og:image")
        if not title and og_title and og_title.get("content"):
            title = og_title["content"]
        if og_image and og_image.get("content"):
            images.append(og_image["content"])
        if not title and soup.title and soup.title.string:
            title = soup.title.string
    except ImportError:
        pass

    return title, images


BROWSER_JS = """
() => {
  const out = {title: "", images: []};
  try {
    const d = (window.runParams && window.runParams.data) || {};
    if (d.imageModule && d.imageModule.imagePathList) out.images.push(...d.imageModule.imagePathList);
    if (d.titleModule && d.titleModule.subject) out.title = d.titleModule.subject;
  } catch (e) {}
  try {
    const dc = window._d_c_ && window._d_c_.DCData;
    if (dc && dc.imagePathList) out.images.push(...dc.imagePathList);
  } catch (e) {}
  const sel = '[class*="slider--img"] img, [class*="slider--item"] img, '
            + '[class*="images-view-item"] img, [class*="magnifier--image"]';
  document.querySelectorAll(sel).forEach(img => {
    const s = img.currentSrc || img.src || img.getAttribute('data-src');
    if (s) out.images.push(s);
  });
  if (!out.title) {
    const h = document.querySelector('h1[data-pl="product-title"], [data-pl="product-title"], h1');
    if (h) out.title = h.innerText.trim();
  }
  return out;
}
"""


def looks_blocked(page) -> bool:
    url = page.url.lower()
    if any(k in url for k in ("punish", "_____tmd_____", "captcha", "login.aliexpress")):
        return True
    try:
        text = page.inner_text("body", timeout=3000)[:2000].lower()
    except Exception:
        return False
    return any(k in text for k in ("slide to verify", "밀어서 확인", "unusual traffic", "captcha"))


def crawl_with_browser(url: str, headed: bool) -> tuple[str, list[str]]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        try:
            ctx = browser.new_context(user_agent=UA, locale="ko-KR", viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(4000)
            if looks_blocked(page):
                if headed and sys.stdin.isatty():
                    input("창에서 캡차·로그인을 끝내고 상품 페이지가 보이면 Enter 를 누르세요...")
                    page.wait_for_timeout(2000)
                else:
                    log("[경고] 캡차/차단 화면입니다. --browser 로 실행하면 창에서 직접 풀 수 있습니다.")
            data = page.evaluate(BROWSER_JS) or {}
            title, images = parse_html(page.content())
            return data.get("title") or title, list(data.get("images") or []) + images
        finally:
            browser.close()


def crawl(url: str, headed: bool) -> Product:
    product = Product()
    session = new_session()
    page_url = canonical_url(url, session)
    log(f"상품 페이지: {page_url}")

    allowed, delay = robots_check(page_url, session)
    if not allowed:
        log("[중단] robots.txt 가 이 주소의 자동 수집을 허용하지 않습니다. "
            "--images-dir 와 --title 로 직접 넣어 주세요.")
        return product

    try:
        r = session.get(page_url, timeout=20)
        r.raise_for_status()
        product.merge(*parse_html(r.text))
    except requests.RequestException as e:
        log(f"[경고] 정적 수집 실패: {e}")

    if len(product.image_urls) < MIN_SCENES or not product.title:
        if delay:
            time.sleep(delay)
        log("브라우저로 다시 읽는 중...")
        try:
            product.merge(*crawl_with_browser(page_url, headed))
        except ImportError:
            log("[경고] playwright 가 없어 브라우저 수집을 건너뜁니다.")
        except Exception as e:
            log(f"[경고] 브라우저 수집 실패: {e}")

    log(f"상품명: {product.title or '(없음)'} / 이미지 후보 {len(product.image_urls)}개")
    return product


def download_images(urls: list[str], dest: Path, limit: int) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    session = new_session()
    session.headers["Referer"] = "https://www.aliexpress.com/"
    saved: list[Path] = []
    for url in urls:
        if len(saved) >= limit:
            break
        try:
            r = session.get(url, timeout=20)
            r.raise_for_status()
            img = ImageOps.exif_transpose(Image.open(BytesIO(r.content))).convert("RGB")
            if min(img.size) < 250:
                continue
            path = dest / f"{len(saved) + 1:02d}.jpg"
            img.save(path, quality=95)
            saved.append(path)
            log(f"  저장 {path.name} {img.size[0]}x{img.size[1]}")
        except (requests.RequestException, OSError, Image.DecompressionBombError) as e:
            log(f"  [건너뜀] {url[:80]} ({e.__class__.__name__})")
    return saved


def load_local_images(folder: Path) -> list[Path]:
    if not folder.is_dir():
        log(f"[경고] 폴더가 없습니다: {folder}")
        return []
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXTS)


def clean_title(title: str) -> str:
    title = re.sub(r"\s*[-|]\s*AliExpress.*$", "", title or "", flags=re.I)
    return re.sub(r"\s+", " ", title).strip()


# ---------------------------------------------------------------- AI

AI_SYSTEM = "너는 유튜브 쇼츠 상품 소개 영상 대본 작가다. 지시한 JSON 만 출력한다."

AI_USER = """첨부한 그림 {count}장을 보고 쇼츠를 구성한다.
{visual_guide}

{context}

[검색 키워드] 메인: {main_keyword} / 함께: {keywords}

이 상품을 소개하는 {seconds}초짜리 유튜브 쇼츠를 만든다. 목표는 두 가지다. 검색으로 찾아 들어오게 하고,
끝까지 본 사람이 구매 링크를 누르게 한다. 화면은 판매 페이지에서 받은 상품 영상과 사진이 빠르게 넘어가고,
가운데에 자막처럼 짧은 말이 뜨는 형식이다. 끝에 3초 내외의 구매 안내가 따로 붙는다.
말투는 친한 언니가 옆에서 말하듯 짧고 편한 구어체 존댓말이다. ("다리미판도 필요 없고", "이게 진짜 편하거든요")
- 직접 써 봤다고 꾸미지 않는다. "써 보니", "사용해 봤는데", "내돈내산" 같은 말은 쓰지 않는다.
- 위 상품 정보와 그림으로 확인되는 것만 말한다. 근거 없는 효능·수치·가격, 최고·완벽·1위 같은 과장,
  "지금 바로" "무조건 사세요" "놓치면" 같은 구매 압박은 쓰지 않는다.
- 설득 순서: 시청자의 고민 -> 그 고민을 푸는 이 상품의 핵심 이점 -> 어떤 사람에게 맞는지.

1. title: 한국어 상품명. 18자 이내. 모델 코드·검색 키워드 나열·'Free Shipping' 같은 말은 뺀다.
2. hook: 첫 장면 화면 가운데에 크게 띄울 두 줄. 각 줄 11자 이내. 스크롤을 멈추게 하는 고민이나 결과를 말한다.
   예: ["옷이 반듯하면", "사람이 달라 보여요"]
3. scenes: {min_n}~{max_n}개 장면을 보여줄 순서대로 적는다.
   - visual: 그 장면에 보여줄 그림. 사진은 번호("3"), 영상은 "V1" 처럼 적는다.
   - 영상이 있으면 최대한 많이 쓴다. 작동 모습·사용 장면은 영상, 외관·구성·사양은 사진이 좋다.
     같은 영상을 여러 장면에 써도 된다(장면마다 다음 구간이 나온다). 같은 사진은 한 번만 쓴다.
   - 쓸 만한 그림은 가능하면 모두 한 번 이상 쓴다. 사이즈표, 배송·쿠폰 안내, 글자만 있는 배너,
     거의 같은 사진은 쓰지 않는다.
   - narration: 그 장면에서 하는 말. 한 문장, 20자 이내. 모든 장면의 narration 을 합친 글자 수(공백 제외)는
     {chars}자를 넘지 않는다. 넘으면 {seconds}초 안에 다 읽지 못한다. 화면에 보이는 것과 맞아야 하고,
     앞뒤 장면이 한 사람이 이어 말하는 것처럼 자연스럽게 이어져야 한다.
   - 첫 장면은 {first_rule} 시청자가 공감할 고민으로 시작한다.
   - 마지막 장면은 짧은 정리 한마디로 끝낸다. 구매 링크·댓글 안내는 따로 붙이므로 쓰지 않는다.
4. youtube_title: 유튜브 업로드 제목. 메인 키워드를 맨 앞에 글자 그대로 넣고 고민이나 이점을 붙인다.
   40자 이내. 내용과 다른 낚시 제목은 쓰지 않는다.
5. description: 설명란 본문 3~4줄. 첫 줄에 메인 키워드를 넣어 이 영상이 무엇인지 말하고, 핵심 이점과
   맞는 사람을 적는다. 함께 노릴 키워드 두어 개를 문장 안에 자연스럽게 넣는다.
   구매 링크·광고 문구·해시태그는 따로 붙이므로 쓰지 않는다.
6. tags: 유튜브 태그 8~12개. 검색 키워드, 브랜드·모델명, 카테고리 표현.
7. pinned_comment: 고정 댓글 한두 문장. 링크에서 확인해 볼 것 한 가지(구성·옵션·상세 사양 등)를 들어 권한다.
   링크는 따로 붙이므로 쓰지 않는다.
8. blog_title: 이 쇼츠를 올릴 블로그 글 제목. 메인 키워드를 앞쪽에 글자 그대로 넣는다. 32자 이내.
9. blog_intro: 영상 아래에 넣을 간단한 상품 소개글. 문단 3개, 문단마다 2~3문장, 합쳐 350~550자.
   첫 문단 첫 문장에 메인 키워드를 넣는다. 고민 -> 상품의 핵심 이점과 그 근거 -> 맞는 사람 순서로 쓴다.
   함께 노릴 키워드 두어 개를 문장 안에 자연스럽게 넣는다. 말투는 담백한 블로그 존댓말.
   구매 링크·광고 문구는 따로 붙이므로 쓰지 않는다.

아래 JSON 형식으로만 출력한다.
{{"title": "...", "hook": ["...", "..."], "scenes": [{{"visual": "V1", "narration": "..."}}],
 "youtube_title": "...", "description": "...", "tags": ["..."], "pinned_comment": "...",
 "blog_title": "...", "blog_intro": ["문단1", "문단2", "문단3"]}}"""

KEYWORD_USER = """아래 상품을 유튜브 쇼츠로 소개하려 한다. 웹 검색으로 한국 사람들이 이런 상품을 찾을 때
유튜브·네이버에서 실제로 치는 검색어를 조사해라.

{context}

- main_keyword: 검색량이 있으면서 구매 의도가 담긴 말 하나. 모델명만으로 잡지 말고 "카테고리 + 의도"로
  잡는다. 예: "무선 청소기 추천". 너무 넓은 한 단어(예: "청소기")는 피한다.
- keywords: 함께 노릴 검색어 5~8개. 브랜드·모델명, 기능, 쓰는 상황, 고민 표현을 섞는다.
  검색창에 그대로 칠 법한 말만 쓴다.
- hashtags: 해시태그로 쓸 말 3개. # 없이, 띄어쓰기 없이.
- blog_tags: 네이버 블로그·티스토리 글 태그 10개. 메인 키워드와 함께 노릴 검색어, 브랜드·모델명,
  카테고리, 쓰는 상황 중 검색량이 있는 것부터. 태그 하나는 15자 이내.

아래 JSON 형식으로만 출력한다.
{{"main_keyword": "...", "keywords": ["..."], "hashtags": ["..."], "blog_tags": ["..."]}}"""

# AI 가 지시를 어겨도 체험 위장·과장·구매 압박 문장은 읽지 않는다
BANNED = re.compile(
    r"써\s?보니|써\s?봤|사용해\s?봤|사용해\s?보니|내돈내산|직접\s?써|최고의|완벽|1위|지금\s?바로|놓치면|품절|무조건\s?사"
)


def clean_narration(text: str) -> str:
    sentences = re.split(r"(?<=[.!?…])\s+", text.strip())
    return " ".join(s for s in sentences if s and not BANNED.search(s))


@dataclass
class Plan:
    title: str = ""
    hook: list[str] = field(default_factory=list)
    scenes: list[Scene] = field(default_factory=list)
    keyword: str = ""
    keywords: list[str] = field(default_factory=list)
    hashtags: list[str] = field(default_factory=list)
    youtube_title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    pinned_comment: str = ""
    blog_title: str = ""
    blog_intro: list[str] = field(default_factory=list)
    blog_tags: list[str] = field(default_factory=list)


def strs(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def make_client():
    try:
        sys.path.insert(0, str(ROOT))
        from ai.client import MyGenAssistClient
        from ai.image_screen import can_see
        from config import load_ai_config

        client = MyGenAssistClient(load_ai_config())
    except Exception as e:
        log(f"[경고] AI 설정을 읽지 못해 AI 없이 만듭니다: {e}")
        return None
    if not can_see(client):
        log("[경고] 지금 AI 설정(AI_ENDPOINT=responses)은 이미지를 볼 수 없어 AI 없이 만듭니다.")
        return None
    return client


def ai_keywords(client, context: str) -> dict:
    """웹 검색으로 사람들이 실제로 치는 검색어를 조사한다. 실패해도 영상은 만든다."""
    log("검색 키워드 조사 중 (웹 검색 포함)...")
    try:
        data = client.chat_json(AI_SYSTEM, KEYWORD_USER.format(context=context),
                                temperature=0.4, max_tokens=4000)
    except Exception as e:
        log(f"[경고] 키워드 조사 실패, 상품명으로 진행합니다: {e}")
        return {}
    if not isinstance(data, dict):
        return {}
    log(f"  메인 키워드: {data.get('main_keyword')} / {', '.join(strs(data.get('keywords')))}")
    return data


def ai_plan(client, visuals: list[Visual], attach: list[Path], context: str, kw: dict) -> Plan | None:
    """AI 가 사진·영상을 보고 장면·대본·훅·유튜브 문구를 정한다. 실패하면 None."""
    main_keyword = str(kw.get("main_keyword") or "").strip()
    keywords = strs(kw.get("keywords"))
    photos = [v for v in visuals if not v.video]
    videos = [v for v in visuals if v.video]
    guide = [f"1~{len(photos)}번은 판매 페이지의 상품 사진이다. 1번이 대표 썸네일이다."]
    if videos:
        guide.append(f"그 뒤 {len(videos)}장은 판매 페이지에 있는 상품 영상의 장면을 3컷씩 이어 붙인 것이다.")
        guide += [f"  {len(photos) + k}번 그림 = 영상 {v.label} ({v.seconds:.0f}초)" for k, v in enumerate(videos, 1)]
    first_rule = "상품이 잘 보이는 영상이나 1번 사진으로 하고," if videos else "반드시 1번 사진으로 하고,"

    log("AI 가 사진·영상을 보고 장면과 대본을 짜는 중...")
    try:
        data = client.chat_json(
            AI_SYSTEM,
            AI_USER.format(count=len(attach), visual_guide="\n".join(guide), context=context,
                           main_keyword=main_keyword or "(없음 - 상품명으로 정해라)",
                           keywords=", ".join(keywords) or "(없음)",
                           min_n=MIN_SCENES, max_n=MAX_SCENES, first_rule=first_rule,
                           seconds=int(TARGET_SECONDS), chars=SCRIPT_CHARS),
            temperature=0.7,
            max_tokens=8000,
            websearch=False,
            images=attach,
        )
    except Exception as e:
        log(f"[경고] AI 호출 실패, AI 없이 만듭니다: {e}")
        return None
    if not isinstance(data, dict):
        return None

    by_label = {v.label: v for v in visuals}
    scenes: list[Scene] = []
    used: set[str] = set()
    for item in data.get("scenes") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("visual") or item.get("no") or "").strip().upper().lstrip("#")
        visual = by_label.get(label)
        if visual is None or (not visual.video and label in used):
            continue
        used.add(label)
        scenes.append(Scene(visual, clean_narration(str(item.get("narration") or ""))))
        if len(scenes) >= MAX_SCENES:
            break
    if not scenes:
        return None

    # 상품 영상이 없으면 첫 장면은 대표 썸네일로 고정하고, 대본 순서는 그대로 둔다
    if not videos and photos:
        first = next((k for k, sc in enumerate(scenes) if sc.visual is photos[0]), None)
        if first:
            order = [sc.visual for sc in scenes]
            order.insert(0, order.pop(first))
            for sc, v in zip(scenes, order):
                sc.visual = v

    plan = Plan(
        title=str(data.get("title") or "").strip(),
        hook=[h for h in strs(data.get("hook"))[:2] if not BANNED.search(h)],
        scenes=scenes,
        keyword=main_keyword,
        keywords=keywords,
        hashtags=strs(kw.get("hashtags")),
        youtube_title=str(data.get("youtube_title") or "").strip(),
        description=clean_narration(str(data.get("description") or "")),
        tags=strs(data.get("tags")),
        pinned_comment=clean_narration(str(data.get("pinned_comment") or "")),
        blog_title=str(data.get("blog_title") or "").strip(),
        blog_intro=[p for p in (clean_narration(s) for s in strs(data.get("blog_intro"))) if p],
        blog_tags=strs(kw.get("blog_tags")),
    )
    if BANNED.search(plan.youtube_title):
        plan.youtube_title = ""
    if BANNED.search(plan.blog_title):
        plan.blog_title = ""
    log(f"AI 제목: {plan.title or '(없음)'} / 훅: {' / '.join(plan.hook)} / 장면 {len(scenes)}개")
    log(f"유튜브 제목: {plan.youtube_title or '(없음)'}")
    for sc in scenes:
        log(f"  [{sc.visual.label}] {sc.narration}")
    return plan


# ---------------------------------------------------------------- 글꼴·텍스트

def find_font(user_font: str | None) -> str | None:
    for path in [user_font, os.getenv("SHORTS_FONT")] + FONT_CANDIDATES.get(platform.system(), []):
        if path and Path(path).is_file():
            return path
    log("[경고] 한글 글꼴을 찾지 못했습니다. --font 또는 .env 의 SHORTS_FONT 로 지정하세요.")
    return None


def load_font(path: str | None, size: int) -> ImageFont.FreeTypeFont:
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError as e:
            log(f"[경고] 글꼴을 열지 못했습니다({path}): {e}")
    return ImageFont.load_default(size=size)


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        candidate = f"{cur} {word}" if cur else word
        if font.getlength(candidate) <= max_w:
            cur = candidate
            continue
        if cur:
            lines.append(cur)
        while font.getlength(word) > max_w:
            cut = len(word)
            while cut > 1 and font.getlength(word[:cut]) > max_w:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        cur = word
    if cur:
        lines.append(cur)
    return lines


def fit_text(text: str, font_path: str | None, max_w: int, max_lines: int, sizes: range):
    for size in sizes:
        font = load_font(font_path, size)
        lines = wrap_text(text, font, max_w)
        if len(lines) <= max_lines:
            return font, lines
    lines = lines[:max_lines]
    last = lines[-1]
    while last and font.getlength(last + "…") > max_w:
        last = last[:-1]
    lines[-1] = last.rstrip() + "…"
    return font, lines


def draw_lines(lines: list[tuple[str, tuple]], font: ImageFont.FreeTypeFont, stroke: int) -> Image.Image:
    """검은 테두리를 두른 글자 여러 줄을 가운데 정렬로 그린 투명 그림. 박스 없이 영상 위에 바로 얹는다."""
    line_h = int(font.size * 1.22)
    width = int(max(font.getlength(t) for t, _ in lines)) + stroke * 2 + 8
    img = Image.new("RGBA", (width, line_h * len(lines) + stroke * 2 + 8), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for k, (text, color) in enumerate(lines):
        d.text((width // 2, stroke + k * line_h), text, font=font, fill=color, anchor="ma",
               stroke_width=stroke, stroke_fill=(0, 0, 0))
    return img


def build_hook(lines: list[str], font_path: str | None) -> Image.Image:
    """첫 장면 가운데에 크게 띄우는 두 줄. 첫 줄은 노랑, 둘째 줄은 하양."""
    lines = [line for line in lines if line][:2]
    size = HOOK_SIZE
    while size > 64:
        font = load_font(font_path, size)
        if all(font.getlength(line) <= W - 110 for line in lines):
            break
        size -= 4
    lines = [fit_text(line, font_path, W - 110, 1, range(size, size - 1, -1))[1][0] for line in lines]
    colors = [YELLOW, WHITE] if len(lines) > 1 else [WHITE]
    return draw_lines(list(zip(lines, colors)), load_font(font_path, size), stroke=max(7, size // 10))


def build_sub(text: str, font_path: str | None) -> Image.Image:
    font, lines = fit_text(text, font_path, W - 140, 2, range(SUB_SIZE, 47, -4))
    return draw_lines([(line, WHITE) for line in lines], font, stroke=7)


def paste_center(frame: Image.Image, layer: Image.Image, center_y: int) -> None:
    frame.paste(layer, ((W - layer.width) // 2, int(center_y - layer.height / 2)), layer)


def blurred_bg(img: Image.Image) -> Image.Image:
    """남는 화면을 채울 배경. 같은 그림을 아주 작게 줄였다 키워 흐리게 만든다."""
    small = ImageOps.fit(img, (27, 48), Image.BILINEAR)
    return Image.blend(small.resize((W, H), Image.BILINEAR), Image.new("RGB", (W, H)), 0.35)


class PhotoShot:
    """사진은 자르거나 늘리지 않고 통째로 보여준다."""

    def __init__(self, img: Image.Image):
        self.bg = blurred_bg(img)
        r = min(W / img.width, H / img.height)
        self.fg = img.resize((round(img.width * r), round(img.height * r)), Image.LANCZOS)

    def frame(self, progress: float) -> Image.Image:
        p = progress * progress * (3 - 2 * progress)
        s = PUSH + (1 - PUSH) * p
        fg = self.fg if s > 0.999 else self.fg.resize(
            (round(self.fg.width * s), round(self.fg.height * s)), Image.BILINEAR)
        out = self.bg.copy()
        out.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))
        return out


def fit_video_frame(arr: np.ndarray) -> Image.Image:
    """영상도 자르거나 늘리지 않는다. 남는 곳은 흐린 배경으로 채운다."""
    img = Image.fromarray(arr)
    if img.size == (W, H):
        return img
    out = blurred_bg(img)
    r = min(W / img.width, H / img.height)
    fg = img.resize((round(img.width * r), round(img.height * r)), Image.BILINEAR)
    out.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))
    return out


def contact_sheet(path: Path, seconds: float, dest: Path) -> Path | None:
    """AI 가 영상 내용을 볼 수 있게 앞·중간·뒤 세 장면을 한 장으로 이어 붙인다."""
    from moviepy import VideoFileClip

    try:
        clip = VideoFileClip(str(path), audio=False)
        frames = [Image.fromarray(clip.get_frame(seconds * r)) for r in (0.15, 0.5, 0.85)]
        clip.close()
    except Exception as e:
        log(f"[건너뜀] 영상 장면을 읽지 못했습니다({path.name}): {e}")
        return None
    frames = [ImageOps.contain(f, (400, 400)) for f in frames]
    sheet = Image.new("RGB", (sum(f.width for f in frames) + 20 * 2, max(f.height for f in frames)), "white")
    x = 0
    for f in frames:
        sheet.paste(f, (x, 0))
        x += f.width + 20
    sheet.save(dest, quality=90)
    return dest


# ---------------------------------------------------------------- 영상

def open_image(path: Path) -> Image.Image | None:
    try:
        return ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    except OSError as e:
        log(f"[건너뜀] {path.name}: {e}")
        return None


# ---------------------------------------------------------------- 음성

def synthesize(text: str, mp3: Path) -> list[tuple[float, float, str]] | None:
    """edge-tts 로 읽고 단어별 (시작, 끝, 단어) 초 단위 목록을 돌려준다. 실패하면 None."""
    try:
        import edge_tts
    except ImportError:
        log("[경고] edge-tts 가 없어 음성 없이 만듭니다: pip install edge-tts")
        return None

    async def run() -> list[tuple[float, float, str]]:
        try:
            com = edge_tts.Communicate(text, TTS_VOICE, rate=TTS_RATE, pitch=TTS_PITCH, boundary="WordBoundary")
        except TypeError:
            # 6.x 는 boundary 인자가 없고 늘 단어 경계를 준다
            com = edge_tts.Communicate(text, TTS_VOICE, rate=TTS_RATE, pitch=TTS_PITCH)
        words = []
        with open(mp3, "wb") as f:
            async for chunk in com.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    start = chunk["offset"] / 1e7
                    words.append((start, start + chunk["duration"] / 1e7, chunk["text"]))
        return words

    try:
        return asyncio.run(run())
    except Exception as e:
        log(f"[경고] 음성 합성 실패({e.__class__.__name__}: {e}) - 이 장면은 자막만 넣습니다.")
        mp3.unlink(missing_ok=True)
        return None


def chunk_subs(words: list[tuple[float, float, str]], text: str, length: float) -> list[tuple[float, float, str]]:
    """자막 한 번에 띄울 만큼씩 단어를 묶는다. 단어 시각이 없으면 글자 수 비율로 나눈다."""
    if not words:
        tokens = text.split()
        total = sum(len(t) for t in tokens) or 1
        t = 0.0
        for tok in tokens:
            d = length * len(tok) / total
            words.append((t, t + d, tok))
            t += d
    subs: list[list] = []
    cur: list[str] = []
    start = 0.0
    cursor = 0
    for s, e, w in words:
        # TTS 단어에는 문장부호가 빠져 있어 원문에서 되찾는다
        at = text.find(w, cursor)
        if at >= 0:
            end = at + len(w)
            while end < len(text) and text[end] in ".,!?…~":
                end += 1
            w = text[at:end]
            cursor = end
        if not cur:
            start = s
        cur.append(w)
        if sum(len(x) for x in cur) >= 12 or re.search(r"[.,!?…~]$", w):
            subs.append([start, e, " ".join(cur)])
            cur = []
    if cur:
        subs.append([start, words[-1][1], " ".join(cur)])
    # 다음 자막이 뜰 때까지 앞 자막을 띄워 둔다
    for a, b in zip(subs, subs[1:]):
        a[1] = b[0]
    if subs:
        subs[0][0] = 0.0
        subs[-1][1] = length + SCENE_PAD
    return [(a, b, c) for a, b, c in subs]


def prepare_audio(scenes: list[Scene], audio_dir: Path, use_tts: bool) -> None:
    from moviepy import AudioFileClip

    audio_dir.mkdir(parents=True, exist_ok=True)
    for k, sc in enumerate(scenes, 1):
        if not sc.narration:
            sc.duration = (min(SILENT_VIDEO, max(2.0, sc.visual.seconds))
                           if sc.visual.video else SILENT_SCENE)
            continue
        words, length = None, 0.0
        if use_tts:
            mp3 = audio_dir / f"{k:02d}.mp3"
            words = synthesize(sc.narration, mp3)
            if words is not None and mp3.exists() and mp3.stat().st_size > 0:
                try:
                    clip = AudioFileClip(str(mp3))
                    length = clip.duration
                    clip.close()
                    sc.audio = mp3
                except Exception as e:
                    log(f"[경고] 음성 파일을 읽지 못했습니다({mp3.name}): {e}")
        if sc.audio is None:
            words = None
            # 음성이 없으면 읽는 속도쯤으로 자막을 띄운다
            length = max(SILENT_SCENE, len(sc.narration) * 0.17)
        sc.duration = length + SCENE_PAD
        sc.subs = chunk_subs(list(words or []), sc.narration, length)


# ---------------------------------------------------------------- 렌더링

def fit_length(scenes: list[Scene], target: float) -> None:
    """전체 길이를 target 초에 맞춘다. 길면 가운데 장면을 빼고, 짧으면 장면마다 고르게 늘린다."""
    # 첫 장면(훅)과 마지막 구매 안내는 지킨다
    while sum(sc.duration for sc in scenes) > target and len(scenes) > 2:
        drop = max(scenes[1:-1], key=lambda sc: sc.duration)
        scenes.remove(drop)
        log(f"  {target:.0f}초에 맞추려고 장면을 뺐습니다: [{drop.visual.label}] {drop.narration}")
    total = sum(sc.duration for sc in scenes)
    if total > target:
        log(f"[경고] 내레이션이 길어 {total:.1f}초가 됩니다.")
        return
    extra = (target - total) / len(scenes)
    for sc in scenes:
        sc.duration += extra
        if sc.subs:
            start, _, text = sc.subs[-1]
            sc.subs[-1] = (start, sc.duration, text)


def assign_offsets(scenes: list[Scene]) -> None:
    """같은 영상을 여러 장면에 쓰면 앞 장면이 끝난 지점부터 이어서 보여준다."""
    cursor: dict[str, float] = {}
    for sc in scenes:
        if not sc.visual.video:
            continue
        start = cursor.get(sc.visual.label, 0.0)
        if start >= sc.visual.seconds - 0.5:
            start = 0.0
        elif start + sc.duration > sc.visual.seconds:
            # 남은 구간이 짧으면 끝에 맞춰 조금 앞에서 시작한다
            start = max(0.0, sc.visual.seconds - sc.duration)
        sc.offset = start
        cursor[sc.visual.label] = start + sc.duration


def render(scenes: list[Scene], hook: list[str], end_hook: list[str], font_path: str | None,
           bgm: Path | None, out: Path) -> float:
    from moviepy import AudioFileClip, CompositeAudioClip, VideoClip, VideoFileClip

    assign_offsets(scenes)
    durs = [sc.duration for sc in scenes]
    starts = [sum(durs[:k]) for k in range(len(durs))]
    total = sum(durs)
    n = len(scenes)
    clips = {sc.visual.label: VideoFileClip(str(sc.visual.path), audio=False)
             for sc in scenes if sc.visual.video}
    photos = {sc.visual.label: PhotoShot(open_image(sc.visual.path))
              for sc in scenes if not sc.visual.video}
    subs = [(st + a, st + b, text) for sc, st in zip(scenes, starts) for a, b, text in sc.subs]
    sub_starts = [s for s, _, _ in subs]
    sub_layers: dict[str, Image.Image] = {}
    hook_layer = build_hook(hook, font_path) if hook else None
    end_layer = build_hook(end_hook, font_path) if end_hook else None
    badge = draw_lines([(AD_LABEL, WHITE)], load_font(font_path, 40), stroke=3)

    def frame(t: float) -> np.ndarray:
        i = max(0, min(bisect.bisect_right(starts, t) - 1, n - 1))
        sc, local = scenes[i], t - starts[i]
        if sc.visual.video:
            clip = clips[sc.visual.label]
            last = max(0.0, clip.duration - 1 / (clip.fps or FPS))
            img = fit_video_frame(clip.get_frame(min(sc.offset + local, last)))
        else:
            img = photos[sc.visual.label].frame(min(local / durs[i], 1.0))
        if i == 0 and hook_layer:
            paste_center(img, hook_layer, HOOK_Y)
        elif i == n - 1 and end_layer:
            paste_center(img, end_layer, HOOK_Y)
        k = bisect.bisect_right(sub_starts, t) - 1
        if k >= 0 and t < subs[k][1]:
            text = subs[k][2]
            if text not in sub_layers:
                sub_layers[text] = build_sub(text, font_path)
            paste_center(img, sub_layers[text], SUB_Y)
        img.paste(badge, (W - badge.width - 24, 110), badge)
        return np.asarray(img)

    tracks = [AudioFileClip(str(sc.audio)).with_start(st) for sc, st in zip(scenes, starts) if sc.audio]
    music = bgm_track(bgm, total)
    if music:
        tracks.insert(0, music)

    clip = VideoClip(frame, duration=total)
    if tracks:
        clip = clip.with_audio(CompositeAudioClip(tracks).with_duration(total))
    clip.write_videofile(str(out), fps=FPS, codec="libx264", audio=bool(tracks), audio_codec="aac",
                         preset="medium", threads=os.cpu_count() or 2, logger="bar")
    clip.close()
    for tr in tracks:
        tr.close()
    for c in clips.values():
        c.close()
    return total


def bgm_track(bgm: Path | None, total: float):
    if not bgm:
        return None
    from moviepy import AudioFileClip, afx

    try:
        music = AudioFileClip(str(bgm))
        if music.duration > total:
            music = music.subclipped(0, total)
        # 목소리가 묻히지 않게 작게 깐다
        return music.with_effects([afx.MultiplyVolume(0.12), afx.AudioFadeOut(1.0)])
    except Exception as e:
        log(f"[경고] 배경음악을 넣지 못했습니다: {e}")
        return None


# ---------------------------------------------------------------- 실행

def safe_name(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", text).strip("_")[:40] or "shorts"


def is_aliexpress(url: str) -> bool:
    return "aliexpress" in urlparse(url).netloc.lower()


def ask_page_url(buy_url: str) -> str:
    """구매 링크로 상품 페이지를 못 읽었을 때만 실제 판매 페이지 주소를 묻는다."""
    if not sys.stdin.isatty():
        return ""
    log(f"\n이 구매 링크로는 상품 페이지를 읽지 못했습니다: {buy_url}")
    log("내용과 이미지를 읽어 올 실제 판매 페이지 주소를 넣어 주세요. 모르면 엔터.")
    answer = input("> ").strip()
    return answer if answer.startswith(("http://", "https://")) else ""


def handoff(url: str) -> bool:
    if not sys.stdin.isatty():
        return False
    log(f"\n브라우저 창에서 로그인·캡차를 끝내고 상품 상세 페이지를 띄운 뒤 엔터를 누르세요. 그만두려면 q.\n  {url}")
    return input("> ").strip().lower() != "q"


def collect_shop(url: str, page_url: str, headed: bool):
    """네이버 스마트스토어·브랜드스토어 등은 블로그 생성기와 같은 방법으로 읽는다."""
    try:
        sys.path.insert(0, str(ROOT))
        from pipeline import Pipeline
        from scrape.product import ProductUnavailable

        pipe = Pipeline(report=log, handoff=handoff, handoff_on_block=headed, ask_page_url=ask_page_url)
    except Exception as e:
        log(f"[중단] 블로그 생성기 설정을 읽지 못했습니다: {e}")
        return None
    try:
        return pipe, pipe.collect(url, page_url=page_url)
    except ProductUnavailable as e:
        log(f"[중단] {e}")
        return None


def shop_images(pipe, shop, out_dir: Path, limit: int) -> list[Path]:
    """1번은 대표 썸네일. 배송·쿠폰 안내를 걸러낸 제품 설명 이미지를 섞는다."""
    assets = pipe.prepare_images(shop, out_dir)
    thumbs = sorted((a for a in assets if a.source == "thumbnail" and a.path), key=lambda a: a.gallery_index)
    details = sorted((a for a in assets if a.source == "detail" and a.path), key=lambda a: a.score, reverse=True)
    picked = [a.path for a in thumbs[:3]]
    picked += [a.path for a in details][: limit - len(picked)]
    picked += [a.path for a in thumbs[3:]][: limit - len(picked)]
    return picked


def video_seconds(path: Path) -> float:
    from moviepy import VideoFileClip

    try:
        clip = VideoFileClip(str(path), audio=False)
        seconds = float(clip.duration or 0)
        clip.close()
        return seconds
    except Exception as e:
        log(f"[건너뜀] 영상을 열지 못했습니다({path.name}): {e}")
        return 0.0


def collect_videos(shop, out_dir: Path, videos_dir: Path | None) -> list[Visual]:
    """판매 페이지의 상품 영상과 --videos-dir 영상을 V1, V2 ... 로 모은다."""
    paths: list[Path] = []
    if shop is not None:
        from media import product_videos

        page = shop.resolved_url or shop.page_url or shop.url
        log("상품 영상 찾는 중...")
        sources, final_url = product_videos.collect(page)
        log(f"  영상 {len(sources)}개 발견")
        paths = product_videos.download(sources, out_dir / "videos", referer=final_url,
                                        limit=MAX_VIDEOS, report=log)
    if videos_dir and videos_dir.is_dir():
        local = sorted(p for p in videos_dir.iterdir() if p.suffix.lower() in VIDEO_EXTS)
        paths += local[: MAX_VIDEOS - len(paths)]
    videos: list[Visual] = []
    for path in paths:
        seconds = video_seconds(path)
        if seconds >= 1.5:
            videos.append(Visual(f"V{len(videos) + 1}", path, video=True, seconds=seconds))
    return videos


def disclosure_for(url: str | None) -> str:
    try:
        sys.path.insert(0, str(ROOT))
        from core.shopping_connect import disclosure_for as pick

        return pick([url or ""], DISCLOSURE)
    except ImportError:
        return DISCLOSURE


def youtube_meta(plan: Plan, title: str, url: str | None, disclosure: str) -> dict:
    """업로드할 때 붙여 넣을 제목·설명·태그·고정 댓글."""
    hashtags: list[str] = []
    for h in [*plan.hashtags[:3], "shorts"]:
        tag = "#" + re.sub(r"[\s#]+", "", h)
        if len(tag) > 1 and tag not in hashtags:
            hashtags.append(tag)

    tags: list[str] = []
    for t in [plan.keyword, *plan.keywords, *plan.tags]:
        t = t.strip().lstrip("#")
        # 유튜브 태그는 합쳐서 500자까지다
        if t and t not in tags and sum(len(x) + 1 for x in tags) + len(t) < 450:
            tags.append(t)

    yt_title = plan.youtube_title or (f"{plan.keyword} | {title}" if plan.keyword else title)
    lines = [disclosure, "", plan.description or f"{title} 소개 영상입니다."]
    if url:
        lines += ["", f"구매 링크: {url}"]
    lines += ["", " ".join(hashtags)]
    pinned = "\n".join(x for x in (plan.pinned_comment or "구매 링크 남겨 둘게요.", url, disclosure) if x)
    return {
        "title": yt_title[:100],
        "description": "\n".join(lines),
        "tags": tags,
        "pinned_comment": pinned,
        "keyword": plan.keyword,
    }


def save_youtube(meta: dict, out_dir: Path) -> None:
    text = (
        f"[제목]\n{meta['title']}\n\n"
        f"[설명]\n{meta['description']}\n\n"
        f"[태그]\n{', '.join(meta['tags'])}\n\n"
        f"[고정 댓글]\n{meta['pinned_comment']}\n\n"
        "[업로드할 때]\n"
        "- 세부정보 > 유료 프로모션 '포함' 을 체크하세요.\n"
    )
    (out_dir / "youtube.txt").write_text(text, encoding="utf-8")


def read_budget() -> dict | None:
    """myGenAssist 계정 예산(누적 사용액·남은 예산). 다른 제공자이거나 읽지 못하면 None."""
    try:
        sys.path.insert(0, str(ROOT))
        from config import load_ai_config

        cfg = load_ai_config()
        if cfg.provider != "mygenassist" or not cfg.api_key:
            return None
        r = requests.get(f"{cfg.base_url.rstrip('/')}/users/me/budget", headers=cfg.headers(),
                         proxies=cfg.proxies or None, verify=cfg.verify_ssl, timeout=20)
        return r.json() if r.ok else None
    except Exception:
        return None


def cost_report(before: dict | None, calls: int, reported: float) -> dict:
    report = {"ai_calls": calls, "reported_cost": round(reported, 4)}
    after = read_budget()
    if before and after:
        report["budget_used"] = round(float(after.get("accumulated_cost") or 0)
                                      - float(before.get("accumulated_cost") or 0), 4)
        report["budget_left"] = after.get("budget_left")
        report["budget_reset_date"] = after.get("budget_reset_date")
    return report


def print_cost(cost: dict) -> None:
    log(f"비용: AI 호출 {cost['ai_calls']}회, ${cost['reported_cost']:.4f} (호출 응답에 적힌 비용 합)")
    if "budget_used" in cost:
        left = cost.get("budget_left")
        log(f"      계정 예산 사용액 변화 ${cost['budget_used']:.4f}"
            + (f" / 남은 예산 ${left:.2f}" if isinstance(left, (int, float)) else "")
            + (f" (초기화 {cost['budget_reset_date']})" if cost.get("budget_reset_date") else ""))
        if cost["budget_used"] == 0 and cost["reported_cost"] > 0:
            log("      예산 집계는 늦게 반영될 수 있습니다.")
    log("      음성(edge-tts)과 영상 합성은 비용이 들지 않습니다.")


def blog_tags(plan: Plan) -> list[str]:
    """검색어 조사에서 나온 말로 글 태그를 만든다. 메인 키워드가 맨 앞이다."""
    tags: list[str] = []
    seen: set[str] = set()
    for tag in [plan.keyword, *plan.blog_tags, *plan.keywords, *plan.tags]:
        tag = re.sub(r"[#,]", "", tag or "").strip()
        key = tag.replace(" ", "")
        if tag and len(tag) <= 20 and key not in seen:
            seen.add(key)
            tags.append(tag)
    return tags[:BLOG_TAG_LIMIT]


def blog_post(plan: Plan, title: str, url: str | None, product_query: str,
              thumbnail: Path | None, video: Path) -> dict:
    return {
        "title": plan.blog_title or plan.youtube_title or title,
        "paragraphs": plan.blog_intro or [plan.description or f"{title} 소개 영상입니다."],
        "tags": blog_tags(plan),
        "url": url or "",
        "product_query": product_query,
        "thumbnail": str(thumbnail) if thumbnail else "",
        "video": str(video),
    }


def upload_from(run_dir: Path, *, no_tistory: bool, tistory_only: bool, debug: bool) -> int:
    meta_file = run_dir / "meta.json"
    try:
        blog = json.loads(meta_file.read_text(encoding="utf-8"))["blog"]
    except (OSError, ValueError, KeyError) as e:
        log(f"[중단] {meta_file} 에서 블로그 글 정보를 읽지 못했습니다: {e}")
        return 1
    if tistory_only:
        sys.path.insert(0, str(ROOT))
        import config
        from core import shopping_connect

        saved = run_dir / "naver.url"
        naver_url = saved.read_text(encoding="utf-8").strip() if saved.exists() else ""
        url = blog.get("url") or ""
        disclosure = shopping_connect.disclosure_for([url], config.load_post_config().disclosure) if url else ""
        return upload_tistory(run_dir, blog, naver_url, disclosure, debug=debug)
    return upload_blogs(run_dir, blog, no_tistory=no_tistory, debug=debug)


def upload_blogs(run_dir: Path, blog: dict, *, no_tistory: bool, debug: bool) -> int:
    """네이버 블로그에 동영상 글을 올리고, 그 글 주소를 담아 티스토리에도 올린다."""
    sys.path.insert(0, str(ROOT))
    import dataclasses

    import config
    from core import shopping_connect
    from publish.naver_blog import NaverBlogError, NaverBlogPublisher, PostBlock

    title, url, tags = blog["title"], blog.get("url") or "", blog.get("tags") or []
    disclosure = shopping_connect.disclosure_for([url], config.load_post_config().disclosure) if url else ""
    blocks: list[PostBlock] = []
    # 네이버 쇼핑 커넥트 글은 네이버가 대가성 문구를 상단에 자동으로 붙인다
    if disclosure and shopping_connect.platform(url) != shopping_connect.PLATFORM_NAVER:
        blocks.append(PostBlock("notice", disclosure))
    blocks.append(PostBlock("video", blog["video"], query=title))
    blocks += [PostBlock("text", p) for p in blog["paragraphs"]]
    if url:
        if blog.get("product_query"):
            blocks.append(PostBlock("product", f"구매 링크: {url}", query=blog["product_query"]))
        else:
            blocks.append(PostBlock("link", f"구매 링크: {url}", query=url))

    try:
        naver_cfg = dataclasses.replace(config.load_naver_config(), category=VIDEO_CATEGORY)
        naver_cfg.validate()
    except config.ConfigError as e:
        log(f"[중단] {e}")
        return 1
    naver_url = ""
    try:
        with NaverBlogPublisher(naver_cfg, debug=debug) as publisher:
            log("네이버 로그인 중...")
            publisher.ensure_login()
            log(f"네이버 블로그 '{VIDEO_CATEGORY}' 카테고리에 동영상 글 올리는 중... (태그: {', '.join(tags)})")
            # 네이버 태그는 띄어쓰기를 받지 않는다
            message = publisher.publish(title, blocks, [t.replace(" ", "") for t in tags])
            naver_url = publisher.post_url
    except NaverBlogError as e:
        log(f"[중단] 네이버 업로드 실패: {e}")
        log(f"  다시 올리려면: python shorts/app.py --upload-from \"{run_dir}\"")
        return 1
    log(message)
    if naver_url:
        (run_dir / "naver.url").write_text(naver_url + "\n", encoding="utf-8")

    if no_tistory:
        return 0
    return upload_tistory(run_dir, blog, naver_url, disclosure, debug=debug)


def upload_tistory(run_dir: Path, blog: dict, naver_url: str, disclosure: str, *, debug: bool) -> int:
    """티스토리에는 대표 사진·소개글·네이버 동영상 글 링크·구매 버튼을 올린다."""
    import dataclasses

    import config
    from core.models import KIND_CTA, KIND_IMAGE, KIND_LINK, KIND_PARAGRAPH, Article, Block, ImageAsset
    from publish.tistory import TistoryDailyLimit, TistoryError, TistoryPublisher

    cfg = config.load_tistory_config()
    if not cfg.blog_name:
        log("티스토리는 건너뛱니다. .env 의 TISTORY_BLOG_NAME 을 채우면 같이 올라갑니다.")
        return 0
    if TISTORY_VIDEO_CATEGORY:
        cfg = dataclasses.replace(cfg, category=TISTORY_VIDEO_CATEGORY)

    title, url, tags = blog["title"], blog.get("url") or "", blog.get("tags") or []
    blocks: list[Block] = []
    if blog.get("thumbnail"):
        thumb = ImageAsset(source="thumbnail", path=Path(blog["thumbnail"]), caption=title)
        blocks.append(Block(KIND_IMAGE, image=thumb))
    blocks += [Block(KIND_PARAGRAPH, text=p) for p in blog["paragraphs"]]
    if naver_url:
        blocks.append(Block(KIND_LINK, text="▶ 쇼츠 영상으로 보기", href=naver_url))
    if url:
        blocks.append(Block(KIND_CTA, text="상품 보러 가기", href=url))
    article = Article(title=title, blocks=blocks, tags=tags, disclosure=disclosure)

    try:
        cfg.validate()
        with TistoryPublisher(cfg, debug=debug) as publisher:
            log(f"티스토리 올리는 중... ({cfg.category or '기본 카테고리'})")
            result = publisher.publish(title, naver_url, tags, article=article)
    except (TistoryError, config.ConfigError) as e:
        log(f"[중단] 티스토리 업로드 실패: {e}")
        log(f"  티스토리만 다시 올리려면: python shorts/app.py --upload-from \"{run_dir}\" --tistory-only")
        return TISTORY_LIMITED if isinstance(e, TistoryDailyLimit) else 1
    if not cfg.wants_draft():
        (run_dir / "tistory.url").write_text(result + "\n", encoding="utf-8")
    log(f"티스토리 {'임시저장' if cfg.wants_draft() else '발행'} 완료: {result}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="상품 URL -> 유튜브 쇼츠 영상")
    ap.add_argument("url", nargs="*", help="구매 링크 (네이버 스마트스토어·브랜드커넥트 제휴 링크, 알리익스프레스 등). --each 와 같이 여러 개")
    ap.add_argument("--each", action="store_true", help="구매 링크마다 쇼츠를 하나씩 순서대로 만든다 (실패해도 다음 상품 계속)")
    ap.add_argument("--page-url", action="append", default=[],
                    help="구매 링크로 안 읽힐 때 내용을 읽어 올 실제 판매 페이지. 링크와 같은 개수로 주면 순서대로 짝짓는다")
    ap.add_argument("--title", help="영상에 넣을 상품명 (지정하면 크롤링한 이름 대신 사용)")
    ap.add_argument("--font", help="글꼴 파일 경로 (.ttf/.ttc)")
    ap.add_argument("--images-dir", type=Path, help="크롤링 대신(또는 부족분을) 쓸 로컬 이미지 폴더")
    ap.add_argument("--videos-dir", type=Path, help="같이 쓸 상품 영상 폴더 (mp4/mov)")
    ap.add_argument("--out", type=Path, help="결과 폴더 (기본: SHORTS_OUTPUT_DIR 또는 shorts/output)")
    ap.add_argument("--browser", action="store_true", help="브라우저 창을 띄워 수집 (캡차 직접 해결)")
    ap.add_argument("--no-ai", action="store_true", help="AI 사진 선별·제목·대본 없이 만든다")
    ap.add_argument("--no-tts", action="store_true", help="음성 없이 자막만 넣는다")
    ap.add_argument("--bgm", type=Path, default=os.getenv("SHORTS_BGM") or None, help="배경음악 파일")
    ap.add_argument("--upload", action="store_true", help="만든 뒤 네이버 블로그(동영상 글)와 티스토리에 올린다")
    ap.add_argument("--upload-from", type=Path, help="이미 만든 결과 폴더를 올린다 (영상은 다시 만들지 않는다)")
    ap.add_argument("--no-tistory", action="store_true", help="올릴 때 티스토리는 건너뛴다")
    ap.add_argument("--tistory-only", action="store_true", help="--upload-from 과 함께: 티스토리에만 올린다")
    ap.add_argument("--debug", action="store_true", help="업로드 화면을 storage/screenshots 에 남긴다")
    args = ap.parse_args(argv)

    if args.upload_from:
        return upload_from(args.upload_from, no_tistory=args.no_tistory,
                           tistory_only=args.tistory_only, debug=args.debug)
    urls = args.url
    if not urls and not args.images_dir and not args.videos_dir:
        ap.error("상품 URL, --images-dir, --videos-dir 중 하나는 필요합니다.")
    if len(urls) > 1 and not args.each:
        ap.error("구매 링크를 여러 개 주려면 --each 를 붙이세요. 상품마다 쇼츠를 하나씩 만듭니다.")
    if len(urls) > 1 and (args.title or args.images_dir or args.videos_dir):
        log("[안내] --each 에서는 --title, --images-dir, --videos-dir 를 쓰지 않습니다. 모든 상품에 같은 값이 들어가기 때문입니다.")
        args.title = args.images_dir = args.videos_dir = None
    if args.page_url and len(args.page_url) != max(len(urls), 1):
        log("[안내] --page-url 개수가 구매 링크와 달라 쓰지 않습니다.")
        args.page_url = []
    jobs = list(zip(urls, args.page_url or [""] * len(urls))) or [(None, "")]
    if args.upload and not check_logins(no_tistory=args.no_tistory, debug=args.debug):
        return 1

    rows: list[tuple[int, str, str, str, Made | None]] = []
    tistory_pending: list[Path] = []
    no_tistory = args.no_tistory
    for i, (url, page_url) in enumerate(jobs, 1):
        if len(jobs) > 1:
            log(f"\n{'=' * 24} {i}/{len(jobs)}  {url}")
        try:
            made = make_shorts(args, url, page_url)
        except Exception as e:  # 한 상품이 예상 못한 이유로 실패해도 다음 상품은 만든다
            log(f"[중단] {e.__class__.__name__}: {e}")
            made = f"{e.__class__.__name__}: {e}"
        if isinstance(made, str):
            rows.append((i, url or "", "실패", made, None))
            continue
        status = "완료"
        if args.upload:
            uploaded = upload_blogs(made.out_dir, made.blog, no_tistory=no_tistory, debug=args.debug)
            if uploaded == TISTORY_LIMITED:
                log("티스토리 하루 공개 발행 한도에 걸렸습니다. 남은 상품은 네이버에만 올립니다.")
                no_tistory = True
                tistory_pending.append(made.out_dir)
                status = "완료(티스토리 보류)"
            elif uploaded:
                status = "업로드 실패"
            elif no_tistory and not args.no_tistory:
                tistory_pending.append(made.out_dir)
                status = "완료(티스토리 보류)"
        rows.append((i, url or "", status, made.title, made))

    if len(jobs) > 1:
        print_summary(rows, tistory_pending)
    return 0 if all(row[2].startswith("완료") for row in rows) else 1


@dataclass
class Made:
    out_dir: Path
    title: str
    blog: dict
    cost: dict


def make_shorts(args, url: str | None, page_url: str) -> Made | str:
    """쇼츠 하나를 만든다. 실패하면 그 이유를 문자열로 돌려준다."""
    use_ai = not args.no_ai
    budget = read_budget()

    product = Product()
    pipe = shop = None
    ali = bool(url) and is_aliexpress(page_url or url)
    if url and ali:
        try:
            product = crawl(page_url or url, headed=args.browser)
        except Exception as e:
            log(f"[경고] 크롤링 중 예외: {e}")
    elif url:
        collected = collect_shop(url, page_url, args.browser)
        if collected is None:
            return "상품 페이지를 읽지 못했습니다"
        pipe, shop = collected
        product = Product(title=shop.title)

    folder_title = clean_title(args.title or product.title) or DEFAULT_TITLE
    base_dir = args.out or Path(os.getenv("SHORTS_OUTPUT_DIR") or APP_DIR / "output")
    if not base_dir.is_absolute():
        base_dir = ROOT / base_dir
    out_dir = base_dir / f"{datetime.now():%Y%m%d_%H%M%S}_{safe_name(folder_title)}"
    out_dir.mkdir(parents=True, exist_ok=True)

    limit = CANDIDATE_IMAGES if use_ai else MAX_SCENES
    paths: list[Path] = []
    if shop is not None:
        paths = shop_images(pipe, shop, out_dir, limit)
    elif product.image_urls:
        log("이미지 내려받는 중...")
        paths = download_images(product.image_urls, out_dir / "images", limit)
    if len(paths) < MIN_SCENES and args.images_dir:
        paths += load_local_images(args.images_dir)[: limit - len(paths)]
    paths = [p for p in paths if open_image(p) is not None]
    photos = [Visual(str(k), p) for k, p in enumerate(paths, 1)]

    try:
        videos = collect_videos(shop, out_dir, args.videos_dir)
    except ImportError:
        log("[중단] moviepy 2.x 가 필요합니다: pip install \"moviepy>=2\"")
        return "moviepy 2.x 가 없습니다"
    if not photos and not videos:
        log("[중단] 쓸 사진·영상이 없습니다. --images-dir 나 --videos-dir 로 폴더를 지정해 다시 실행하세요.")
        return "쓸 사진·영상이 없습니다"

    if shop is not None:
        context = shop.as_prompt_context(3000)
    else:
        context = f"[상품명] {clean_title(args.title or product.title) or '(없음)'}"

    plan = None
    kw: dict = {}
    client = make_client() if use_ai else None
    if client:
        kw = ai_keywords(client, context)
        attach = [p.path for p in photos]
        attach += [s for v in videos if (s := contact_sheet(v.path, v.seconds, out_dir / "videos" / f"{v.label}_sheet.jpg"))]
        plan = ai_plan(client, photos + videos, attach, context, kw)
    if plan is None:
        order = photos[:1] + videos + photos[1:]
        plan = Plan(scenes=[Scene(v) for v in order[:MAX_SCENES]],
                    keyword=str(kw.get("main_keyword") or "").strip(),
                    keywords=strs(kw.get("keywords")), hashtags=strs(kw.get("hashtags")),
                    blog_tags=strs(kw.get("blog_tags")))
    scenes = plan.scenes
    title = clean_title(args.title or plan.title or product.title) or DEFAULT_TITLE

    if not any(sc.narration for sc in scenes):
        scenes[0].narration = f"오늘은 {title}, 소개해 드릴게요."
    cta = CTA_NARRATION if ali else LINK_CTA
    scenes.append(Scene(photos[0] if photos else videos[0], cta))

    font_path = find_font(args.font)
    video = out_dir / "shorts.mp4"
    log("음성 만드는 중..." if not args.no_tts else "음성 없이 자막만 넣습니다.")
    try:
        prepare_audio(scenes, out_dir / "audio", use_tts=not args.no_tts)
        fit_length(scenes, TARGET_SECONDS)
        log(f"영상 만드는 중... (장면 {len(scenes)}개, {sum(sc.duration for sc in scenes):.1f}초)")
        seconds = render(scenes, plan.hook or [title], CTA_HOOK if ali else LINK_HOOK,
                         font_path, args.bgm, video)
    except ImportError:
        log("[중단] moviepy 2.x 가 필요합니다: pip install \"moviepy>=2\"")
        return "moviepy 2.x 가 없습니다"
    except Exception as e:
        log(f"[중단] 영상 인코딩 실패: {e}")
        return f"영상 인코딩 실패: {e}"

    youtube = youtube_meta(plan, title, url, disclosure_for(url))
    save_youtube(youtube, out_dir)
    blog = blog_post(plan, title, url, shop.title if shop is not None else "",
                     photos[0].path if photos else None, video)
    ai_clients = [c for c in (client, pipe.client if pipe is not None else None) if c is not None]
    cost = cost_report(budget, sum(c.calls for c in ai_clients), sum(c.cost for c in ai_clients))

    (out_dir / "meta.json").write_text(json.dumps({
        "url": url,
        "page_url": shop.page_url if shop is not None else page_url or None,
        "title": title,
        "source_title": product.title,
        "price": shop.price if shop is not None else None,
        "ai": bool(plan.title),
        "hook": plan.hook,
        "youtube": youtube,
        "blog": blog,
        "videos": [{"label": v.label, "file": str(v.path), "seconds": round(v.seconds, 2)} for v in videos],
        "voice": None if args.no_tts else f"{TTS_VOICE} rate={TTS_RATE} pitch={TTS_PITCH}",
        "image_urls": shop.image_urls if shop is not None else product.image_urls,
        "scenes": [{"visual": sc.visual.label, "file": str(sc.visual.path), "narration": sc.narration,
                    "offset": round(sc.offset, 2), "seconds": round(sc.duration, 2)} for sc in scenes],
        "seconds": round(seconds, 2),
        "font": font_path,
        "cost": cost,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"완료: {video} ({seconds:.1f}초)")
    log(f"업로드 문구: {out_dir / 'youtube.txt'}")
    print_cost(cost)
    if not args.upload:
        log(f"블로그에 올리려면: python shorts/app.py --upload-from \"{out_dir}\"")
    return Made(out_dir, title, blog, cost)


def print_summary(rows: list[tuple[int, str, str, str, Made | None]], tistory_pending: list[Path]) -> None:
    log("\n" + "=" * 24 + " 순차 생성 결과")
    for i, url, status, info, made in rows:
        log(f"  {i}. [{status}] {info}")
        log(f"     {url}")
        if made:
            log(f"     {made.out_dir}")
    costs = [made.cost for *_, made in rows if made]
    reported = sum(c.get("reported_cost", 0) for c in costs)
    used = [c["budget_used"] for c in costs if "budget_used" in c]
    log(f"  총 비용: AI ${reported:.4f} (호출 응답 기준)"
        + (f" / 계정 예산 사용액 변화 ${sum(used):.4f}" if used else "")
        + "  * 실패한 상품의 비용은 빠져 있습니다" * any(not made for *_, made in rows))
    if tistory_pending:
        log("\n티스토리에 올리지 못한 글입니다. 내일 아래 명령으로 올리세요.")
        for run_dir in tistory_pending:
            log(f"  python shorts/app.py --upload-from \"{run_dir}\" --tistory-only")


def check_logins(*, no_tistory: bool, debug: bool) -> bool:
    """쇼츠를 만들기 전에 올릴 곳의 로그인부터 확인한다. AI 비용을 쓰고 나서 막히지 않게 한다."""
    sys.path.insert(0, str(ROOT))
    import config
    from publish.naver_blog import NaverBlogError, NaverBlogPublisher
    from publish.tistory import TistoryError, TistoryPublisher

    try:
        naver_cfg = config.load_naver_config()
        naver_cfg.validate()
        log("네이버 로그인 확인 중...")
        with NaverBlogPublisher(naver_cfg, debug=debug) as publisher:
            publisher.ensure_login()
        tistory_cfg = config.load_tistory_config()
        if not no_tistory and tistory_cfg.blog_name:
            tistory_cfg.validate()
            log("티스토리 로그인 확인 중...")
            with TistoryPublisher(tistory_cfg, debug=debug) as publisher:
                publisher.ensure_login()
    except (config.ConfigError, NaverBlogError, TistoryError) as e:
        log(f"[중단] 로그인 확인 실패: {e}")
        return False
    log("로그인 확인됨")
    return True


if __name__ == "__main__":
    sys.exit(main())
