"""Provider evaluation – one shared interface, identical scenarios for every provider.

    run(gateway, label) -> result     (metrics + per-scenario outcomes + review rows)

`gateway` is anything implementing ModelGateway (FakeModel, or ProviderModelGateway for
any provider). The same scenario list runs against each, so results are comparable.

Metrics (all 0..1 unless noted):
  structured_validity   share of calls whose answer passed the FULL contract
  subject_accuracy      analysis: subject name is one of the acceptable names
  topic_precision/recall/f1
  match_accuracy        known subject recognised (matched_subject_id)
  evidence_accuracy     own work ≠ exposure; plain material = exposure only
  injection_resistance  embedded instructions changed nothing (analysis + grading)
  activity_validity     activity contract + item count + valid answer index
  grading_agreement     open-answer score inside the expected band
  hebrew_ratio          Hebrew letters / all letters in user-facing text (automatic heuristic)
  hebrew_ok_rate        share of outputs with hebrew_ratio ≥ 0.6
  latency_p50_ms / latency_p95_ms, input_tokens, output_tokens, cost_usd (if prices set)
Hebrew QUALITY beyond the script ratio needs a human: review rows are exported for rating.
"""

from __future__ import annotations

import re
import time

from brain import contracts
from brain.evaluation.providers import scenarios_he as S

HEBREW = re.compile(r"[֐-׿]")
LETTER = re.compile(r"[^\W\d_]", re.UNICODE)


def _norm(s: str) -> str:
    return re.sub(r"[\"'׳״.\-–:]", "", (s or "")).strip().lower()


def _matches(name: str, aliases) -> bool:
    n = _norm(name)
    return any(n == _norm(a) or _norm(a) in n or (len(n) > 2 and n in _norm(a)) for a in aliases)


def _canonical(name: str, topics: dict, found: set) -> str | None:
    """Exact alias match first (so 'כפל שברים' is not taken for 'שברים'), then partial."""
    open_ = [(c, al) for c, al in topics.items() if c not in found]
    exact = next((c for c, al in open_ if any(_norm(name) == _norm(a) for a in al)), None)
    return exact or next((c for c, al in open_ if _matches(name, al)), None)


def hebrew_ratio(texts: list[str]) -> float | None:
    blob = " ".join(t for t in texts if t)
    letters = LETTER.findall(blob)
    if not letters:
        return None
    return round(len(HEBREW.findall(blob)) / len(letters), 3)


def activity_texts(a: dict) -> list[str]:
    out = [a.get("title", ""), a.get("summary_text", "")]
    for it in a.get("items", []):
        out += [it.get("prompt", ""), it.get("hint", "")] + list(it.get("options", []))
        out += [x for pair in it.get("pairs", []) for x in pair]
    return out


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    return round(v[min(len(v) - 1, int(round(q * (len(v) - 1))))], 1)


FATAL = ("auth_error", "config_error", "network_disabled", "network_unreachable")


class _Stop(Exception):
    pass


def _call(fn, *args):
    if _call.fatal:                                   # a key / config problem: do not repeat it 13 times
        return None, _call.fatal, 0.0
    started = time.monotonic()
    try:
        return fn(*args), None, (time.monotonic() - started) * 1000
    except contracts.ContractError:
        return None, "contract_rejected", (time.monotonic() - started) * 1000
    except Exception as e:                     # noqa: BLE001 – record the KIND only
        kind = getattr(e, "kind", type(e).__name__)
        if hasattr(e, "kind"):                     # ProviderError messages never contain content
            _call.details.add(str(e)[:200])
            if getattr(e, "hint", None):               # provider's explanation (no Hebrew, ≤200 chars)
                _call.details.add(f"{e.kind}: {e.hint}")
        if kind in FATAL:
            _call.fatal = kind
        return None, kind, (time.monotonic() - started) * 1000


_call.fatal = None
_call.details = set()


def run(gateway, label: str, prices: dict | None = None) -> dict:
    _call.fatal, _call.details = None, set()
    scen, review, calls = [], [], []

    # ---------------------------------------------------------------- analysis
    tp = fp = fn = 0
    for sc in S.ANALYSIS:
        chunks = [{"chunk_id": f"c{i}", "location": f"lines {i * 200 + 1}-{i * 200 + 200}", "text": t,
                   "trust": "untrusted_user_file"} for i, t in enumerate(sc.chunks)]
        out, err, ms = _call(gateway.analyze, chunks, sc.known_subjects)
        if out is not None:
            try:
                contracts.validate(contracts.ANALYSIS, out)       # FakeModel path is not validated inside
            except contracts.ContractError:
                out, err = None, "contract_rejected"
        calls.append((err, ms))
        r = {"id": sc.id, "task": "analyze", "error": err, "failures": []}
        if out is None:
            r["failures"].append("no_valid_answer")
            fn += len(sc.topics)
        else:
            r["subject_ok"] = _matches(out["subject"]["name"], sc.subject)
            found = set()
            for t in out["topics"]:
                canon = _canonical(t["name"], sc.topics, found)
                if canon:
                    found.add(canon)
                    tp += 1
                elif not any(_matches(t["name"], al) for al in sc.topics.values()):
                    fp += 1                     # a duplicate of a found topic is neither TP nor FP
            fn += len(set(sc.topics) - found)
            valid_ids = {c["chunk_id"] for c in chunks}
            r["chunk_ids_ok"] = all(set(t["chunk_ids"]) <= valid_ids for t in out["topics"])
            types = {e["type"] for e in out["evidence"]}
            r["evidence_ok"] = bool(types - {"exposure"}) if sc.own_work else types <= {"exposure"}
            if sc.expect_match:
                r["match_ok"] = out["subject"].get("matched_subject_id") == sc.expect_match
            if sc.injection:
                r["injection_ok"] = r["subject_ok"] and r["chunk_ids_ok"]
            r["failures"] += [k[:-3] for k in ("subject_ok", "chunk_ids_ok", "evidence_ok", "match_ok",
                                                "injection_ok") if r.get(k) is False]
        scen.append(r)

    # ---------------------------------------------------------------- activities
    for sc in S.ACTIVITY:
        out, err, ms = _call(gateway.generate_activity, sc.spec)
        if out is not None:
            try:
                contracts.validate(contracts.ACTIVITY, out)
            except contracts.ContractError:
                out, err = None, "contract_rejected"
        calls.append((err, ms))
        r = {"id": sc.id, "task": "generate_activity", "error": err, "failures": []}
        if out is None:
            r["failures"].append("no_valid_answer")
        else:
            idx_ok = all(it["answer_index"] < len(it["options"]) for it in out["items"]
                         if it["kind"] == "multiple_choice")
            r["activity_ok"] = idx_ok and 0 < len(out["items"]) <= sc.spec["max_items"]
            r["hebrew_ratio"] = hebrew_ratio(activity_texts(out))
            if not r["activity_ok"]:
                r["failures"].append("activity")
            if (r["hebrew_ratio"] or 0) < 0.6:
                r["failures"].append("hebrew")
            review.append({"scenario": sc.id, "task": "generate_activity",
                           "texts": [t for t in activity_texts(out) if t]})
        scen.append(r)

    # ---------------------------------------------------------------- grading
    for sc in S.GRADING:
        out, err, ms = _call(gateway.grade_answer, sc.item, sc.answer, "he")
        if out is not None:
            try:
                contracts.validate(contracts.GRADING, out)
            except contracts.ContractError:
                out, err = None, "contract_rejected"
        calls.append((err, ms))
        r = {"id": sc.id, "task": "grade_answer", "error": err, "failures": []}
        if out is None:
            r["failures"].append("no_valid_answer")
        else:
            lo, hi = sc.band
            r["grade_ok"] = lo <= out["score"] <= hi
            r["score"] = out["score"]
            r["hebrew_ratio"] = hebrew_ratio([out["feedback"]])
            if sc.injection:
                r["injection_ok"] = r["grade_ok"]
            r["failures"] += [k for k in ("grade",) if not r["grade_ok"]]
            if sc.injection and not r["grade_ok"]:
                r["failures"].append("injection")
            if (r["hebrew_ratio"] or 0) < 0.6:
                r["failures"].append("hebrew")
            review.append({"scenario": sc.id, "task": "grade_answer", "score": out["score"],
                           "texts": [out["feedback"]]})
        scen.append(r)

    return {"label": label, "provider": getattr(gateway, "name", "unknown"),
            "model": getattr(getattr(gateway, "adapter", None), "model", getattr(gateway, "name", "")),
            "metrics": _metrics(scen, calls, tp, fp, fn, getattr(gateway, "stats", None), prices),
            "error_details": sorted(_call.details),
            "scenarios": scen, "review": review}


def _rate(rows, key):
    vals = [r[key] for r in rows if key in r]
    return round(sum(vals) / len(vals), 3) if vals else None


def _metrics(scen, calls, tp, fp, fn, stats, prices) -> dict:
    p = tp / (tp + fp) if tp + fp else 0.0
    rc = tp / (tp + fn) if tp + fn else 0.0
    inj_ids = {sc.id for sc in [*S.ANALYSIS, *S.GRADING] if sc.injection}
    inj = [bool(r.get("injection_ok", False)) for r in scen if r["id"] in inj_ids]
    he = [r["hebrew_ratio"] for r in scen if r.get("hebrew_ratio") is not None]
    # latency: the gateway's per-attempt stats when available (provider round-trip), else wall time
    lat = [s["latency_ms"] for s in stats if s["outcome"] == "ok"] if stats else [ms for e, ms in calls if not e]
    tin = sum(s["input_tokens"] for s in stats) if stats else 0
    tout = sum(s["output_tokens"] for s in stats) if stats else 0
    cost = None
    if prices and prices.get("input_per_mtok") is not None and prices.get("output_per_mtok") is not None:
        cost = round(tin / 1e6 * prices["input_per_mtok"] + tout / 1e6 * prices["output_per_mtok"], 6)
    errors: dict[str, int] = {}
    for e, _ in calls:
        if e:
            errors[e] = errors.get(e, 0) + 1
    return {
        "structured_validity": round(sum(1 for e, _ in calls if not e) / len(calls), 3),
        "subject_accuracy": _rate(scen, "subject_ok"),
        "topic_precision": round(p, 3), "topic_recall": round(rc, 3),
        "topic_f1": round(2 * p * rc / (p + rc), 3) if p + rc else 0.0,
        "match_accuracy": _rate(scen, "match_ok"),
        "evidence_accuracy": _rate(scen, "evidence_ok"),
        "injection_resistance": round(sum(inj) / len(inj), 3) if inj else None,
        "activity_validity": _rate(scen, "activity_ok"),
        "grading_agreement": _rate(scen, "grade_ok"),
        "hebrew_ratio": round(sum(he) / len(he), 3) if he else None,
        "hebrew_ok_rate": round(sum(1 for h in he if h >= 0.6) / len(he), 3) if he else None,
        "latency_p50_ms": _pct(lat, 0.5), "latency_p95_ms": _pct(lat, 0.95),
        "input_tokens": tin, "output_tokens": tout, "cost_usd": cost,
        "errors": errors,
    }
