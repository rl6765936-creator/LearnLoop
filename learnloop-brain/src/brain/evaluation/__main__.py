"""Run the evaluation:   python -m brain.evaluation [LABEL] [--compare BASELINE_LABEL]

Writes local_data/eval/<LABEL>.json with METRICS and scenario ids only (no content).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from brain.evaluation.compare import compare
from brain.evaluation.metrics import evaluate

OUT_DIR = Path(__file__).resolve().parents[3] / "local_data" / "eval"


def main(argv: list[str]) -> int:
    label = argv[0] if argv and not argv[0].startswith("--") else "latest"
    if not label.replace("-", "").replace("_", "").isalnum():
        print("label: letters, digits, - and _ only")
        return 2
    result = evaluate(label)
    result.pop("telemetry")                 # the records themselves are not saved
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"{label}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(f"Evaluation '{label}' – {len(result['scenarios'])} synthetic scenarios")
    for name, value in result["metrics"].items():
        print(f"  {name:26} {value}")
    failed = [s for s in result["scenarios"] if s["failures"]]
    for s in failed:
        print(f"  FAILED scenario {s['id']}: {', '.join(s['failures'])}")

    if "--compare" in argv:
        base_label = argv[argv.index("--compare") + 1]
        baseline = json.loads((OUT_DIR / f"{base_label}.json").read_text(encoding="utf-8"))
        passed, reasons = compare(baseline, result)
        print(f"\nCompared with '{base_label}': {'PASSED' if passed else 'FAILED'}")
        for r in reasons:
            print(f"  - {r}")
        return 0 if passed else 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
