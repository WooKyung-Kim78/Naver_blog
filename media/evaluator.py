"""이미지를 내려받아 품질을 매기고 중복을 걸러낸다.

중복 판정은 지각 해시(dHash)를 쓴다. 쇼핑몰 상세페이지는 같은 사진을 크기만 바꿔
여러 번 쓰는 경우가 흔한데, URL 비교로는 이걸 못 잡는다.
"""

from __future__ import annotations

import io
import re
from dataclasses import replace
from pathlib import Path

import requests
from PIL import Image, ImageStat

from core.models import ImageAsset

#: 해밍 거리가 이 값 이하면 같은 이미지로 본다.
DUPLICATE_DISTANCE = 8

MIN_EDGE = 400
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class ImageProcessor:
    def __init__(self, *, verify_ssl: bool = True, proxies: dict | None = None):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA})
        self.session.verify = verify_ssl
        if proxies:
            self.session.proxies.update(proxies)

    def prepare(self, assets: list[ImageAsset], dest_dir: Path, *, referer: str = "") -> list[ImageAsset]:
        """내려받아 평가하고, 중복을 뺀 뒤 점수순으로 돌려준다."""
        dest_dir.mkdir(parents=True, exist_ok=True)
        kept: list[ImageAsset] = []

        for asset in assets:
            try:
                data = self._download(asset.url, referer)
                source_image = Image.open(io.BytesIO(data))
                source_image.load()
            except Exception:
                continue

            if min(source_image.size) < MIN_EDGE:
                continue

            for image in _split_tall(source_image):
                panel = replace(asset, path=None)
                panel.width, panel.height = image.size
                panel.phash = dhash(image)
                panel.score = quality_score(image)

                if panel.score < 25 or self._is_duplicate(panel, kept):
                    continue

                # 파일명에 지각 해시를 넣는다. 슬롯마다 따로 호출해도 이름이 겹치지 않고,
                # 같은 이미지는 같은 파일로 떨어져 디스크도 아낀다.
                panel.path = self._save(image, dest_dir, f"{panel.source}_{panel.phash:016x}")
                kept.append(panel)

        kept.sort(key=lambda a: (a.priority, -a.score))
        return kept

    def _download(self, url: str, referer: str) -> bytes:
        headers = {"Referer": referer} if referer else {}
        resp = self.session.get(url, headers=headers, timeout=40)
        resp.raise_for_status()
        return resp.content

    @staticmethod
    def _is_duplicate(asset: ImageAsset, kept: list[ImageAsset]) -> bool:
        if asset.phash is None:
            return False
        return any(
            other.phash is not None and hamming(asset.phash, other.phash) <= DUPLICATE_DISTANCE
            for other in kept
        )

    @staticmethod
    def _save(image: Image.Image, dest_dir: Path, name: str) -> Path:
        path = dest_dir / f"{_slug(name)}.jpg"
        rgb = image.convert("RGB")

        # 네이버 에디터는 가로 1200px 이면 충분하다. 더 크면 업로드만 느려진다.
        if rgb.width > 1200:
            ratio = 1200 / rgb.width
            rgb = rgb.resize((1200, int(rgb.height * ratio)), Image.LANCZOS)

        rgb.save(path, "JPEG", quality=88, optimize=True)
        return path


def _split_tall(image: Image.Image, *, trigger: float = 2.5, panel_aspect: float = 1.4,
                max_panels: int = 4) -> list[Image.Image]:
    """세로로 긴 상세페이지 이미지를 패널 단위로 자른다.

    네이버 상세설명은 860x5000 처럼 여러 장을 이어붙인 한 장인 경우가 많다.
    그대로 쓰면 본문에서 화면을 다 잡아먹으므로, 읽기 좋은 비율로 나눠 각각을
    독립된 이미지로 쓴다.
    """
    width, height = image.size
    if height <= width * trigger:
        return [image]

    panel_height = int(width * panel_aspect)
    count = min(max_panels, max(2, round(height / panel_height)))
    step = height // count

    return [image.crop((0, i * step, width, min((i + 1) * step, height))) for i in range(count)]


def dhash(image: Image.Image, size: int = 8) -> int:
    """인접 픽셀의 밝기 대소만 남기는 지각 해시. 크기와 압축률이 달라도 값이 비슷하다."""
    small = image.convert("L").resize((size + 1, size), Image.LANCZOS)
    pixels = list(small.getdata())

    bits = 0
    for row in range(size):
        offset = row * (size + 1)
        for col in range(size):
            bits = (bits << 1) | int(pixels[offset + col] > pixels[offset + col + 1])
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def quality_score(image: Image.Image) -> float:
    """해상도, 비율, 정보량을 합쳐 0~100 으로 매긴다."""
    width, height = image.size

    pixels = width * height
    resolution = min(pixels / 1_000_000, 1.0) * 40

    longer, shorter = max(width, height), min(width, height)
    ratio = longer / shorter if shorter else 99
    aspect = 30 if ratio <= 2.0 else max(0, 30 - (ratio - 2.0) * 15)

    # 표준편차가 낮으면 단색 배경이나 여백 이미지다.
    try:
        stat = ImageStat.Stat(image.convert("L"))
        detail = min(stat.stddev[0] / 60, 1.0) * 30
    except Exception:
        detail = 15

    return round(resolution + aspect + detail, 1)


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^\w가-힣]+", "_", text).strip("_").lower()
    return cleaned[:50] or "image"
