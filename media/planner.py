"""슬롯별로 어떤 이미지를 쓸지 결정한다.

전체 우선순위는 상세페이지 > 대표 이미지 > 스톡 > AI 생성이다. 다만 슬롯마다 어울리는
출처가 다르다. "실사용 시나리오"에 상세페이지의 스펙 이미지를 넣으면 어색하고,
"주요 특징"에 스톡 사진을 넣으면 정보가 없다. 그래서 슬롯별로 선호 순서를 따로 두되,
가용한 실제 상품 이미지가 있으면 AI 생성은 쓰지 않는다는 원칙은 지킨다.
"""

from __future__ import annotations

from pathlib import Path

from core.models import (
    SLOT_CTA,
    SLOT_FEATURE,
    SLOT_HERO,
    SLOT_INFOGRAPHIC,
    SLOT_LIFESTYLE,
    SLOT_PRODUCT,
    ImageAsset,
    ProductBrief,
)

#: 슬롯별 출처 선호 순서. 앞에 있을수록 먼저 고른다.
SLOT_PREFERENCE: dict[str, tuple[str, ...]] = {
    SLOT_HERO: ("thumbnail", "detail", "stock", "ai"),
    SLOT_PRODUCT: ("detail", "thumbnail", "stock", "ai"),
    SLOT_FEATURE: ("detail", "thumbnail", "stock", "ai"),
    SLOT_LIFESTYLE: ("stock", "ai", "detail", "thumbnail"),
    SLOT_INFOGRAPHIC: ("detail", "stock", "ai", "thumbnail"),
    SLOT_CTA: ("thumbnail", "stock", "ai", "detail"),
}

#: 슬롯별 스톡 사진 검색어를 만들 때 붙이는 영어 힌트.
SLOT_QUERY_HINT: dict[str, str] = {
    SLOT_HERO: "product flat lay",
    SLOT_PRODUCT: "product close up",
    SLOT_FEATURE: "detail macro",
    SLOT_LIFESTYLE: "person using daily life",
    SLOT_INFOGRAPHIC: "organized desk overhead",
    SLOT_CTA: "lifestyle warm light",
}


class ImagePlanner:
    def __init__(self, stock=None, generator=None, processor=None, *, image_dir: Path | None = None):
        self.stock = stock
        self.generator = generator
        self.processor = processor
        self.image_dir = image_dir or Path("images")

    def assign(
        self,
        slots: list[str],
        available: list[ImageAsset],
        brief: ProductBrief,
        *,
        stock_query: str = "",
    ) -> dict[str, ImageAsset]:
        """슬롯 목록에 이미지를 하나씩 배정한다. 같은 이미지를 두 번 쓰지 않는다."""
        pool = list(available)
        used_hashes: set[int] = set()
        result: dict[str, ImageAsset] = {}

        for slot in slots:
            asset = self._pick_from_pool(slot, pool, used_hashes)

            if asset is None:
                asset = self._fetch_stock(slot, stock_query, used_hashes)
            if asset is None:
                asset = self._generate(slot, brief)

            if asset is None:
                continue

            asset.slot = slot
            if asset.phash is not None:
                used_hashes.add(asset.phash)
            if asset in pool:
                pool.remove(asset)
            result[slot] = asset

        return result

    def _pick_from_pool(self, slot: str, pool: list[ImageAsset], used: set[int]) -> ImageAsset | None:
        for source in SLOT_PREFERENCE.get(slot, ("detail", "thumbnail", "stock", "ai")):
            if source in ("stock", "ai"):
                continue  # 이 둘은 미리 받아둔 게 아니라 필요할 때 가져온다
            candidates = [
                a
                for a in pool
                if a.source == source and a.path and (a.phash is None or a.phash not in used)
            ]
            if candidates:
                return max(candidates, key=lambda a: a.score)
        return None

    def _fetch_stock(self, slot: str, query: str, used: set[int]) -> ImageAsset | None:
        if not self.stock or not self.processor:
            return None

        search = f"{query} {SLOT_QUERY_HINT.get(slot, '')}".strip()
        try:
            found = self.stock.search(search, per_page=3)
        except Exception:
            return None

        prepared = self.processor.prepare(found, self.image_dir / "stock")
        for asset in prepared:
            if asset.phash is None or asset.phash not in used:
                return asset
        return None

    def _generate(self, slot: str, brief: ProductBrief) -> ImageAsset | None:
        if not self.generator or not self.generator.enabled:
            return None

        subject = brief.one_liner or brief.category
        scene = brief.scenarios[0].situation if brief.scenarios else ""
        return self.generator.generate(slot, subject, brief.category, self.image_dir / "ai", scene)
