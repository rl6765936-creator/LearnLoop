"""Evaluation on SYNTHETIC data (spec §43–§49, APPROVED approach).

- dataset.py   – scenarios covering the spec §46 list (+ prompt injection)
- metrics.py   – runs them and measures classification, HITL behaviour, privacy, reliability
- compare.py   – before/after comparison against PREDEFINED thresholds (spec §48)
- telemetry.py – the only data that may ever leave for monitoring: metadata, allow-listed

Nothing here connects to LangSmith yet. A LangSmith adapter will receive exactly what
telemetry.py exports, and nothing else (spec §12: metadata only by default).
"""
