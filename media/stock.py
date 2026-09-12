"""무료 스톡 이미지(Pexels / Unsplash) 검색."""

from __future__ import annotations

import requests

from config import ImageConfig
from core.models import ImageAsset


class StockImageFetcher:
    def __init__(self, cfg: ImageConfig):
        cfg.validate()
        self.cfg = cfg
        self.session = requests.Session()
        self.session.verify = cfg.verify_ssl
        if cfg.proxies:
            self.session.proxies.update(cfg.proxies)

    def search(self, query: str, *, per_page: int = 3) -> list[ImageAsset]:
        if self.cfg.provider == "unsplash":
            return self._search_unsplash(query, per_page)
        return self._search_pexels(query, per_page)

    def _search_pexels(self, query: str, per_page: int) -> list[ImageAsset]:
        resp = self.session.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": self.cfg.pexels_key},
            params={"query": query, "per_page": per_page, "orientation": "landscape"},
            timeout=30,
        )
        if resp.status_code == 401:
            raise RuntimeError("Pexels 인증 실패. PEXELS_API_KEY 를 확인하세요.")
        resp.raise_for_status()

        return [
            ImageAsset(
                source="stock",
                url=photo["src"]["large"],
                credit=f"Photo by {photo.get('photographer', 'Unknown')} on Pexels",
                caption=query,
            )
            for photo in resp.json().get("photos", [])
        ]

    def _search_unsplash(self, query: str, per_page: int) -> list[ImageAsset]:
        resp = self.session.get(
            "https://api.unsplash.com/search/photos",
            headers={"Authorization": f"Client-ID {self.cfg.unsplash_key}"},
            params={"query": query, "per_page": per_page, "orientation": "landscape"},
            timeout=30,
        )
        if resp.status_code == 401:
            raise RuntimeError("Unsplash 인증 실패. UNSPLASH_ACCESS_KEY 를 확인하세요.")
        resp.raise_for_status()

        return [
            ImageAsset(
                source="stock",
                url=photo["urls"]["regular"],
                credit=f"Photo by {photo['user']['name']} on Unsplash",
                caption=query,
            )
            for photo in resp.json().get("results", [])
        ]
