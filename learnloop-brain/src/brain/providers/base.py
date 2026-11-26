"""Provider-neutral types: the request the brain wants answered, the answer, and errors.

What this is: the "unified model adapter interface". Business logic (ingestion, activities,
grading) never sees a provider's wire format. It calls `ModelGateway` (model_gateway.py);
`ProviderModelGateway` (gateway.py) turns each call into an `LLMRequest`, hands it to one
`ProviderAdapter`, and gets back an `LLMResponse`.

An adapter only translates:
    LLMRequest  --build_request-->  HttpRequest   (provider JSON + headers)
    HttpResponse --parse_response--> LLMResponse  (or a ProviderError)
It never opens a connection itself. Sending is the transport's job (transport.py), and the
default transport refuses to send anything [APPROVED restriction: local development only].

Privacy rule for this whole package: error messages and log records carry KINDS, STATUS
CODES and COUNTS only – never prompt text, study content, model output or keys.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


# --------------------------------------------------------------------------- request / response
@dataclass(frozen=True)
class LLMRequest:
    task: str                       # "analyze" | "generate_activity" | "grade_answer"
    system: str                     # instructions (ours – trusted)
    user_text: str                  # contains the UNTRUSTED data section, already framed
    schema: dict                    # the contract (contracts/*.schema.json) the answer must match
    schema_name: str                # short identifier, e.g. "content_analysis"
    max_output_tokens: int = 2048
    temperature: float | None = None   # None = provider default (some models reject the field)
    language: str = "he"

    def __repr__(self) -> str:      # never print content
        return (f"LLMRequest(task={self.task!r}, schema={self.schema_name!r}, "
                f"chars={len(self.user_text)}, language={self.language!r})")


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class LLMResponse:
    data: dict                      # parsed JSON – NOT yet validated against the contract
    provider: str
    model: str
    usage: Usage = field(default_factory=Usage)
    finish_reason: str = ""
    latency_ms: float = 0.0

    def __repr__(self) -> str:
        return (f"LLMResponse(provider={self.provider!r}, model={self.model!r}, "
                f"finish={self.finish_reason!r}, tokens={self.usage.input_tokens}/"
                f"{self.usage.output_tokens})")


# --------------------------------------------------------------------------- HTTP (wire level)
class Secret:
    """Holds an API key. repr/str never show it, so it cannot leak into logs or tracebacks."""

    __slots__ = ("_value",)

    def __init__(self, value: str):
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret(***)"

    __str__ = __repr__


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: dict[str, str]         # WITHOUT the key; the transport adds it (see auth_header)
    body: dict
    auth_header: str = ""           # header NAME the transport fills with the key
    auth_prefix: str = ""           # e.g. "Bearer "

    def __repr__(self) -> str:
        return f"HttpRequest({self.method} {self.url}, body_keys={sorted(self.body)})"


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: Any                       # parsed JSON (dict) or None
    headers: dict[str, str] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"HttpResponse(status={self.status})"


# --------------------------------------------------------------------------- errors
class ProviderError(Exception):
    """Base. `kind` is a stable code; the message never contains content."""

    kind = "provider_error"
    retryable = False

    def __init__(self, detail: str = "", status: int | None = None, retry_after: float | None = None):
        self.status, self.retry_after = status, retry_after
        text = self.kind + (f" (HTTP {status})" if status else "") + (f": {detail}" if detail else "")
        super().__init__(text)


class NetworkDisabled(ProviderError):
    kind = "network_disabled"        # the default: nothing may leave the computer


class ConfigError(ProviderError):
    kind = "config_error"


class AuthError(ProviderError):
    kind = "auth_error"


class InvalidRequest(ProviderError):
    kind = "invalid_request"


class RateLimited(ProviderError):
    kind = "rate_limited"
    retryable = True


class CreditExhausted(ProviderError):
    """The provider account has no credit left (prepaid credits used up, or billing limit hit).
    Retrying cannot help: AI sends pause, the queue is kept, she decides whether to top up."""
    kind = "credit_exhausted"
    retryable = False


CREDIT_CODES = ("insufficient_quota", "credit_balance_exhausted", "billing_hard_limit_reached",
                "billing_not_active")


class ProviderTimeout(ProviderError):
    kind = "timeout"
    retryable = True


class ServerError(ProviderError):
    kind = "server_error"
    retryable = True


class NetworkUnreachable(ProviderError):
    kind = "network_unreachable"     # DNS / TLS certificate: will not fix itself by retrying


class ContentRefused(ProviderError):
    kind = "content_refused"         # provider refused / safety-blocked


class TruncatedOutput(ProviderError):
    kind = "truncated_output"        # hit the output-token limit – JSON is incomplete


class MalformedOutput(ProviderError):
    kind = "malformed_output"        # not JSON, or no answer where one was expected


def error_for_status(status: int, retry_after: float | None = None, detail: str = "") -> ProviderError:
    """Common HTTP status → error mapping. Adapters refine it with provider specifics."""
    if status in (401, 403):
        return AuthError(detail, status)
    if status == 408:
        return ProviderTimeout(detail, status)
    if status == 429:
        return RateLimited(detail, status, retry_after)
    if status >= 500:
        return ServerError(detail, status, retry_after)
    return InvalidRequest(detail, status)


# --------------------------------------------------------------------------- adapter interface
class ProviderAdapter(Protocol):
    provider: str                    # "anthropic" | "openai" | "gemini"
    model: str

    def build_request(self, request: LLMRequest) -> HttpRequest: ...

    def parse_response(self, response: HttpResponse) -> LLMResponse: ...
