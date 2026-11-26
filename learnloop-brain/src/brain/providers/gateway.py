"""ProviderModelGateway – implements the existing ModelGateway with a real-provider adapter.

The rest of the brain (ingestion, activities, grading) keeps calling
analyze / generate_activity / grade_answer exactly as with FakeModel. Which provider answers
is a configuration choice (config.py); no provider name appears in business logic.

Per call:
  1. build the prompt (prompts.py) – instructions in `system`, untrusted data framed;
  2. local input budget check (nothing is sent if the input is too large);
  3. adapter.build_request → transport.send → adapter.parse_response;
  4. retry ONLY retryable errors (rate limit, timeout, 5xx) with exponential backoff;
  5. remove strict-mode nulls, then validate against the FULL contract (contracts/);
  6. log one metadata-only line and keep metadata-only stats (for evaluation).
Contract failures are not retried here – the ingestion step already re-asks (3 attempts).
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from brain import contracts
from brain.providers import prompts, schema_tools
from brain.providers.base import InvalidRequest, LLMRequest, ProviderAdapter, ProviderError
from brain.providers.transport import Transport, monotonic_ms

log = logging.getLogger("brain.providers")

DEFAULT_OUTPUT_TOKENS = {"analyze": 4096, "generate_activity": 4096, "grade_answer": 1024}
SCHEMA_OF = {"analyze": contracts.ANALYSIS, "generate_activity": contracts.ACTIVITY,
             "grade_answer": contracts.GRADING}


class ProviderModelGateway:
    def __init__(self, adapter: ProviderAdapter, transport: Transport, *, language: str = "he",
                 max_attempts: int = 3, backoff_s: float = 0.5, max_backoff_s: float = 8.0,
                 max_input_chars: int = 120_000, temperature: float | None = None,
                 output_tokens: dict | None = None, min_interval_s: float = 0.0,
                 rate_limit_wait_s: float = 0.0, budget=None, task_adapters: dict | None = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = monotonic_ms):
        self.adapter, self.transport, self.language = adapter, transport, language
        self.name = adapter.provider
        self.max_attempts, self.backoff_s, self.max_backoff_s = max(1, max_attempts), backoff_s, max_backoff_s
        self.max_input_chars, self.temperature = max_input_chars, temperature
        self.output_tokens = {**DEFAULT_OUTPUT_TOKENS, **(output_tokens or {})}
        self.sleep, self.clock = sleep, clock
        # Free tiers allow only a few requests per minute: keep a minimum gap between calls,
        # and wait longer after a rate-limit answer that gives no Retry-After.
        self.min_interval_s, self.rate_limit_wait_s = min_interval_s, rate_limit_wait_s
        self._last_send: float | None = None
        # Monthly budget (decision 2026-11-24): checked BEFORE every request, recorded after.
        self.budget = budget
        # Optional stronger model for specific tasks only (e.g. {"generate_activity": adapter}).
        self.task_adapters = task_adapters or {}
        self.stats: list[dict] = []          # metadata only: task, outcome, latency, tokens

    # ------------------------------------------------------------------ ModelGateway
    def analyze(self, chunks: list[dict], known_subjects: list[dict], file_context: dict | None = None) -> dict:
        return self._call("analyze", prompts.analyze_payload(chunks, known_subjects, file_context), self.language)

    def generate_activity(self, spec: dict) -> dict:
        return self._call("generate_activity", prompts.activity_payload(spec), spec.get("language", self.language))

    def grade_answer(self, item: dict, answer: str, language: str = "he") -> dict:
        return self._call("grade_answer", prompts.grading_payload(item, answer), language)

    # ------------------------------------------------------------------ internals
    def _call(self, task: str, payload: dict, language: str) -> dict:
        schema_name = SCHEMA_OF[task]
        request = LLMRequest(task=task, system=prompts.system_prompt(task, language),
                             user_text=prompts.data_section(payload),
                             schema=contracts.schema(schema_name), schema_name=schema_name,
                             max_output_tokens=self.output_tokens[task], temperature=self.temperature,
                             language=language)
        if len(request.user_text) > self.max_input_chars:
            self._record(task, 0, "input_too_large", 0.0, None)
            raise InvalidRequest("input exceeds the local budget")   # never sent
        adapter = self.task_adapters.get(task, self.adapter)
        http_request = adapter.build_request(request)
        chars = len(request.user_text) + len(request.system)

        for attempt in range(1, self.max_attempts + 1):
            if self.min_interval_s and self._last_send is not None:
                gap = self.min_interval_s - (self.clock() - self._last_send) / 1000.0
                if gap > 0:
                    self.sleep(gap)
            estimate = 0.0
            if self.budget is not None:
                try:
                    estimate = self.budget.check(adapter.model, chars, request.max_output_tokens)
                except ProviderError as e:
                    self._record(task, attempt, e.kind, 0.0, None)
                    raise                                   # nothing was sent
            self._last_send = started = self.clock()
            try:
                response = adapter.parse_response(self.transport.send(http_request))
            except ProviderError as e:
                elapsed = self.clock() - started
                if self.budget is not None and e.status is not None and e.status < 500 and e.status != 429:
                    pass                                    # rejected requests are not billed
                elif self.budget is not None and e.kind in ("truncated_output", "content_refused", "malformed_output"):
                    self.budget.record(adapter.model, 0, 0, estimate)   # an answer came back: count it
                self._record(task, attempt, e.kind, elapsed, None)
                if e.kind == "credit_exhausted":
                    self._credit_out_at = self.clock()     # pause AI sends; the queue is kept
                if not e.retryable or attempt == self.max_attempts:
                    raise
                delay = self._delay(attempt, e.retry_after)
                if e.retry_after is None and (e.kind == "rate_limited" or e.status == 503):
                    # 503 = "overloaded": a short pause rarely helps (real run gemini-free-5)
                    delay = max(delay, self.rate_limit_wait_s)
                self.sleep(delay)
                continue
            response.latency_ms = self.clock() - started
            if self.budget is not None:
                self.budget.record(adapter.model, response.usage.input_tokens, response.usage.output_tokens, estimate)
            data = schema_tools.strip_nulls(response.data, request.schema)
            try:
                contracts.validate(schema_name, data)
            except contracts.ContractError:
                self._record(task, attempt, "contract_rejected", response.latency_ms, response)
                raise
            self._record(task, attempt, "ok", response.latency_ms, response)
            return data
        raise AssertionError("unreachable")

    def _delay(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return min(retry_after, self.max_backoff_s)
        return min(self.backoff_s * (2 ** (attempt - 1)), self.max_backoff_s)

    CREDIT_RECHECK_S = 6 * 3600     # after "no credit": try again at most every 6 hours [ASSUMPTION]

    def budget_exhausted(self) -> bool:
        if getattr(self, "_credit_out_at", None) is not None:
            if self.clock() - self._credit_out_at < self.CREDIT_RECHECK_S:
                return True
            self._credit_out_at = None                     # maybe she topped up: one request may try
        return self.budget is not None and self.budget.exhausted()

    def credit_exhausted(self) -> bool:
        return getattr(self, "_credit_out_at", None) is not None

    def _record(self, task, attempt, outcome, latency_ms, response) -> None:
        entry = {"provider": self.name, "model": self.task_adapters.get(task, self.adapter).model, "task": task, "attempt": attempt,
                 "outcome": outcome, "latency_ms": round(latency_ms, 1),
                 "input_tokens": response.usage.input_tokens if response else 0,
                 "output_tokens": response.usage.output_tokens if response else 0}
        self.stats.append(entry)
        # One line, fixed fields only. No prompt, no content, no output, no key.
        log.info("provider_call provider=%s model=%s task=%s attempt=%d outcome=%s latency_ms=%.1f "
                 "in_tokens=%d out_tokens=%d", entry["provider"], entry["model"], task, attempt,
                 outcome, entry["latency_ms"], entry["input_tokens"], entry["output_tokens"])
