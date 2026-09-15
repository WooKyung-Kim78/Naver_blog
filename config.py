"""환경설정 로딩 모듈. 모든 설정은 .env 파일에서 읽어온다."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

try:
    # 사내망은 SSL 검사 장비가 자체 서명 인증서로 통신을 가로챈다. 파이썬 기본 인증서
    # 번들에는 그 루트 CA 가 없으므로, 윈도우 인증서 저장소를 쓰도록 바꾼다.
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent

load_dotenv(ROOT / ".env")

OUTPUT_DIR = ROOT / "output"
STORAGE_DIR = ROOT / "storage"
SESSION_FILE = STORAGE_DIR / "naver_session.json"

for _d in (OUTPUT_DIR, STORAGE_DIR):
    _d.mkdir(exist_ok=True)


class ConfigError(RuntimeError):
    """.env 설정이 잘못되었을 때 발생."""


def _get(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


def _get_bool(key: str, default: bool = False) -> bool:
    raw = _get(key)
    return raw.lower() in ("1", "true", "yes", "y", "on") if raw else default


def _get_int(key: str, default: int) -> int:
    try:
        return int(_get(key) or default)
    except ValueError:
        return default


@dataclass
class AIConfig:
    """Bayer myGenAssist API 접속 정보."""

    base_url: str
    api_key: str
    auth_header: str
    endpoint: str
    chat_model: str
    use_websearch: bool
    supports_json_mode: bool
    verify_ssl: bool
    proxies: dict[str, str] = field(default_factory=dict)

    @property
    def url(self) -> str:
        path = "/chat/agent" if self.endpoint == "agent" else "/responses"
        return f"{self.base_url.rstrip('/')}{path}"

    def headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", self.auth_header: self.api_key}

    def validate(self) -> None:
        if not self.base_url:
            raise ConfigError("AI_BASE_URL 이 비어 있습니다. .env 를 확인하세요.")
        if not self.api_key:
            raise ConfigError("AI_API_KEY 가 비어 있습니다. myGenAssist 에서 API 키를 발급받아 .env 에 넣으세요.")
        if self.endpoint not in ("agent", "responses"):
            raise ConfigError("AI_ENDPOINT 는 agent 또는 responses 여야 합니다.")


@dataclass
class ImageConfig:
    provider: str
    pexels_key: str
    unsplash_key: str
    count: int
    verify_ssl: bool
    proxies: dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        if self.provider == "pexels" and not self.pexels_key:
            raise ConfigError("PEXELS_API_KEY 가 비어 있습니다. https://www.pexels.com/api/ 에서 무료 발급받으세요.")
        if self.provider == "unsplash" and not self.unsplash_key:
            raise ConfigError("UNSPLASH_ACCESS_KEY 가 비어 있습니다. https://unsplash.com/developers 에서 발급받으세요.")


@dataclass
class ImageGenConfig:
    """AI 이미지 생성. Bayer API 에는 생성 기능이 없어 외부 프로바이더를 꽂는다."""

    base_url: str
    api_key: str
    auth_header: str
    auth_prefix: str
    model: str
    size: str
    verify_ssl: bool
    proxies: dict[str, str] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.api_key)


@dataclass
class QualityConfig:
    pass_mark: int
    max_attempts: int
    humanize: bool


@dataclass
class NaverConfig:
    user_id: str
    password: str
    blog_id: str
    category: str
    post_mode: str
    headed: bool

    def validate(self) -> None:
        missing = [
            name
            for name, value in (
                ("NAVER_ID", self.user_id),
                ("NAVER_PW", self.password),
                ("NAVER_BLOG_ID", self.blog_id),
            )
            if not value
        ]
        if missing:
            raise ConfigError(f"{', '.join(missing)} 값이 비어 있습니다. .env 를 확인하세요.")
        if self.post_mode not in ("draft", "publish"):
            raise ConfigError("POST_MODE 는 draft 또는 publish 여야 합니다.")


@dataclass
class PostConfig:
    disclosure: str
    persona: str


def _proxies() -> dict[str, str]:
    out = {}
    if _get("HTTP_PROXY"):
        out["http"] = _get("HTTP_PROXY")
    if _get("HTTPS_PROXY"):
        out["https"] = _get("HTTPS_PROXY")
    return out


def load_ai_config() -> AIConfig:
    return AIConfig(
        base_url=_get("AI_BASE_URL", "https://chat.int.bayer.com/api/v3"),
        api_key=_get("AI_API_KEY"),
        auth_header=_get("AI_AUTH_HEADER", "x-baychatgpt-accesstoken"),
        endpoint=_get("AI_ENDPOINT", "agent").lower(),
        chat_model=_get("AI_CHAT_MODEL", "gpt-4o"),
        use_websearch=_get_bool("AI_USE_WEBSEARCH", True),
        supports_json_mode=_get_bool("AI_SUPPORTS_JSON_MODE", True),
        verify_ssl=_get_bool("AI_VERIFY_SSL", True),
        proxies=_proxies(),
    )


def load_image_config() -> ImageConfig:
    return ImageConfig(
        provider=_get("IMAGE_PROVIDER", "pexels").lower(),
        pexels_key=_get("PEXELS_API_KEY"),
        unsplash_key=_get("UNSPLASH_ACCESS_KEY"),
        count=_get_int("IMAGE_COUNT", 4),
        verify_ssl=_get_bool("AI_VERIFY_SSL", True),
        proxies=_proxies(),
    )


def load_imagegen_config() -> ImageGenConfig:
    return ImageGenConfig(
        base_url=_get("IMAGE_GEN_BASE_URL"),
        api_key=_get("IMAGE_GEN_API_KEY"),
        auth_header=_get("IMAGE_GEN_AUTH_HEADER", "Authorization"),
        auth_prefix=_get("IMAGE_GEN_AUTH_PREFIX", "Bearer"),
        model=_get("IMAGE_GEN_MODEL", "gpt-image-1"),
        size=_get("IMAGE_GEN_SIZE", "1024x1024"),
        verify_ssl=_get_bool("AI_VERIFY_SSL", True),
        proxies=_proxies(),
    )


def load_quality_config() -> QualityConfig:
    return QualityConfig(
        pass_mark=_get_int("QUALITY_PASS_MARK", 80),
        max_attempts=_get_int("QUALITY_MAX_ATTEMPTS", 3),
        humanize=_get_bool("HUMANIZE", True),
    )


def load_naver_config() -> NaverConfig:
    return NaverConfig(
        user_id=_get("NAVER_ID"),
        password=_get("NAVER_PW"),
        blog_id=_get("NAVER_BLOG_ID"),
        category=_get("NAVER_CATEGORY"),
        post_mode=_get("POST_MODE", "draft").lower(),
        headed=_get_bool("BROWSER_HEADED", True),
    )


def load_post_config() -> PostConfig:
    return PostConfig(
        disclosure=_get(
            "DISCLOSURE_TEXT",
            "이 글에는 쇼핑 커넥트 상품이 포함되어 있으며, 상품 판매 시 크리에이터는 수수료를 받습니다.",
        ),
        persona=_get("BLOG_PERSONA", "친근하고 솔직한 리뷰어 말투"),
    )
