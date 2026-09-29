"""myGenAssist 및 Google Gemini API 클라이언트.

docs/v3-api.yaml 기준:
  - POST /chat/agent : OpenAI chat/completions 호환. agent.tool_keys 로 websearch 사용 가능.
  - POST /responses  : OpenAI Responses API 호환.
  - 인증 헤더        : x-baychatgpt-accesstoken
"""

from __future__ import annotations

import base64
import io
import json
import re
import time
from pathlib import Path

import requests

from config import AIConfig

#: 비전 입력으로 보낼 때 줄이는 가로 폭. 원본 그대로 보내면 토큰과 시간이 크게 늘고
#: 상세페이지 글자를 읽는 데는 이 정도면 충분하다.
VISION_MAX_WIDTH = 900


class AIError(RuntimeError):
    pass


class MyGenAssistClient:
    """공통 chat/chat_json 인터페이스를 제공한다 (이름은 기존 호출부 호환용)."""

    def __init__(self, cfg: AIConfig):
        cfg.validate()
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update(cfg.headers())
        if cfg.proxies:
            self.session.proxies.update(cfg.proxies)
        self.session.verify = cfg.verify_ssl

    def ping(self) -> dict:
        """선택한 제공자의 인증 및 API 접근 가능 여부를 확인한다."""
        if self.cfg.provider == "gemini":
            resp = self.session.get(f"{self.cfg.base_url.rstrip('/')}/models", timeout=30)
            if resp.status_code >= 400:
                raise AIError(f"Gemini API 오류 {resp.status_code}: {resp.text[:800]}")
            return resp.json()

        url = f"{self.cfg.base_url.rstrip('/')}/users/me"
        resp = self.session.get(url, timeout=30)
        if resp.status_code == 401:
            raise AIError("인증 실패(401). AI_API_KEY 를 확인하세요.")
        if resp.status_code == 403:
            raise AIError("권한 없음(403). 계정이 비활성 상태이거나 이용약관 동의가 필요할 수 있습니다.")
        resp.raise_for_status()
        return resp.json()

    def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.8,
        max_tokens: int = 8000,
        json_mode: bool = False,
        websearch: bool | None = None,
        images: list[Path] | None = None,
        retries: int = 2,
    ) -> str:
        if self.cfg.provider == "gemini":
            payload = self._gemini_payload(
                system, user, temperature, max_tokens, json_mode, websearch, images
            )
        else:
            payload = (
                self._agent_payload(system, user, temperature, max_tokens, json_mode, websearch, images)
                if self.cfg.endpoint == "agent"
                else self._responses_payload(system, user, temperature, max_tokens, json_mode)
            )

        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                resp = self.session.post(self.cfg.url, json=payload, timeout=300)
                if resp.status_code >= 400:
                    provider = "Gemini" if self.cfg.provider == "gemini" else "API"
                    raise AIError(f"{provider} 오류 {resp.status_code}: {resp.text[:800]}")
                return self._extract_text(resp.json())
            except (requests.RequestException, AIError) as exc:
                last_error = exc
                if attempt < retries:
                    time.sleep(2 * (attempt + 1))
        raise AIError(f"AI 호출에 실패했습니다: {last_error}")

    def chat_json(self, system: str, user: str, **kwargs) -> dict | list:  # noqa: D401
        raw = self.chat(system, user, json_mode=self.cfg.supports_json_mode, **kwargs)
        return _parse_json(raw)

    def _agent_payload(self, system, user, temperature, max_tokens, json_mode, websearch, images=None) -> dict:
        content: str | list = user
        if images:
            content = [{"type": "text", "text": user}]
            content += [
                {"type": "image_url", "image_url": {"url": data_uri, "detail": "high"}}
                for data_uri in (encode_image(p) for p in images)
                if data_uri
            ]

        payload: dict = {
            "model": self.cfg.chat_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        use_search = self.cfg.use_websearch if websearch is None else websearch
        if use_search:
            payload["agent"] = {"type": "react", "tool_keys": ["websearch"]}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _responses_payload(self, system, user, temperature, max_tokens, json_mode) -> dict:
        payload: dict = {
            "model": self.cfg.chat_model,
            "instructions": system,
            "input": user,
            "temperature": temperature,
            "max_output_tokens": max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["text"] = {"format": {"type": "json_object"}}
        return payload

    def _gemini_payload(self, system, user, temperature, max_tokens, json_mode, websearch, images=None) -> dict:
        parts: list[dict] = [{"text": user}]
        for path in images or []:
            data_uri = encode_image(path)
            if data_uri:
                mime_type, encoded = data_uri.split(",", 1)
                parts.append({"inlineData": {"mimeType": mime_type[5:].split(";", 1)[0], "data": encoded}})

        use_search = self.cfg.use_websearch if websearch is None else websearch
        if json_mode and use_search:
            # 실제 Gemini generateContent 시험에서 Google Search와
            # responseMimeType=application/json을 함께 보내면 candidates 없이
            # 응답하는 경우가 확인되어, 프롬프트 지시로 JSON 출력을 요청한다.
            system += "\n\n응답은 유효한 JSON만 출력하고, 마크다운 코드펜스나 설명을 덧붙이지 마세요."
        payload: dict = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": temperature,
                # Gemini 3의 출력 한도에는 내부 사고 토큰도 포함되므로,
                # 호출부가 요청한 예산의 2배를 주어 최종 답변 공간을 확보한다.
                "maxOutputTokens": max_tokens * 2,
            },
        }
        # Gemini Google Search와 JSON MIME 모드를 동시에 켰을 때 비어 있는
        # candidates 응답이 재현되어, 검색 요청은 프롬프트 지시로 JSON을 요청한다.
        model_version = re.match(r"gemini-(\d+)", self.cfg.chat_model)
        supports_tool_json = bool(model_version and int(model_version.group(1)) >= 3)
        if supports_tool_json:
            # Gemini 3의 기본 thinking은 medium이며, 긴 집필 프롬프트에서는
            # 출력 토큰 예산을 사고에 쓰고 최종 텍스트 없이 끝날 수 있다.
            payload["generationConfig"]["thinkingConfig"] = {"thinkingLevel": "LOW"}
        if json_mode and not use_search:
            payload["generationConfig"]["responseMimeType"] = "application/json"
        if use_search:
            payload["tools"] = [{"googleSearch": {}}]
        return payload

    @staticmethod
    def _extract_text(data: dict) -> str:
        """Gemini, chat/completions, Responses 응답에서 텍스트를 추출한다."""
        if isinstance(data.get("candidates"), list):
            candidates = data["candidates"]
            if candidates:
                candidate = candidates[0]
                content = candidate.get("content") or {}
                text = "".join(
                    part.get("text", "")
                    for part in content.get("parts") or []
                    if isinstance(part, dict) and not part.get("thought")
                )
                if text.strip():
                    return text
                if candidate.get("finishReason") == "MAX_TOKENS":
                    usage = data.get("usageMetadata") or {}
                    thoughts = usage.get("thoughtsTokenCount", 0)
                    raise AIError(
                        "Gemini가 출력 토큰 한도에 도달해 최종 답변을 만들지 못했습니다 "
                        f"(사고 토큰 {thoughts}개). Gemini 3에서는 사고 토큰도 출력 한도에 포함됩니다. "
                        "AI 호출을 다시 시도하거나 max_tokens 를 늘려주세요."
                    )

        feedback = data.get("promptFeedback") or {}
        if feedback.get("blockReason"):
            raise AIError(f"Gemini가 요청을 차단했습니다: {feedback['blockReason']}")

        if "usageMetadata" in data and "candidates" not in data:
            usage = data.get("usageMetadata") or {}
            thoughts = usage.get("thoughtsTokenCount", 0)
            candidate_tokens = usage.get("candidatesTokenCount", 0)
            model = data.get("modelVersion", "unknown model")
            response_id = data.get("responseId", "unavailable")
            raise AIError(
                f"Gemini({model})가 후보 답변 없이 종료했습니다 "
                f"(사고 토큰 {thoughts}개, 답변 토큰 {candidate_tokens}개, 응답 ID {response_id}). "
                "이 응답은 토큰 한도 초과를 단정할 수 없습니다. doctor 명령으로 API 연결을 확인하고, "
                "반복되면 이 응답 ID와 함께 Gemini API 상태를 확인하세요."
            )

        if isinstance(data.get("error"), dict):
            error = data["error"]
            raise AIError(f"Gemini API 오류: {error.get('message') or json.dumps(error)[:800]}")

        if isinstance(data.get("choices"), list) and data["choices"]:
            choice = data["choices"][0]
            message = choice.get("message") or {}
            content = message.get("content")

            # gpt-5 계열 추론 모델은 max_tokens 를 추론에 먼저 쓴다. 한도가 모자라면
            # 본문이 빈 문자열로 오므로, 무슨 일이 일어났는지 알려준다.
            if not content and choice.get("finish_reason") == "length":
                usage = data.get("usage") or {}
                reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
                raise AIError(
                    f"모델 '{data.get('model')}' 이(가) 토큰 한도({usage.get('completion_tokens')})를 "
                    f"추론에만 {reasoning}개 써서 답변이 비었습니다. max_tokens 를 늘리세요."
                )

            if isinstance(content, str) and content.strip():
                return content
            if isinstance(content, list):
                joined = "".join(p.get("text", "") for p in content if isinstance(p, dict))
                if joined.strip():
                    return joined

        if isinstance(data.get("output_text"), str) and data["output_text"].strip():
            return data["output_text"]

        chunks: list[str] = []
        for item in data.get("output") or []:
            for part in item.get("content") or []:
                if part.get("type") in ("output_text", "text") and part.get("text"):
                    chunks.append(part["text"])
        if chunks:
            return "".join(chunks)

        raise AIError(f"응답에서 텍스트를 찾지 못했습니다: {json.dumps(data)[:800]}")


def encode_image(path: Path) -> str:
    """이미지를 data URI 로 만든다. 실패하면 빈 문자열을 돌려 호출부가 건너뛰게 한다."""
    try:
        from PIL import Image

        image = Image.open(path)
        image.load()
        if image.width > VISION_MAX_WIDTH:
            ratio = VISION_MAX_WIDTH / image.width
            image = image.resize((VISION_MAX_WIDTH, int(image.height * ratio)), Image.LANCZOS)

        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, "JPEG", quality=72, optimize=True)
        return f"data:image/jpeg;base64,{base64.b64encode(buffer.getvalue()).decode()}"
    except Exception:
        return ""


def _parse_json(raw: str) -> dict | list:
    """모델이 코드펜스나 설명을 덧붙여도 JSON 을 뽑아낸다."""
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue

    raise AIError(f"JSON 파싱 실패. 모델 응답:\n{raw[:1000]}")
