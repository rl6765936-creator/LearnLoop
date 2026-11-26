"""Offline provider simulator: answers HttpRequests in each provider's OWN response format.

Purpose: run the whole product and the evaluation through the real adapters (request
building, response parsing, schema transforms, retries, contract validation) WITHOUT any
network. The answers come from the deterministic FakeModel logic. Everything stays in memory.

The wrap_* functions are also used by tests to build provider-shaped responses.
"""

from __future__ import annotations

import json
from typing import Callable

from brain.model_gateway import fake_activity, fake_grade
from brain.providers import prompts
from brain.providers.base import HttpRequest, HttpResponse


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)          # rough, for latency/cost plumbing only


def wrap_anthropic(data: dict | str, *, stop_reason: str = "end_turn", model: str = "sim", in_tokens: int = 10) -> HttpResponse:
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return HttpResponse(200, {"id": "msg_sim", "type": "message", "role": "assistant", "model": model,
                              "content": [{"type": "text", "text": text}], "stop_reason": stop_reason,
                              "usage": {"input_tokens": in_tokens, "output_tokens": _tokens(text)}})


def wrap_openai(data: dict | str, *, status: str = "completed", incomplete_reason: str | None = None,
                refusal: str | None = None, model: str = "sim", in_tokens: int = 10) -> HttpResponse:
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    content = ([{"type": "refusal", "refusal": refusal}] if refusal is not None
               else [{"type": "output_text", "text": text, "annotations": []}])
    body = {"id": "resp_sim", "object": "response", "status": status, "model": model,
            "output": [{"type": "reasoning", "summary": []},
                       {"type": "message", "role": "assistant", "content": content}],
            "usage": {"input_tokens": in_tokens, "output_tokens": _tokens(text)}}
    if incomplete_reason:
        body["incomplete_details"] = {"reason": incomplete_reason}
    return HttpResponse(200, body)


def wrap_gemini(data: dict | str, *, finish: str = "STOP", model: str = "sim", in_tokens: int = 10,
                block_reason: str | None = None) -> HttpResponse:
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    body = {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": finish}],
            "usageMetadata": {"promptTokenCount": in_tokens, "candidatesTokenCount": _tokens(text)},
            "modelVersion": model}
    if block_reason:
        body = {"promptFeedback": {"blockReason": block_reason}}
    return HttpResponse(200, body)


WRAP = {"anthropic": wrap_anthropic, "openai": wrap_openai, "gemini": wrap_gemini}


def request_user_text(provider: str, req: HttpRequest) -> str:
    b = req.body
    if provider == "anthropic":
        return b["messages"][0]["content"]
    if provider == "openai":
        return b["input"][0]["content"][0]["text"]
    return b["contents"][0]["parts"][0]["text"]


def handler_for(provider: str, analysis_script: Callable[[list, list], dict] | dict | None = None,
                answer: Callable[[str, dict], dict] | None = None) -> Callable[[HttpRequest], HttpResponse]:
    """A MockTransport handler that plays `provider`. `answer(task, payload)` overrides the
    default FakeModel-based answers (used by the evaluation's mock mode)."""

    def default(task: str, payload: dict) -> dict:
        if task == "analyze":
            script = analysis_script or {"subject": {"name": "לא ידוע", "confidence": 0.3},
                                         "topics": [], "evidence": []}
            return script(payload["chunks"], payload["known_subjects"]) if callable(script) else script
        if task == "generate_activity":
            return fake_activity(payload["spec"])
        return fake_grade(payload["item"], payload["answer"], "he")

    respond = answer or default

    def handle(req: HttpRequest) -> HttpResponse:
        user_text = request_user_text(provider, req)
        payload = prompts.read_data_section(user_text)
        return WRAP[provider](respond(payload["task"], payload), in_tokens=_tokens(user_text))

    return handle
