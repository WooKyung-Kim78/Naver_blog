from __future__ import annotations

import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fakes import SECRET  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """결과는 임시 폴더에, 외부 API 키와 네트워크는 끊은 채로 돌린다."""
    import config

    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setattr(config, "OUTPUT_DIR", out)
    for key in ("PEXELS_API_KEY", "UNSPLASH_ACCESS_KEY", "IMAGE_GEN_BASE_URL", "IMAGE_GEN_API_KEY"):
        monkeypatch.setenv(key, "")
    for key in ("WRITER_MODE", "STAGED_SECTION_BATCH", "STAGED_MAX_FIX_ROUNDS", "STAGED_AI_REVIEW"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)

    def no_network(*args, **kwargs):
        raise RuntimeError("테스트에서는 네트워크를 쓰지 않습니다.")

    monkeypatch.setattr(requests.Session, "request", no_network)
    return out
