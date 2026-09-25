import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60

_MAX_RETRIES = 3
_RETRY_BACKOFF_SECONDS = 2.0
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_RETRY_AFTER_SECONDS = 30.0


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
        """POST one chat completion request, retrying on transient failures.

        Retries (bounded, with backoff) on HTTP 429/5xx, timeouts, and
        connection errors (resets, refused connections, etc.) — the same
        four failure modes a batch of concurrent workers sharing one
        provider rate limit is most likely to hit. Anything else (4xx other
        than 429, malformed response body) raises immediately since retrying
        it would just fail the same way again. Safe to call from multiple
        threads at once: no shared mutable state, each call opens its own
        connection.
        """
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

        for attempt in range(_MAX_RETRIES + 1):
            try:
                response = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
                response.raise_for_status()
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                return LLMResponse(content=content, raw_response=data)
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status in _RETRYABLE_STATUS_CODES and attempt < _MAX_RETRIES:
                    wait = self._retry_wait(exc, attempt, status)
                    logger.warning(
                        f"LLM API returned {status} (attempt {attempt + 1}/{_MAX_RETRIES + 1}); "
                        f"retrying in {wait:.1f}s..."
                    )
                    time.sleep(wait)
                    continue
                raise
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                if attempt < _MAX_RETRIES:
                    wait = self._retry_wait(exc, attempt, None)
                    logger.warning(
                        f"LLM API request failed ({exc.__class__.__name__}) "
                        f"(attempt {attempt + 1}/{_MAX_RETRIES + 1}); retrying in {wait:.1f}s..."
                    )
                    time.sleep(wait)
                    continue
                raise

    @staticmethod
    def _retry_wait(exc: Exception, attempt: int, status: Optional[int]) -> float:
        """Honor a 429's Retry-After header when present (capped, so a
        misbehaving provider can't stall a batch indefinitely); otherwise
        exponential backoff."""
        if status == 429:
            response = getattr(exc, "response", None)
            retry_after = response.headers.get("Retry-After") if response is not None else None
            if retry_after is not None:
                try:
                    return min(float(retry_after), _MAX_RETRY_AFTER_SECONDS)
                except ValueError:
                    pass
        return _RETRY_BACKOFF_SECONDS * (2 ** attempt)
