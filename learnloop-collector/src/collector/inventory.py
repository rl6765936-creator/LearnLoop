"""Local inventory of the approved folder: HOW MUCH material exists – nothing is sent.

Purpose: answer "how big and how expensive would processing the existing material be?"
BEFORE any decision about transfer, using only this computer.

What it does (read-only, same safety path as a normal scan):
  PathGuard + exclusions -> hash (duplicates) -> extract (OCR OFF) -> local security filter
  -> chunking exactly as the transfer would do it -> COUNT characters / chunks / envelopes.
What it does NOT do:
  - send anything anywhere (no network code here);
  - write the Collector state, the outbox or the review list (nothing changes);
  - print or save file names or content. The output is aggregate numbers only.
OCR is skipped on purpose (slow; English-only by decision): images and scanned pages
are COUNTED as "needs OCR" so their share is visible.

Token numbers are ESTIMATES [ASSUMPTION]: tokenizers differ per provider and Hebrew uses
more tokens per character than English. A range is reported, not one number.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict
from pathlib import Path

from collector.config import DEFAULT_CONFIG_PATH, CollectorConfig, load_config
from collector.monitor.scanner import hash_file, scan_folder
from collector.permissions.path_guard import PathGuard
from collector.readers import base
from collector.readers.extract import IMAGE_EXTENSIONS, extract_file
from collector.readers.text_reader import TEXT_EXTENSIONS
from collector.security.filter import PENDING_REVIEW, filter_result
from collector.transfer.envelope import MAX_CHUNKS_PER_ENVELOPE, make_chunks

# ---- estimation constants – all [ASSUMPTION], shown in the report ---------------------
CHARS_PER_TOKEN = {"hebrew": (1.8, 2.5, 3.5), "other": (3.0, 4.0, 5.0)}   # (high-token, mid, low-token)
PROMPT_TOKENS_PER_REQUEST = 1_500     # instructions + answer schema sent with every request
OUTPUT_TOKENS_PER_REQUEST = 700       # one structured analysis answer
DAILY_BUDGETS = (100_000, 300_000, 1_000_000)   # example daily input-token budgets
CODE_EXTENSIONS = TEXT_EXTENSIONS - {".txt", ".md", ".csv", ".json", ".markdown"}

HEBREW = re.compile(r"[֐-׿]")
SKIPPED_PAGES = re.compile(r"(\d+) page\(s\) without text were skipped")
AGE_BUCKETS = (("under_30_days", 30), ("30_to_365_days", 365), ("over_1_year", None))


def _kind(ext: str) -> str:
    if ext == ".pdf":
        return "pdf"
    if ext in (".docx", ".pptx", ".xlsx"):
        return ext[1:]
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in CODE_EXTENSIONS:
        return "code"
    if ext in TEXT_EXTENSIONS:
        return "text"
    return "other"


def _empty_bucket() -> dict:
    return {"files": 0, "bytes": 0, "segments": 0, "chars": 0, "hebrew_chars": 0, "chunks": 0,
            "envelopes": 0}


def _add(bucket: dict, **values) -> None:
    for k, v in values.items():
        bucket[k] += v


def tokens(chars: int, hebrew_chars: int) -> dict:
    other = chars - hebrew_chars
    he, ot = CHARS_PER_TOKEN["hebrew"], CHARS_PER_TOKEN["other"]
    return {"high": round(hebrew_chars / he[0] + other / ot[0]),
            "mid": round(hebrew_chars / he[1] + other / ot[1]),
            "low": round(hebrew_chars / he[2] + other / ot[2])}


def build_inventory(config: CollectorConfig | None = None, config_path: Path = DEFAULT_CONFIG_PATH,
                    now: float | None = None, prices: dict | None = None) -> dict:
    config = config or load_config(config_path).config
    if config.approved_folder is None:
        return {"error": "no folder is approved – nothing was read"}
    if config.paused:
        return {"error": "collection is paused – nothing was read"}
    now = now or time.time()
    guard = PathGuard(config)
    root = Path(config.approved_folder)

    def still_allowed() -> bool:                    # revoke / pause stops the inventory too
        live = load_config(config_path).config if config_path.exists() else config
        return live.approved_folder == config.approved_folder and not live.paused

    scan = scan_folder(root, guard)
    by_kind: dict[str, dict] = {}
    by_age = {name: _empty_bucket() for name, _ in AGE_BUCKETS}
    seen: set[str] = set()
    counts = {"files_found": len(scan.files), "excluded_or_blocked": len(scan.blocked), "duplicates": 0,
              "duplicate_bytes": 0, "held_for_review": 0, "redacted_files": 0, "unreadable": 0,
              "unsupported": 0, "empty": 0, "needs_ocr_files": 0, "needs_ocr_pages": 0,
              "multi_envelope_files": 0, "stopped": None}
    sizes: list[int] = []                           # estimated mid tokens per file (no names)

    total_files = len(scan.files)
    print(f"  {total_files:,} files to count (counts only, no names)...", flush=True)
    for done, (rel, (size, mtime_ns)) in enumerate(sorted(scan.files.items()), 1):
        if done % 200 == 0 or done == total_files:
            print(f"  ...{done:,}/{total_files:,}", flush=True)       # progress, so a long count is visible
        if not still_allowed():
            counts["stopped"] = "permission revoked or paused during the inventory"
            break
        path = root / rel
        ext = path.suffix.lower()
        kind = by_kind.setdefault(_kind(ext), _empty_bucket())
        sha = hash_file(path, guard)
        if sha is None:
            counts["unreadable"] += 1
            continue
        if sha in seen:                             # identical content – would not be sent twice
            counts["duplicates"] += 1
            counts["duplicate_bytes"] += size
            continue
        seen.add(sha)
        age_days = (now - mtime_ns / 1e9) / 86400
        age = next(name for name, limit in AGE_BUCKETS if limit is None or age_days < limit)

        result = extract_file(path, guard, ocr=None, still_allowed=still_allowed)
        _add(kind, files=1, bytes=size)
        if result.status == base.UNSUPPORTED:
            counts["unsupported"] += 1
            continue
        if result.status == base.EMPTY:
            counts["empty"] += 1
            continue
        if result.status != base.OK:
            if ext in IMAGE_EXTENSIONS or "OCR" in (result.error or ""):
                counts["needs_ocr_files"] += 1
            else:
                counts["unreadable"] += 1
            continue
        for w in result.warnings:
            m = SKIPPED_PAGES.search(w)
            if m:
                counts["needs_ocr_pages"] += int(m.group(1))
        outcome = filter_result(result)
        if outcome.status == PENDING_REVIEW:        # would be held, never sent automatically
            counts["held_for_review"] += 1
            continue
        if outcome.redactions:
            counts["redacted_files"] += 1
        chunks = make_chunks([asdict(s) for s in outcome.segments])
        envelopes = max(1, -(-len(chunks) // MAX_CHUNKS_PER_ENVELOPE))
        chars = sum(len(c["text"]) for c in chunks)
        heb = sum(len(HEBREW.findall(c["text"])) for c in chunks)
        if envelopes > 1:
            counts["multi_envelope_files"] += 1
        values = dict(segments=len(outcome.segments), chars=chars, hebrew_chars=heb, chunks=len(chunks),
                      envelopes=envelopes)
        _add(kind, **values)
        _add(by_age[age], files=1, bytes=size, **values)
        sizes.append(tokens(chars, heb)["mid"])

    total = _empty_bucket()
    for b in by_kind.values():
        _add(total, **b)
    content_tokens = tokens(total["chars"], total["hebrew_chars"])
    requests = total["envelopes"]
    input_tokens = {k: v + requests * PROMPT_TOKENS_PER_REQUEST for k, v in content_tokens.items()}
    output_tokens = requests * OUTPUT_TOKENS_PER_REQUEST
    for b in [*by_kind.values(), *by_age.values()]:
        b["tokens_mid"] = tokens(b["chars"], b["hebrew_chars"])["mid"]
    sizes.sort()
    report = {
        "counts": counts,
        "by_type": dict(sorted(by_kind.items())),
        "by_age": by_age,
        "total": total,
        "estimate": {
            "content_tokens": content_tokens,
            "requests": requests,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "largest_file_tokens_mid": sizes[-1] if sizes else 0,
            "median_file_tokens_mid": sizes[len(sizes) // 2] if sizes else 0,
            "days_at_daily_budget": {str(b): round(input_tokens["mid"] / b, 1) for b in DAILY_BUDGETS},
            "assumptions": {"chars_per_token": CHARS_PER_TOKEN,
                            "prompt_tokens_per_request": PROMPT_TOKENS_PER_REQUEST,
                            "output_tokens_per_request": OUTPUT_TOKENS_PER_REQUEST,
                            "ocr": "skipped – images and scanned pages are counted, not read"},
        },
    }
    if prices:
        report["estimate"]["cost_usd"] = {
            name: {k: round(input_tokens[k] / 1e6 * p["input_per_mtok"] + output_tokens / 1e6 * p["output_per_mtok"], 2)
                   for k in ("low", "mid", "high")}
            for name, p in prices.items()}
    return report


def format_report(r: dict) -> str:
    if "error" in r:
        return f"Inventory: {r['error']}"
    c, t, e = r["counts"], r["total"], r["estimate"]
    lines = ["INVENTORY – local only, nothing was sent. Aggregate numbers, no file names.", ""]
    if c["stopped"]:
        lines.append(f"STOPPED: {c['stopped']} (numbers below are partial)")
    lines += [f"Files found: {c['files_found']:,}   excluded/blocked: {c['excluded_or_blocked']:,}   "
              f"identical duplicates: {c['duplicates']:,}",
              f"Held for review (possible secret): {c['held_for_review']:,}   files with removed secrets: "
              f"{c['redacted_files']:,}",
              f"Need OCR (not read): {c['needs_ocr_files']:,} files + {c['needs_ocr_pages']:,} scanned pages   "
              f"unreadable: {c['unreadable']:,}   unsupported: {c['unsupported']:,}   empty: {c['empty']:,}",
              "", f"{'type':8} {'files':>7} {'MB':>8} {'pages/parts':>12} {'chars':>12} {'Hebrew %':>9} "
                  f"{'~tokens':>11} {'requests':>9}"]
    for name, b in [*r["by_type"].items(), ("TOTAL", dict(t, tokens_mid=e["content_tokens"]["mid"]))]:
        he = f"{100 * b['hebrew_chars'] / b['chars']:.0f}%" if b["chars"] else "-"
        lines.append(f"{name:8} {b['files']:>7,} {b['bytes'] / 1e6:>8.1f} {b['segments']:>12,} {b['chars']:>12,} "
                     f"{he:>9} {b['tokens_mid']:>11,} {b['envelopes']:>9,}")
    lines += ["", "By age (last modified):"]
    for name, b in r["by_age"].items():
        lines.append(f"  {name:16} {b['files']:>6,} files  ~{b['tokens_mid']:,} tokens")
    lines += ["", "ESTIMATE for analysing everything once (range = tokenizer uncertainty):",
              f"  requests: {e['requests']:,}   input tokens: {e['input_tokens']['low']:,} – "
              f"{e['input_tokens']['high']:,} (mid {e['input_tokens']['mid']:,})   output tokens: ~{e['output_tokens']:,}",
              f"  typical file ~{e['median_file_tokens_mid']:,} tokens, largest ~{e['largest_file_tokens_mid']:,}; "
              f"files needing several requests: {c['multi_envelope_files']:,}",
              "  days to finish at a daily input budget of: " +
              ", ".join(f"{int(k):,} → {v} days" for k, v in e["days_at_daily_budget"].items())]
    for name, cost in e.get("cost_usd", {}).items():
        lines.append(f"  cost {name}: ${cost['low']} – ${cost['high']} (mid ${cost['mid']})")
    lines += ["", "All token and cost numbers are ESTIMATES (assumptions are listed in the saved JSON)."]
    return "\n".join(lines)
