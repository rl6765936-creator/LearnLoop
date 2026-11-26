"""Comparison report (Markdown) across providers – metrics only, no content.
The separate review file (review_<label>.md) holds the synthetic outputs for human rating."""

from __future__ import annotations

ROWS = [
    ("structured_validity", "תקינות פלט מובנה"), ("subject_accuracy", "דיוק זיהוי מקצוע"),
    ("topic_f1", "F1 נושאים"), ("topic_precision", "דיוק נושאים"), ("topic_recall", "כיסוי נושאים"),
    ("match_accuracy", "זיהוי מקצוע קיים"), ("evidence_accuracy", "סוג ראיה"),
    ("injection_resistance", "עמידות להוראות זדוניות"), ("activity_validity", "תקינות פעילויות"),
    ("grading_agreement", "התאמת בדיקת תשובות"), ("hebrew_ratio", "שיעור עברית (אוטומטי)"),
    ("hebrew_ok_rate", "פלטים בעברית תקינה"), ("latency_p50_ms", "זמן תגובה חציוני (ms)"),
    ("latency_p95_ms", "זמן תגובה p95 (ms)"), ("input_tokens", "טוקנים נכנסים"),
    ("output_tokens", "טוקנים יוצאים"), ("cost_usd", "עלות ($)"),
]


def _fmt(v):
    if v is None:
        return "—"
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


def comparison_markdown(results: list[dict], mode: str) -> str:
    head = ("> **מצב MOCK – תשובות מדומות מקומיות.** התוצאות בודקות את התשתית בלבד ואינן אומרות דבר "
            "על איכות, זמן או עלות של ספק אמיתי.\n\n" if mode == "mock" else
            "> מצב אמיתי – חומר מומצא בלבד.\n\n")
    lines = [f"# השוואת ספקים – {results[0]['label'] if results else ''}", "", head.rstrip(), "",
             "| מדד | " + " | ".join(f"{r['provider']} ({r['model']})" for r in results) + " |",
             "|---|" + "---|" * len(results)]
    for key, title in ROWS:
        lines.append(f"| {title} | " + " | ".join(_fmt(r["metrics"].get(key)) for r in results) + " |")
    lines.append("| שגיאות | " + " | ".join(
        ", ".join(f"{k}×{v}" for k, v in r["metrics"]["errors"].items()) or "—" for r in results) + " |")
    lines += ["", "## תרחישים שנכשלו"]
    any_fail = False
    for r in results:
        for s in r["scenarios"]:
            if s["failures"]:
                any_fail = True
                lines.append(f"- {r['provider']}: `{s['id']}` – {', '.join(s['failures'])}"
                             + (f" (שגיאה: {s['error']})" if s.get("error") else ""))
    if not any_fail:
        lines.append("- אין")
    details = sorted({d for r in results for d in r.get("error_details", [])})
    if details:
        lines += ["", "## פירוט השגיאות (בלי תוכן)"] + [f"- {d}" for d in details]
    lines += ["", "שיעור העברית הוא בדיקה אוטומטית של הכתב בלבד. איכות הניסוח נבדקת ידנית בקובץ ה-review."]
    return "\n".join(lines) + "\n"


def review_markdown(results: list[dict]) -> str:
    lines = ["# בדיקה אנושית של איכות העברית (חומר מומצא)", "",
             "דרגי כל פלט 1–5 (5 = עברית טבעית ונכונה) בעמודה האחרונה.", "",
             "| ספק | תרחיש | משימה | טקסט | ציון 1–5 |", "|---|---|---|---|---|"]
    for r in results:
        for row in r["review"]:
            text = " / ".join(row["texts"]).replace("|", "¦").replace("\n", " ")
            lines.append(f"| {r['provider']} | {row['scenario']} | {row['task']} | {text} | |")
    return "\n".join(lines) + "\n"
