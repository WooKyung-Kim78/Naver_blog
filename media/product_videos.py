"""상품 페이지의 동영상(대표 영상·상세설명 영상)을 찾아 내려받는다. 쇼츠 생성기가 쓴다.

스마트스토어 영상은 네이버 동영상 플레이어로 재생되고, 플레이어가 받아 오는 재생 정보(JSON)에
화질별 mp4 주소가 들어 있다. 페이지를 열고 상세설명을 펼쳐 스크롤하는 동안 오가는 응답과
화면의 <video> 를 함께 살펴 영상 주소를 모은다. 판매자가 올린 영상이므로 해당 상품을 소개하는
용도로만 쓴다.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests

MP4_RE = re.compile(r"https?://[^\s\"'<>\\]+?\.mp4(?:\?[^\s\"'<>\\]*)?", re.I)
M3U8_RE = re.compile(r"https?://[^\s\"'<>\\]+?\.m3u8(?:\?[^\s\"'<>\\]*)?", re.I)
PLAY_RE = re.compile(r"/vod/play/v\d+\.\d+/([^/?]+)")
MAX_VIDEOS = 4
MAX_BYTES = 120 * 1024 * 1024

_PLAY_BUTTONS = (
    ".se-video button, .se-module-video button, [class*='video'] [class*='play'], "
    ".u_rmc_play_btn, .pzp-playback-switch, button[aria-label*='재생']"
)

_DOM_JS = """
() => {
  const out = [];
  for (const v of document.querySelectorAll('video')) {
    for (const s of [v.currentSrc, v.src, ...Array.from(v.querySelectorAll('source')).map(x => x.src)]) {
      if (s && !s.startsWith('blob:')) out.push(s);
    }
  }
  let state = '';
  try { state = JSON.stringify(window.__PRELOADED_STATE__ || {}); } catch (e) {}
  return {srcs: out, state};
}
"""


@dataclass
class VideoSource:
    url: str
    kind: str  # mp4 / hls
    height: int = 0
    group: str = ""  # 같은 영상의 화질별 주소를 묶는 키


def collect(url: str) -> tuple[list[VideoSource], str]:
    """(찾은 영상 주소, 실제로 연 페이지 주소). 실패해도 예외를 던지지 않는다."""
    try:
        from playwright.sync_api import sync_playwright

        from scrape.browser import new_context
        from scrape.detail_ui import expand_collapsed_detail, open_detail_tab
    except ImportError:
        return [], url

    found: list[VideoSource] = []
    final_url = url

    def on_response(resp) -> None:
        try:
            u = resp.url
            ctype = (resp.headers.get("content-type") or "").lower()
            if "/vod/play/" in u and "json" in ctype:
                m = PLAY_RE.search(u)
                found.extend(_from_json(resp.json(), m.group(1) if m else u))
            elif ctype.startswith("video/mp4") or MP4_RE.fullmatch(u):
                found.append(VideoSource(u, "mp4", group=_key(u)))
            elif "mpegurl" in ctype or M3U8_RE.fullmatch(u):
                found.append(VideoSource(u, "hls", group=_key(u)))
        except Exception:
            pass

    try:
        with sync_playwright() as p:
            browser, context = new_context(p)
            page = context.new_page()
            page.on("response", on_response)
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(3000)
            final_url = page.url
            _click_play(page)
            open_detail_tab(page)
            for _ in range(14):
                expand_collapsed_detail(page)
                page.mouse.wheel(0, 2200)
                page.wait_for_timeout(700)
            _click_play(page)
            page.wait_for_timeout(2500)
            dom = page.evaluate(_DOM_JS) or {}
            for src in dom.get("srcs") or []:
                kind = "hls" if ".m3u8" in src else "mp4"
                found.append(VideoSource(src, kind, group=_key(src)))
            state = dom.get("state") or ""
            for m in MP4_RE.finditer(state):
                found.append(VideoSource(m.group(0), "mp4", group=_key(m.group(0))))
            browser.close()
    except Exception:
        pass
    return _best_per_group(found), final_url


def download(sources: list[VideoSource], dest: Path, *, referer: str,
              limit: int = MAX_VIDEOS, report=print) -> list[Path]:
    """화질 좋은 주소부터 내려받는다. 다른 영상이 limit 개 모이면 멈춘다."""
    dest.mkdir(parents=True, exist_ok=True)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Referer": referer,
    }
    saved: list[Path] = []
    for src in sources:
        if len(saved) >= limit:
            break
        out = dest / f"V{len(saved) + 1}.mp4"
        try:
            if src.kind == "hls":
                _download_hls(src.url, out, referer)
            else:
                _download_mp4(src.url, out, headers)
        except Exception as exc:  # noqa: BLE001 - 한 편 실패로 나머지를 버리지 않는다
            report(f"  [건너뜀] 영상 {urlparse(src.url).netloc} ({exc.__class__.__name__})")
            out.unlink(missing_ok=True)
            continue
        if out.exists() and out.stat().st_size > 50_000:
            saved.append(out)
            report(f"  영상 저장 {out.name} ({out.stat().st_size / 1e6:.1f}MB)")
        else:
            out.unlink(missing_ok=True)
    return saved


def _from_json(data, group: str) -> list[VideoSource]:
    """재생 정보 JSON 어디에 있든 mp4·m3u8 주소와 세로 해상도를 꺼낸다."""
    out: list[VideoSource] = []

    def walk(obj, height: int = 0) -> None:
        if isinstance(obj, dict):
            opt = obj.get("encodingOption") or {}
            h = int(opt.get("height") or obj.get("height") or height or 0)
            src = obj.get("source")
            if isinstance(src, str):
                if ".m3u8" in src:
                    out.append(VideoSource(src, "hls", h, group))
                elif MP4_RE.match(src):
                    out.append(VideoSource(src, "mp4", h, group))
            for v in obj.values():
                walk(v, h)
        elif isinstance(obj, list):
            for v in obj:
                walk(v, height)

    walk(data)
    return out


def _click_play(page) -> None:
    """플레이어는 재생 버튼을 눌러야 재생 정보를 받아 오는 경우가 있다."""
    try:
        buttons = page.locator(_PLAY_BUTTONS)
        for i in range(min(buttons.count(), 6)):
            try:
                buttons.nth(i).click(timeout=1500)
                page.wait_for_timeout(1200)
            except Exception:
                continue
    except Exception:
        pass


def _key(url: str) -> str:
    return url.split("?", 1)[0]


def _best_per_group(found: list[VideoSource]) -> list[VideoSource]:
    """같은 영상은 mp4 우선, 그다음 1080p 이하에서 가장 높은 화질 하나만 남긴다."""
    groups: dict[str, list[VideoSource]] = {}
    seen: set[str] = set()
    for src in found:
        if _key(src.url) in seen:
            continue
        seen.add(_key(src.url))
        groups.setdefault(src.group or _key(src.url), []).append(src)
    best = []
    for items in groups.values():
        items.sort(key=lambda s: (s.kind == "mp4", s.height <= 1080, s.height), reverse=True)
        best.append(items[0])
    return best


def _download_mp4(url: str, out: Path, headers: dict) -> None:
    with requests.get(url, headers=headers, stream=True, timeout=60) as r:
        r.raise_for_status()
        size = 0
        with open(out, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("영상이 너무 큽니다")
                f.write(chunk)


def _download_hls(url: str, out: Path, referer: str) -> None:
    import imageio_ffmpeg

    cmd = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
        "-headers", f"Referer: {referer}\r\n",
        "-i", url, "-c", "copy", "-bsf:a", "aac_adtstoasc", str(out),
    ]
    subprocess.run(cmd, check=True, timeout=300, capture_output=True)
