"""Provider configuration and the gateway factory – "switch provider by configuration".

Config file: local_data/providers.json (git-ignored, optional). Example:

    {
      "provider": "anthropic",          // fake | anthropic | openai | gemini
      "transport": "mock",              // blocked (default) | mock | network
      "models": {"anthropic": "SET-AT-ACTIVATION", "openai": "SET-AT-ACTIVATION",
                 "gemini": "SET-AT-ACTIVATION"},
      "allow_network": false
    }

The file holds NO keys. Only the NAMES of environment variables (defaults below). A config
that contains anything that looks like a key value is rejected.

Real network use needs two locks AND a real model name:
  1. "transport": "network" and "allow_network": true in the file, and
  2. environment variable LEARNLOOP_ALLOW_NETWORK=1 in the shell that runs the brain.
[APPROVED restriction, 24.11.2026: local development only – do not open these locks until
real-provider evaluation is separately approved.]
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from brain.model_gateway import FakeModel
from brain.providers.adapters import ADAPTERS, AnthropicAdapter
from brain.providers.base import ConfigError
from brain.providers.budget import BudgetLedger
from brain.providers.gateway import ProviderModelGateway
from brain.providers.simulator import handler_for
from brain.providers.transport import BlockedTransport, MockTransport, NetworkTransport

PROVIDERS = ("fake", "anthropic", "openai", "gemini")
TRANSPORTS = ("blocked", "mock", "network")
PLACEHOLDER = "SET-AT-ACTIVATION"
NETWORK_ENV = "LEARNLOOP_ALLOW_NETWORK"
DEFAULT_KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}
CONFIG_PATH = Path(__file__).resolve().parents[3] / "local_data" / "providers.json"

_KNOWN = {"account_tier", "provider", "transport", "models", "allow_network", "key_env", "timeout_s", "max_attempts",
          "temperature", "anthropic_version", "max_input_chars", "output_tokens", "language",
          "min_interval_s", "rate_limit_wait_s", "gemini_thinking_level", "tls_mode",
          "monthly_budget_usd", "prices", "task_models", "allowed_models", "budget_month_overrides"}
LEDGER_PATH = CONFIG_PATH.parent / "ai_budget.json"
# Decision 2026-11-24: OpenAI – gpt-6-luna for routine work, gpt-6.1-terra only where quality
# tests show it is needed; gpt-6.2-sol not used. Prices: list prices read 24.11.2026 [verify].
DEFAULT_ALLOWED = {"openai": ["gpt-6-luna", "gpt-6.1-terra"]}
DEFAULT_PRICES = {"gpt-6-luna": {"input_per_mtok": 0.10, "output_per_mtok": 0.50},
                  "gpt-6.1-terra": {"input_per_mtok": 1.00, "output_per_mtok": 5.00},
                  "gemini-3.1-flash-lite": {"input_per_mtok": 0.25, "output_per_mtok": 1.50}}
TASKS = ("analyze", "generate_activity", "grade_answer")
_KEY_LIKE = ("sk-", "sk_", "AIza", "ant-api", "Bearer ", "AQ.")
_KEY_PATTERN = __import__("re").compile(r"(sk-[A-Za-z0-9_-]{8,}|sk_[A-Za-z0-9]{8,}|AIza[0-9A-Za-z_-]{10,}|"
                                         r"AQ\.[A-Za-z0-9_-]{10,}|ant-api[0-9A-Za-z_-]*|Bearer\s+\S+)")


def _values(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _values(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _values(v)
    elif isinstance(obj, str):
        yield obj


@dataclass
class ProviderConfig:
    provider: str = "fake"
    transport: str = "blocked"
    models: dict = field(default_factory=lambda: {p: PLACEHOLDER for p in DEFAULT_KEY_ENV})
    allow_network: bool = False
    key_env: dict = field(default_factory=lambda: dict(DEFAULT_KEY_ENV))
    timeout_s: float = 60.0
    max_attempts: int = 3
    temperature: float | None = None
    anthropic_version: str = "2023-06-01"     # [ASSUMPTION] verify the current value at activation
    max_input_chars: int = 120_000            # local budget; set per model at activation
    output_tokens: dict = field(default_factory=dict)
    language: str = "he"
    # "free" | "paid" | "unset". Real material (purpose="application") over the network needs
    # "paid": free tiers may use content to improve products and let humans read it
    # (e.g. Gemini API Additional Terms, Unpaid Services). A free tier may only ever receive
    # the SYNTHETIC evaluation scenarios (purpose="evaluation").
    account_tier: str = "unset"
    min_interval_s: float = 0.0          # gap between requests (free tiers: a few per minute)
    rate_limit_wait_s: float = 0.0       # wait after HTTP 429 without Retry-After
    gemini_thinking_level: str | None = None   # minimal | low | medium | high (model-dependent)
    tls_mode: str = "default"            # see transport.tls_context – verification is never off
    monthly_budget_usd: float = 0.80     # APPROVED 2026-11-24: internal monthly limit, checked before every send
    # APPROVED 2026-11-25 (option 1): $5 for the FIRST month of real material only, e.g. {"2026-12": 5.0}.
    # Set at activation; at most one month above the regular limit.
    budget_month_overrides: dict = field(default_factory=dict)
    prices: dict = field(default_factory=lambda: {k: dict(v) for k, v in DEFAULT_PRICES.items()})
    task_models: dict = field(default_factory=dict)      # e.g. {"generate_activity": "gpt-6.1-terra"}
    allowed_models: dict = field(default_factory=lambda: {k: list(v) for k, v in DEFAULT_ALLOWED.items()})

    def model(self) -> str:
        return self.models.get(self.provider, PLACEHOLDER)

    def check(self) -> "ProviderConfig":
        if self.provider not in PROVIDERS:
            raise ConfigError("unknown provider")
        if self.transport not in TRANSPORTS:
            raise ConfigError("unknown transport")
        if self.account_tier not in ("free", "paid", "unset"):
            raise ConfigError("account_tier must be free, paid or unset")
        if not 0 <= float(self.min_interval_s) <= 120 or not 0 <= float(self.rate_limit_wait_s) <= 300:
            raise ConfigError("min_interval_s must be 0..120 and rate_limit_wait_s 0..300 seconds")
        if not 0 <= float(self.monthly_budget_usd) <= 1000:
            raise ConfigError("monthly_budget_usd must be 0..1000")
        import re as _re
        ov = self.budget_month_overrides
        if not isinstance(ov, dict) or len(ov) > 1 or any(
                not _re.fullmatch(r"\d{4}-\d{2}", str(k)) or not 0 <= float(v) <= 5 for k, v in ov.items()):
            raise ConfigError('budget_month_overrides: at most one month, e.g. {"2026-12": 5.0}, and at most $5')
        for task in self.task_models:
            if task not in TASKS:
                raise ConfigError("task_models keys: " + ", ".join(TASKS))
        allowed = self.allowed_models.get(self.provider)
        if allowed:
            for m in [self.model(), *self.task_models.values()]:
                if m != PLACEHOLDER and m not in allowed:
                    raise ConfigError(f"model not allowed for {self.provider} (allowed: {', '.join(allowed)})")
        from brain.providers.transport import TLS_MODES
        if self.tls_mode not in TLS_MODES:
            raise ConfigError("tls_mode must be one of: " + ", ".join(TLS_MODES))
        if self.gemini_thinking_level not in (None, "minimal", "low", "medium", "high"):
            raise ConfigError("gemini_thinking_level must be minimal, low, medium or high")
        if not 1 <= int(self.max_attempts) <= 6:
            raise ConfigError("max_attempts must be 1..6")
        for name in self.key_env.values():
            if not str(name).replace("_", "").isalnum() or _KEY_PATTERN.search(str(name)):
                raise ConfigError("key_env holds environment-variable NAMES, never keys")
        return self


def from_dict(data: dict) -> ProviderConfig:
    unknown = set(data) - _KNOWN
    if unknown:
        # e.g. "api_key": keys never go into the config file
        raise ConfigError("unknown config fields: " + ", ".join(sorted(unknown)))
    if any(_KEY_PATTERN.search(v) for v in _values(data)):
        raise ConfigError("the config looks like it contains a key – use environment variables")
    cfg = ProviderConfig()
    for k, v in data.items():
        if k in ("models", "key_env", "output_tokens", "prices", "task_models", "allowed_models"):
            getattr(cfg, k).update(v)
        else:
            setattr(cfg, k, v)
    return cfg.check()


def load_config(path: Path | str | None = None) -> ProviderConfig:
    p = Path(path) if path else CONFIG_PATH
    if not p.exists():
        return ProviderConfig()               # default: FakeModel, network blocked
    return from_dict(json.loads(p.read_text(encoding="utf-8")))


PURPOSES = ("application", "evaluation")


def build_transport(cfg: ProviderConfig, mock_handler=None, analysis_script=None, environ=os.environ,
                    purpose: str = "application"):
    if cfg.transport == "blocked":
        return BlockedTransport()
    if cfg.transport == "mock":
        return MockTransport(mock_handler or handler_for(cfg.provider, analysis_script))
    # network: both locks + a real model name
    if not cfg.allow_network or environ.get(NETWORK_ENV) != "1":
        raise ConfigError(f"network transport needs allow_network=true AND {NETWORK_ENV}=1")
    if cfg.model() == PLACEHOLDER:
        raise ConfigError("set a real model name for the provider before using the network")
    if purpose not in PURPOSES:
        raise ConfigError("unknown purpose")
    if purpose == "application" and cfg.account_tier != "paid":
        raise ConfigError("real study material needs a PAID account tier (account_tier=paid); "
                          "a free or unknown tier may only run the synthetic evaluation")
    return NetworkTransport(cfg.key_env[cfg.provider], timeout_s=cfg.timeout_s, tls_mode=cfg.tls_mode)


def build_adapter(cfg: ProviderConfig, model: str | None = None):
    cls, model = ADAPTERS[cfg.provider], model or cfg.model()
    if cls is AnthropicAdapter:
        return cls(model, api_version=cfg.anthropic_version)
    if cfg.provider == "gemini":
        return cls(model, thinking_level=cfg.gemini_thinking_level)
    return cls(model)


def build_gateway(cfg: ProviderConfig | None = None, *, analysis_script=None, mock_handler=None,
                  sleep=None, environ=os.environ, purpose: str = "application", budget=None):
    """The one factory the application uses. provider=fake → FakeModel (default)."""
    cfg = (cfg or ProviderConfig()).check()
    if cfg.provider == "fake":
        return FakeModel(analysis_script or {"subject": {"name": "לא ידוע", "confidence": 0.3},
                                             "topics": [], "evidence": []})
    kwargs = {"sleep": sleep} if sleep else {}
    if budget is None and cfg.transport == "network" and cfg.account_tier != "free":
        budget = BudgetLedger(LEDGER_PATH, cfg.monthly_budget_usd, cfg.prices,
                              month_overrides=cfg.budget_month_overrides)
    task_adapters = {task: build_adapter(cfg, m) for task, m in cfg.task_models.items()}
    return ProviderModelGateway(build_adapter(cfg),
                                build_transport(cfg, mock_handler, analysis_script, environ, purpose),
                                language=cfg.language, max_attempts=cfg.max_attempts,
                                max_input_chars=cfg.max_input_chars, temperature=cfg.temperature,
                                output_tokens=cfg.output_tokens, min_interval_s=cfg.min_interval_s,
                                rate_limit_wait_s=cfg.rate_limit_wait_s, budget=budget,
                                task_adapters=task_adapters, **kwargs)
