import logging
import os
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60


class LLMConfigError(Exception):
    """Raised when required LLM environment variables are missing."""


@dataclass
class LLMResponse:
    content: str
    raw_response: dict


class LLMClient:
    """Minimal client for any OpenAI-compatible chat completions API.

    Works with OpenAI, Grok (x.ai), or any other provider exposing the same
    `/chat/completions` shape — just point LLM_BASE_URL at it.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.api_key = (api_key or os.getenv("LLM_API_KEY", "")).strip()
        self.base_url = (base_url or os.getenv("LLM_BASE_URL", "")).strip().rstrip("/")
        self.model = (model or os.getenv("LLM_MODEL", "")).strip()
        self.provider = (provider or os.getenv("LLM_PROVIDER", "openai-compatible")).strip()
        self.timeout = timeout

        missing = [
            name
            for name, value in (
                ("LLM_API_KEY", self.api_key),
                ("LLM_BASE_URL", self.base_url),
                ("LLM_MODEL", self.model),
            )
            if not value
        ]
        if missing:
            raise LLMConfigError(
                f"Missing required LLM environment variable(s): {', '.join(missing)}"
            )

    def chat_completion(self, system_prompt: str, user_message: str) -> LLMResponse:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        response = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        return LLMResponse(content=content, raw_response=data)
