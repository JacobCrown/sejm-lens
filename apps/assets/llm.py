"""Cienki klient dowolnego API zgodnego z OpenAI (`POST {base}/chat/completions`).

Configured only through .env (LLM_PROVIDER, LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, ...).
No provider SDK: the server only has to speak the OpenAI protocol (OpenAI, Azure OpenAI
with the right LLM_BASE_URL, OpenRouter, Groq, Together, Mistral, DeepSeek, Ollama, LM Studio, vLLM...).
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

PROVIDER_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
    "together": "https://api.together.xyz/v1",
    "mistral": "https://api.mistral.ai/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "ollama": "http://localhost:11434/v1",
    "lmstudio": "http://localhost:1234/v1",
    "vllm": "http://localhost:8000/v1",
}
LOCAL_PROVIDERS = {"ollama", "lmstudio", "vllm"}


class LLMError(Exception):
    pass


class LLMNotConfigured(LLMError):
    pass


def resolve_base_url(provider: str, base_url: str) -> str:
    url = (base_url or "").strip()
    if not url:
        url = PROVIDER_BASE_URLS.get((provider or "").strip().lower(), "")
    if not url:
        known = ", ".join(sorted(PROVIDER_BASE_URLS))
        raise LLMNotConfigured(f"Ustaw LLM_BASE_URL albo LLM_PROVIDER (znane: {known}).")
    url = url.rstrip("/")
    return url if url.endswith("/chat/completions") else f"{url}/chat/completions"


def extract_json(text: str) -> dict:
    """Extracts a JSON object from a reply, even if the model added <think>, ``` or a comment."""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I).strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.S | re.I)
    if fenced:
        cleaned = fenced.group(1).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("response does not contain a JSON object")
    return json.loads(cleaned[start : end + 1])


@dataclass
class LLMResponse:
    text: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: Decimal | None = None
    duration_ms: int = 0
    dropped_params: list[str] = field(default_factory=list)


class LLMClient:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 180,
        temperature: float | None = 0,
        max_tokens: int | None = None,
        response_format: str = "json_object",
        extra_headers: dict | None = None,
        extra_body: dict | None = None,
        max_retries: int = 3,
        transport: httpx.BaseTransport | None = None,
    ):
        if not model:
            raise LLMNotConfigured("Ustaw LLM_MODEL w .env.")
        if response_format not in ("json_object", "json_schema", "none"):
            raise LLMNotConfigured("LLM_RESPONSE_FORMAT must be json_object, json_schema or none.")
        self.url = base_url
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.response_format = response_format
        self.extra_body = dict(extra_body or {})
        self.max_retries = max_retries
        headers = {"Content-Type": "application/json", **(extra_headers or {})}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._http = httpx.Client(timeout=timeout, headers=headers, transport=transport)

    @classmethod
    def from_settings(cls, transport: httpx.BaseTransport | None = None) -> "LLMClient":
        provider = settings.LLM_PROVIDER.strip().lower()
        api_key = settings.LLM_API_KEY
        if not api_key and provider not in LOCAL_PROVIDERS and not settings.LLM_BASE_URL:
            raise LLMNotConfigured("Set LLM_API_KEY in .env (or use a local provider: ollama, lmstudio, vllm).")
        temp = settings.LLM_TEMPERATURE.strip()
        max_tokens = settings.LLM_MAX_TOKENS.strip()
        return cls(
            base_url=resolve_base_url(provider, settings.LLM_BASE_URL),
            model=settings.LLM_MODEL,
            api_key=api_key,
            timeout=settings.LLM_TIMEOUT,
            temperature=float(temp) if temp else None,
            max_tokens=int(max_tokens) if max_tokens else None,
            response_format=settings.LLM_RESPONSE_FORMAT,
            extra_headers=settings.LLM_EXTRA_HEADERS,
            extra_body=settings.LLM_EXTRA_BODY,
            transport=transport,
        )

    def close(self):
        self._http.close()

    def _body(self, messages, schema, drop: set[str]) -> dict:
        body: dict = {"model": self.model, "messages": messages, **self.extra_body}
        if self.temperature is not None and "temperature" not in drop:
            body["temperature"] = self.temperature
        if self.max_tokens and "max_tokens" not in drop:
            body["max_tokens"] = self.max_tokens
        if "response_format" not in drop:
            if self.response_format == "json_object":
                body["response_format"] = {"type": "json_object"}
            elif self.response_format == "json_schema" and schema:
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "declaration", "schema": schema, "strict": False},
                }
        # OpenRouter reports the call cost in USD only when asked.
        if "openrouter.ai" in self.url and "usage" not in drop and "usage" not in body:
            body["usage"] = {"include": True}
        return body

    def chat(self, messages: list[dict], schema: dict | None = None) -> LLMResponse:
        """Sends the conversation. If the provider rejects an unsupported parameter, retries without it."""
        started = time.monotonic()
        drop: set[str] = set()
        last_error = ""
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._http.post(self.url, json=self._body(messages, schema, drop))
            except httpx.TransportError as exc:
                last_error = f"connection error: {exc}"
            else:
                if resp.status_code == 200:
                    return self._parse(resp, started, drop)
                last_error = f"HTTP {resp.status_code}: {resp.text[:500]}"
                if resp.status_code == 400:
                    param = self._unsupported_param(resp.text, drop)
                    if param:
                        logger.warning("LLM provider rejected parameter %r, retrying without it", param)
                        drop.add(param)
                        continue
                    raise LLMError(f"LLM provider rejected the request ({last_error})")
                if resp.status_code in (401, 403, 404):
                    raise LLMError(f"LLM provider refused access or does not know the model ({last_error})")
                if resp.status_code not in (408, 409, 429) and resp.status_code < 500:
                    raise LLMError(f"Unexpected LLM provider response ({last_error})")
            if attempt < self.max_retries:
                wait = 2**attempt
                logger.warning("LLM: %s. Retrying in %ss (%s/%s)", last_error, wait, attempt + 1, self.max_retries)
                time.sleep(wait)
        raise LLMError(f"LLM provider is not responding correctly: {last_error}")

    @staticmethod
    def _unsupported_param(body: str, already: set[str]) -> str | None:
        low = body.lower()
        for param in ("response_format", "temperature", "max_tokens", "usage"):
            if param in low and param not in already:
                return param
        if "json_schema" in low and "response_format" not in already:
            return "response_format"
        return None

    def _parse(self, resp: httpx.Response, started: float, dropped: set[str]) -> LLMResponse:
        try:
            data = resp.json()
            message = data["choices"][0]["message"]
            text = message.get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Malformed LLM provider response: {resp.text[:300]}") from exc
        if not text.strip():
            raise LLMError("The model returned an empty response")
        usage = data.get("usage") or {}
        raw_cost = usage.get("cost")
        return LLMResponse(
            text=text,
            model=data.get("model") or self.model,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            cost_usd=Decimal(str(raw_cost)) if raw_cost is not None else None,
            duration_ms=int((time.monotonic() - started) * 1000),
            dropped_params=sorted(dropped),
        )
