"""Monthly AI budget (user decision 2026-11-24, APPROVED).

- Internal limit: $0.80 per calendar month (setting `monthly_budget_usd`).
- BEFORE every request: estimate its worst-case cost (all input + the full output allowance)
  and refuse if spent + estimate would exceed the limit. Nothing is sent then.
- AFTER every answer: add the real cost from the provider's token counts.
- When the budget is used up, AI calls stop until the next month. Material waiting in the
  queue is NOT deleted or lost – it stays and is processed later.
- A second, independent limit ($1) is set by the user in the provider's console.

The ledger file holds only month -> dollars and request counts. No content.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from brain.providers.base import ProviderError

HEBREW_SAFE_CHARS_PER_TOKEN = 1.8          # pessimistic: Hebrew can be ~1.8 chars per token


class BudgetExhausted(ProviderError):
    kind = "budget_exhausted"               # not retryable: wait for next month


class BudgetLedger:
    def __init__(self, path: Path | None, monthly_limit_usd: float, prices: dict,
                 clock=lambda: datetime.now(timezone.utc), month_overrides: dict | None = None):
        self.path, self.base_limit, self.prices, self.clock = path, float(monthly_limit_usd), prices, clock
        # Decision 2026-11-25 (option 1): a higher limit for ONE named month (the first month of real
        # material, e.g. {"2026-12": 5.0}); every other month uses the regular limit.
        self.month_overrides = {str(k): float(v) for k, v in (month_overrides or {}).items()}
        self._data = self._load()

    @property
    def limit(self) -> float:
        return self.month_overrides.get(self.month(), self.base_limit)

    # ------------------------------------------------------------------ persistence
    def _load(self) -> dict:
        if self.path and self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                # unreadable ledger: fail CLOSED – treat the month as used up
                return {self.month(): {"usd": float("inf"), "requests": 0, "corrupt": True}}
        return {}

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data), encoding="utf-8")
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------ queries
    def month(self) -> str:
        return self.clock().strftime("%Y-%m")

    def spent(self) -> float:
        return float(self._data.get(self.month(), {}).get("usd", 0.0))

    def remaining(self) -> float:
        return max(0.0, self.limit - self.spent())

    def exhausted(self) -> bool:
        return self.remaining() <= 0.0

    def price(self, model: str) -> dict:
        p = self.prices.get(model)
        if not p:
            # unknown price: we cannot keep the promise "never exceed" – refuse
            raise BudgetExhausted(f"no price configured for model {model}")
        return p

    def estimate(self, model: str, chars: int, max_output_tokens: int) -> float:
        p = self.price(model)
        tokens_in = chars / HEBREW_SAFE_CHARS_PER_TOKEN
        return tokens_in / 1e6 * p["input_per_mtok"] + max_output_tokens / 1e6 * p["output_per_mtok"]

    # ------------------------------------------------------------------ gate + record
    def check(self, model: str, chars: int, max_output_tokens: int) -> float:
        est = self.estimate(model, chars, max_output_tokens)
        if self.spent() + est > self.limit:
            raise BudgetExhausted(f"monthly AI budget would be exceeded (limit ${self.limit:.2f})")
        return est

    def record(self, model: str, input_tokens: int, output_tokens: int, fallback_usd: float) -> float:
        p = self.price(model)
        cost = input_tokens / 1e6 * p["input_per_mtok"] + output_tokens / 1e6 * p["output_per_mtok"]
        if input_tokens == 0 and output_tokens == 0:
            cost = fallback_usd                     # provider gave no counts: assume the estimate
        month = self._data.setdefault(self.month(), {"usd": 0.0, "requests": 0})
        month["usd"] = round(month["usd"] + cost, 6)
        month["requests"] += 1
        self._save()
        return cost

    def status(self) -> dict:
        return {"month": self.month(), "limit_usd": self.limit, "spent_usd": round(self.spent(), 4),
                "remaining_usd": round(self.remaining(), 4), "exhausted": self.exhausted()}
