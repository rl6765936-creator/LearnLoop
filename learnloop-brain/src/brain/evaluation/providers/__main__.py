"""Compare model providers on identical synthetic Hebrew scenarios.

    python -m brain.evaluation.providers --mock [LABEL]
        Offline. Each provider adapter answers through an in-memory simulator.
        Proves the pipeline; says nothing about real quality.

    python -m brain.evaluation.providers --real [LABEL] [--providers anthropic,openai,gemini]
        NOT APPROVED IN THIS STAGE. Refuses to run unless local_data/providers.json allows the
        network AND LEARNLOOP_ALLOW_NETWORK=1 AND each provider's key variable is set AND a real
        model name is configured. Sends ONLY the synthetic scenarios in scenarios_he.py.

Output: local_data/eval/providers/<LABEL>.json (metrics, scenario ids, review texts),
<LABEL>.md (comparison) and review_<LABEL>.md (synthetic outputs for human rating).
Prices (optional): local_data/prices.json  {"anthropic": {"input_per_mtok": 3, "output_per_mtok": 15}, ...}
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

from brain.evaluation.providers.mock import oracle
from brain.evaluation.providers.report import comparison_markdown, review_markdown
from brain.evaluation.providers.runner import run
from brain.providers.base import ProviderError
from brain.providers.config import build_gateway, load_config
from brain.providers.simulator import handler_for

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "local_data" / "eval" / "providers"
ALL = ("anthropic", "openai", "gemini")


def _arg(argv, flag, default=None):
    return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) else default


def main(argv: list[str]) -> int:
    mode = "real" if "--real" in argv else "mock"
    positional = [a for i, a in enumerate(argv) if not a.startswith("--") and
                  (i == 0 or argv[i - 1] not in ("--providers",))]
    label = positional[0] if positional else f"{mode}-latest"
    if not label.replace("-", "").replace("_", "").isalnum():
        print("label: letters, digits, - and _ only")
        return 2
    providers = tuple(p for p in _arg(argv, "--providers", ",".join(ALL)).split(",") if p)
    if set(providers) - set(ALL):
        print("unknown provider")
        return 2
    prices_file = ROOT / "local_data" / "prices.json"
    prices = json.loads(prices_file.read_text(encoding="utf-8")) if prices_file.exists() else {}

    base = load_config()
    results = []
    for p in providers:
        if mode == "mock":
            cfg = replace(base, provider=p, transport="mock")
            gw = build_gateway(cfg, mock_handler=handler_for(p, answer=oracle))
        else:
            cfg = replace(base, provider=p, transport="network")
            try:
                gw = build_gateway(cfg, purpose="evaluation")   # synthetic scenarios only
                gw.transport  # noqa: B018 – constructed above; key format already checked
            except ProviderError as e:
                print(f"{p}: real evaluation refused – {e}")
                return 3
        results.append(run(gw, label, prices.get(p)))

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{label}.json").write_text(json.dumps({"mode": mode, "results": results}, ensure_ascii=False,
                                                  indent=2), encoding="utf-8")
    (OUT / f"{label}.md").write_text(comparison_markdown(results, mode), encoding="utf-8")
    (OUT / f"review_{label}.md").write_text(review_markdown(results), encoding="utf-8")
    print(comparison_markdown(results, mode))
    print(f"saved: {OUT / (label + '.md')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
