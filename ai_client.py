"""Bayer myGenAssist API 클라이언트.

docs/v3-api.yaml 기준:
  - POST /chat/agent : OpenAI chat/completions 호환. agent.tool_keys 로 websearch 사용 가능.
  - POST /responses  : OpenAI Responses API 호환.
  - 인증 헤더        : x-baychatgpt-accesstoken
"""

from __future__ import annotations

import json
import re
import time

import requests

from config import AIConfig


class AIError(RuntimeError):
    pass


class MyGenAssistClient:
    def __init__(self, cfg: AIConfig):
        cfg.validate()
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update(cfg.headers())
        if cfg.proxies:
            self.session.proxies.update(cfg.proxies)
        self.session.verify = cfg.verify_ssl

    def ping(self) -> dict:
        """토큰이 유효한지 /users/me 로 확인한다."""
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
        retries: int = 2,
    ) -> str:
        payload = (
            self._agent_payload(system, user, temperature, max_tokens, json_mode, websearch)
            if self.cfg.endpoint == "agent"
            else self._responses_payload(system, user, temperature, max_tokens, json_mode)
        )

        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                resp = self.session.post(self.cfg.url, json=payload, timeout=300)
                if resp.status_code >= 400:
                    raise AIError(f"API 오류 {resp.status_code}: {resp.text[:800]}")
                return self._extract_text(resp.json())
            except (requests.RequestException, AIError) as exc:
                last_error = exc
                if attempt < retries:
                    time.sleep(2 * (attempt + 1))
        raise AIError(f"AI 호출에 실패했습니다: {last_error}")

    def chat_json(self, system: str, user: str, **kwargs) -> dict | list:
        raw = self.chat(system, user, json_mode=self.cfg.supports_json_mode, **kwargs)
        return _parse_json(raw)

    def _agent_payload(self, system, user, temperature, max_tokens, json_mode, websearch) -> dict:
        payload: dict = {
            "model": self.cfg.chat_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
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

    @staticmethod
    def _extract_text(data: dict) -> str:
        """chat/completions 와 Responses 두 응답 형식을 모두 처리한다."""
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
