"""AI 응답을 JSON 으로 받고, 깨졌거나 필수 필드가 빠지면 한 번 고쳐 달라고 다시 묻는다."""

from __future__ import annotations

from pathlib import Path

from ai.client import AIError, _parse_json


class StageError(RuntimeError):
    """단계 하나가 끝내 실패했다. 앞 단계 저장본은 남아 있어 그 단계부터 다시 돌릴 수 있다."""

    def __init__(self, stage: str, message: str):
        super().__init__(f"[{stage} 단계] {message}")
        self.stage = stage


def ask_json(
    client,
    stage: str,
    system: str,
    user: str,
    *,
    required: tuple[str, ...] = (),
    temperature: float = 0.5,
    max_tokens: int = 6000,
    images: list[Path] | None = None,
) -> dict:
    kwargs: dict = {"temperature": temperature, "max_tokens": max_tokens, "websearch": False}
    if images:
        kwargs["images"] = images
    json_mode = getattr(getattr(client, "cfg", None), "supports_json_mode", True)

    raw = _call(client, stage, system, user, json_mode, kwargs)
    data, problem = _decode(raw, required)
    if not problem:
        return data

    retry = (
        f"{user}\n\n[직전 응답의 문제]\n{problem}\n"
        "위 문제를 고쳐, 요구한 형식의 JSON 전체를 처음부터 다시 출력한다."
    )
    raw = _call(client, stage, system, retry, json_mode, kwargs)
    data, problem = _decode(raw, required)
    if problem:
        raise StageError(stage, f"AI 응답을 쓸 수 없습니다. {problem}")
    return data


def _call(client, stage: str, system: str, user: str, json_mode: bool, kwargs: dict) -> str:
    try:
        return client.chat(system, user, json_mode=json_mode, **kwargs)
    except AIError as exc:
        raise StageError(stage, f"AI 호출 실패: {exc}") from exc


def _decode(raw: str, required: tuple[str, ...]) -> tuple[dict, str]:
    try:
        data = _parse_json(raw)
    except AIError:
        return {}, "유효한 JSON 이 아니었다."
    if not isinstance(data, dict):
        return {}, "JSON 객체({...})가 아니었다."
    missing = [key for key in required if _is_empty(data.get(key))]
    if missing:
        return data, f"다음 필드가 비었거나 빠졌다: {', '.join(missing)}"
    return data, ""


def _is_empty(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return not value
    return False
