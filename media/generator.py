"""AI 이미지 생성.

Bayer myGenAssist API(docs/v3-api.yaml)에는 이미지 생성 엔드포인트가 없다. 그래서
외부 프로바이더를 꽂을 수 있는 형태로만 만들어 두고, 설정이 없으면 조용히 비활성화한다.
파이프라인은 생성기가 꺼져 있어도 상세페이지 이미지와 스톡 사진으로 동작한다.

OpenAI 호환 규격(POST {base}/images/generations)을 따르는 서비스면 .env 설정만으로 붙는다.
"""

from __future__ import annotations

import base64
from pathlib import Path

import requests

from config import ImageGenConfig
from core.models import ImageAsset
from media import prompts


class ImageGenerator:
    def __init__(self, cfg: ImageGenConfig):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.verify = cfg.verify_ssl
        if cfg.proxies:
            self.session.proxies.update(cfg.proxies)

    @property
    def enabled(self) -> bool:
        return self.cfg.enabled

    def generate(self, slot: str, subject: str, category: str, dest_dir: Path, scene: str = "") -> ImageAsset | None:
        if not self.enabled:
            return None

        prompt = prompts.build(slot, subject, category, scene)
        try:
            data = self._request(prompt)
        except Exception:
            return None
        if not data:
            return None

        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / f"ai_{slot}.png"
        path.write_bytes(data)

        return ImageAsset(source="ai", path=path, slot=slot, prompt=prompt, score=70.0)

    def _request(self, prompt: str) -> bytes | None:
        resp = self.session.post(
            f"{self.cfg.base_url.rstrip('/')}/images/generations",
            headers={
                "Content-Type": "application/json",
                self.cfg.auth_header: f"{self.cfg.auth_prefix} {self.cfg.api_key}".strip(),
            },
            json={
                "model": self.cfg.model,
                "prompt": prompt,
                "n": 1,
                "size": self.cfg.size,
            },
            timeout=180,
        )
        resp.raise_for_status()

        entries = resp.json().get("data") or []
        if not entries:
            return None

        entry = entries[0]
        if entry.get("b64_json"):
            return base64.b64decode(entry["b64_json"])
        if entry.get("url"):
            return self.session.get(entry["url"], timeout=120).content
        return None
