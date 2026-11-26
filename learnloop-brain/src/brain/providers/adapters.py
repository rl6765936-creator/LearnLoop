"""The three provider adapters: Anthropic (Claude), OpenAI, Google Gemini.

Each one is a pure translator (no network, no key): `build_request` produces the provider's
JSON, `parse_response` reads the provider's answer or raises a ProviderError. Wire formats
were checked against the providers' public documentation on 24.11.2026 and are listed in
capabilities.py; they MUST be re-checked when real calls are approved (APIs change).
"""

from __future__ import annotations

import json
import re

from brain.providers import schema_tools
from brain.providers.base import (ContentRefused, HttpRequest, HttpResponse, LLMRequest, LLMResponse,
                                  MalformedOutput, TruncatedOutput, Usage, error_for_status, ServerError)


def _retry_after(resp: HttpResponse) -> float | None:
    value = {k.lower(): v for k, v in resp.headers.items()}.get("retry-after")
    try:
        return min(float(value), 60.0) if value is not None else None
    except ValueError:
        return None


def _load_json(text: str | None) -> dict:
    if not text or not text.strip():
        raise MalformedOutput("empty answer")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise MalformedOutput("answer is not valid JSON") from None
    if not isinstance(data, dict):
        raise MalformedOutput("answer is not a JSON object")
    return data


_HEBREW_RUN = re.compile(r"[\u0590-\u05FF][\u0590-\u05FF\s\"',.:-]*")


def provider_hint(body) -> str | None:
    """The provider's own short explanation of a REJECTED request (HTTP 4xx), e.g. which schema
    keyword it does not accept. Used ONLY in the synthetic evaluation report, never logged by the
    application. Defense in depth: cut to 200 characters and any Hebrew text removed, since our
    content is Hebrew and error explanations are not."""
    msg = None
    if isinstance(body, dict):
        err = body.get("error")
        msg = err.get("message") if isinstance(err, dict) else err if isinstance(err, str) else None
    if not isinstance(msg, str) or not msg.strip():
        return None
    msg = _HEBREW_RUN.sub("…", " ".join(msg.split()))
    return msg[:200]


def _check_http(resp: HttpResponse) -> None:
    if resp.status != 200:
        from brain.providers.base import CREDIT_CODES, CreditExhausted
        err_body = resp.body.get("error") if isinstance(resp.body, dict) else None
        code = err_body.get("code") if isinstance(err_body, dict) else None
        if resp.status == 402 or code in CREDIT_CODES:
            raise CreditExhausted(f"provider credit exhausted ({code or resp.status})", resp.status)
        err = error_for_status(resp.status, _retry_after(resp))
        if 400 <= resp.status < 500 and resp.status not in (401, 403):
            err.hint = provider_hint(resp.body)
        raise err
    if not isinstance(resp.body, dict):
        raise MalformedOutput("response body is not a JSON object")


# =========================================================================== Anthropic
class AnthropicAdapter:
    """Messages API. Structured output: output_config.format = {type: json_schema, schema}.
    The Messages API is stateless – nothing is kept between calls on the provider side."""

    provider = "anthropic"
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, model: str, api_version: str = "2023-06-01"):
        self.model, self.api_version = model, api_version

    def build_request(self, request: LLMRequest) -> HttpRequest:
        body = {
            "model": self.model,
            "max_tokens": request.max_output_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": request.user_text}],
            "output_config": {"format": {"type": "json_schema",
                                         "schema": schema_tools.for_provider(request.schema, self.provider)}},
        }
        if request.temperature is not None:
            body["temperature"] = request.temperature
        return HttpRequest("POST", self.URL,
                           {"content-type": "application/json", "anthropic-version": self.api_version},
                           body, auth_header="x-api-key")

    def parse_response(self, resp: HttpResponse) -> LLMResponse:
        if resp.status == 529:                       # Anthropic "overloaded" – worth a retry
            raise ServerError("overloaded", 529, _retry_after(resp))
        _check_http(resp)
        body = resp.body
        stop = body.get("stop_reason") or ""
        if stop == "refusal":
            raise ContentRefused("model refused")
        if stop == "max_tokens":
            raise TruncatedOutput("output token limit reached")
        texts = [b.get("text", "") for b in body.get("content") or [] if b.get("type") == "text"]
        data = _load_json("".join(texts))
        u = body.get("usage") or {}
        return LLMResponse(data, self.provider, body.get("model", self.model),
                           Usage(int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0))), stop)


# =========================================================================== OpenAI
class OpenAIAdapter:
    """Responses API with strict json_schema. IMPORTANT: the Responses API is STATEFUL by
    default (keeps the response as application state) – we always send store=false and never
    use previous_response_id, so every call is self-contained."""

    provider = "openai"
    URL = "https://api.openai.com/v1/responses"

    def __init__(self, model: str):
        self.model = model

    def build_request(self, request: LLMRequest) -> HttpRequest:
        body = {
            "model": self.model,
            "instructions": request.system,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": request.user_text}]}],
            "store": False,
            "max_output_tokens": request.max_output_tokens,
            "text": {"format": {"type": "json_schema", "name": request.schema_name, "strict": True,
                                "schema": schema_tools.for_provider(request.schema, self.provider)}},
        }
        if request.temperature is not None:
            body["temperature"] = request.temperature
        return HttpRequest("POST", self.URL, {"content-type": "application/json"}, body,
                           auth_header="authorization", auth_prefix="Bearer ")

    def parse_response(self, resp: HttpResponse) -> LLMResponse:
        _check_http(resp)
        body = resp.body
        status = body.get("status") or ""
        if status == "incomplete":
            reason = (body.get("incomplete_details") or {}).get("reason", "")
            if reason == "content_filter":
                raise ContentRefused("content filter")
            raise TruncatedOutput(reason or "incomplete")
        if status == "failed":
            raise ServerError("response failed")
        texts = []
        for item in body.get("output") or []:
            if item.get("type") != "message":
                continue                          # e.g. reasoning items
            for part in item.get("content") or []:
                if part.get("type") == "refusal":
                    raise ContentRefused("model refused")
                if part.get("type") == "output_text":
                    texts.append(part.get("text", ""))
        data = _load_json("".join(texts))
        u = body.get("usage") or {}
        return LLMResponse(data, self.provider, body.get("model", self.model),
                           Usage(int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0))), status)


# =========================================================================== Gemini
class GeminiAdapter:
    """generateContent with responseMimeType=application/json + responseJsonSchema.
    Stateless. PAID TIER ONLY: on the free tier Google may use the data to improve products."""

    provider = "gemini"
    BASE = "https://generativelanguage.googleapis.com/v1beta/models/"
    BLOCKED = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"}

    def __init__(self, model: str, thinking_level: str | None = None):
        self.model, self.thinking_level = model, thinking_level

    def build_request(self, request: LLMRequest) -> HttpRequest:
        gen = {"responseMimeType": "application/json",
               "responseJsonSchema": schema_tools.for_provider(request.schema, self.provider),
               "maxOutputTokens": request.max_output_tokens}
        if request.temperature is not None:
            gen["temperature"] = request.temperature
        if self.thinking_level:
            # Gemini 3.x: thinking tokens count against maxOutputTokens – keep it small for JSON.
            gen["thinkingConfig"] = {"thinkingLevel": self.thinking_level}
        body = {"systemInstruction": {"parts": [{"text": request.system}]},
                "contents": [{"role": "user", "parts": [{"text": request.user_text}]}],
                "generationConfig": gen}
        return HttpRequest("POST", f"{self.BASE}{self.model}:generateContent",
                           {"content-type": "application/json"}, body, auth_header="x-goog-api-key")

    def parse_response(self, resp: HttpResponse) -> LLMResponse:
        _check_http(resp)
        body = resp.body
        if (body.get("promptFeedback") or {}).get("blockReason"):
            raise ContentRefused("prompt blocked")
        candidates = body.get("candidates") or []
        if not candidates:
            raise ContentRefused("no candidates")
        cand = candidates[0]
        finish = cand.get("finishReason") or ""
        if finish in self.BLOCKED:
            raise ContentRefused(finish.lower())
        if finish == "MAX_TOKENS":
            raise TruncatedOutput("output token limit reached")
        parts = (cand.get("content") or {}).get("parts") or []
        data = _load_json("".join(p.get("text", "") for p in parts if not p.get("thought")))
        u = body.get("usageMetadata") or {}
        return LLMResponse(data, self.provider, body.get("modelVersion", self.model),
                           Usage(int(u.get("promptTokenCount", 0)), int(u.get("candidatesTokenCount", 0))),
                           finish)


ADAPTERS = {"anthropic": AnthropicAdapter, "openai": OpenAIAdapter, "gemini": GeminiAdapter}
