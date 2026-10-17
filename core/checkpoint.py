"""단계별 결과를 결과 폴더의 stages/ 에 저장해, 실패한 지점부터 다시 실행한다.

각 저장본에는 그 단계에 들어간 입력의 해시를 함께 남긴다. 앞 단계 결과가 바뀌면
해시가 달라져 뒤 단계 저장본은 자동으로 무효가 된다.
"""

from __future__ import annotations

import dataclasses
import functools
import hashlib
import json
import os
import types
import typing
from datetime import datetime
from pathlib import Path
from typing import Any

from core.models import ImageAsset, Product

STAGES_DIR = "stages"

#: 저장본에 섞여 들어가면 안 되는 값. 혹시 들어가도 파일에 쓰기 전에 가린다.
SECRET_ENV_KEYS = (
    "AI_API_KEY",
    "GEMINI_API_KEY",
    "PEXELS_API_KEY",
    "UNSPLASH_ACCESS_KEY",
    "IMAGE_GEN_API_KEY",
    "NAVER_PW",
)


# ------------------------------------------------------------------ 직렬화


def to_data(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_data(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, Path):
        return obj.as_posix()
    if isinstance(obj, (list, tuple)):
        return [to_data(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): to_data(v) for k, v in obj.items()}
    return obj


@functools.lru_cache(maxsize=None)
def _hints(cls: type) -> dict[str, Any]:
    return typing.get_type_hints(cls)


def from_data(tp: Any, data: Any) -> Any:
    """JSON 값을 타입에 맞춰 되살린다. 형이 틀리거나 빠진 값은 빈 값으로 채운다.

    AI 응답도 이 함수로 받아서, 문자열 자리에 숫자가 오거나 목록 자리에 문자열이
    와도 죽지 않게 한다.
    """
    origin = typing.get_origin(tp)

    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        if data is None or not args:
            return None
        return from_data(args[0], data)

    if origin in (list, tuple):
        (inner,) = typing.get_args(tp) or (Any,)
        if isinstance(data, (str, dict)) or not isinstance(data, list):
            data = [data] if isinstance(data, str) and data.strip() and inner is str else []
        if dataclasses.is_dataclass(inner):
            return [from_data(inner, x) for x in data if isinstance(x, dict)]
        values = [from_data(inner, x) for x in data if x is not None]
        return [v for v in values if v.strip()] if inner is str else values

    if origin is dict:
        _, inner = typing.get_args(tp) or (str, Any)
        if not isinstance(data, dict):
            return {}
        return {str(k): from_data(inner, v) for k, v in data.items()}

    if dataclasses.is_dataclass(tp):
        if not isinstance(data, dict):
            data = {}
        hints = _hints(tp)
        kwargs = {}
        for f in dataclasses.fields(tp):
            if f.name in data:
                kwargs[f.name] = from_data(hints[f.name], data[f.name])
            elif f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
                kwargs[f.name] = _zero(hints[f.name])
        return tp(**kwargs)

    if tp is str:
        # 저장본을 그대로 되살려야 입력 해시가 같게 나온다. 공백을 다듬지 않는다.
        if data is None or isinstance(data, (dict, list)):
            return ""
        return str(data)
    if tp is bool:
        if isinstance(data, str):
            return data.strip().lower() in ("1", "true", "yes", "y")
        return bool(data)
    if tp is int:
        try:
            return int(float(data))
        except (TypeError, ValueError):
            return 0
    if tp is float:
        try:
            return float(data)
        except (TypeError, ValueError):
            return 0.0
    if tp is Path:
        return Path(data) if data else None
    return data


def _zero(tp: Any) -> Any:
    origin = typing.get_origin(tp)
    if origin in (list, tuple):
        return []
    if origin is dict:
        return {}
    if tp is str:
        return ""
    if tp in (int, float):
        return tp()
    if tp is bool:
        return False
    return None


def digest(*parts: Any) -> str:
    raw = json.dumps(to_data(list(parts)), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def scrub(text: str) -> str:
    for key in SECRET_ENV_KEYS:
        value = (os.getenv(key) or "").strip()
        if len(value) >= 6:
            text = text.replace(value, "***")
    return text


# ------------------------------------------------------------------ 저장소


class Checkpoint:
    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)

    @property
    def dir(self) -> Path:
        return self.run_dir / STAGES_DIR

    def exists(self, name: str) -> bool:
        return (self.dir / f"{name}.json").exists()

    def save(self, name: str, data: Any, input_hash: str = "") -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        record = {
            "stage": name,
            "input_hash": input_hash,
            "saved_at": f"{datetime.now():%Y-%m-%d %H:%M:%S}",
            "data": to_data(data),
        }
        text = json.dumps(record, ensure_ascii=False, indent=2, default=str)
        (self.dir / f"{name}.json").write_text(scrub(text), encoding="utf-8")

    def load(self, name: str, input_hash: str | None = None) -> Any:
        """저장본이 없거나 입력이 달라졌으면 None. input_hash 를 비우면 확인하지 않는다."""
        path = self.dir / f"{name}.json"
        if not path.exists():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if input_hash is not None and record.get("input_hash") != input_hash:
            return None
        return record.get("data")

    def delete(self, name: str) -> None:
        path = self.dir / f"{name}.json"
        if path.exists():
            path.unlink()

    # -- 상품과 이미지. 폴더 이름이 바뀌어도 쓸 수 있게 이미지 경로는 상대 경로로 둔다.

    def save_product(self, product: Product, assets: list[ImageAsset]) -> None:
        rows = []
        for asset in assets:
            row = to_data(asset)
            if asset.path:
                row["path"] = self._relative(asset.path)
            rows.append(row)
        self.save("00_product", {"product": product, "assets": rows})

    def load_product(self) -> tuple[Product, list[ImageAsset]] | None:
        data = self.load("00_product")
        if not isinstance(data, dict):
            return None
        product = from_data(Product, data.get("product"))
        assets = []
        for row in data.get("assets") or []:
            asset = from_data(ImageAsset, row)
            if asset.path and not asset.path.is_absolute():
                asset.path = self.run_dir / asset.path
            assets.append(asset)
        return product, assets

    def _relative(self, path: Path) -> str:
        try:
            return Path(path).resolve().relative_to(self.run_dir.resolve()).as_posix()
        except ValueError:
            return Path(path).as_posix()
