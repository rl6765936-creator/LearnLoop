"""Entry point of the LearnLoop Local Collector.

Usage (from the src folder):
    python -m collector.main status
    python -m collector.main grant [FOLDER]     (no FOLDER = open a folder picker)
    python -m collector.main revoke
    python -m collector.main pause | resume
    python -m collector.main exclude PATTERN | unexclude PATTERN
    python -m collector.main check PATH         (would the Collector be allowed to read PATH?)
    python -m collector.main read PATH          (extract text from one file and show a preview)
    python -m collector.main scan               (one detection cycle: what is new / changed / removed)
    python -m collector.main watch              (keep watching the folder until Ctrl+C)
    python -m collector.main review             (files held because of a possible secret)
    python -m collector.main review approve|ignore|exclude FILE
    python -m collector.main review text FILE "content"   (type the content of an unreadable file)
    python -m collector.main ocr-languages      (which OCR languages Windows has)
    python -m collector.main outbox             (what WOULD be sent to the AI – filtered only)
    python -m collector.main transfer-permission on|off   (AI transfer permission, separate from reading)
    python -m collector.main transfer           (DRY RUN: shows what would be sent; sends nothing)
    python -m collector.main inventory [--save] (how much material exists + token/cost ESTIMATE;
                                                 local only, no file names, changes nothing)
    python -m collector.main collections        (folders inside the approved folder + their classification)
    python -m collector.main collections set FOLDER completed|active|not_studied|unknown [--period TEXT]
    python -m collector.main collections start  (ongoing work starts now: new/changed files go first)
    python -m collector.main progress           (live queue and gradual backfill, per folder)
    python -m collector.main send-to-brain      (REAL transfer, only to the local brain at 127.0.0.1)
    python -m collector.main brain-link --token-file PATH [--port 8765]  (connect to the local brain)
    python -m collector.main upload FILE       (manual upload: one file, one-time approval)
    python -m collector.main git [add|remove|preview|transfer-on|transfer-off PATH]  (Git repositories, approved one by one)
    python -m collector.main activitywatch [grant|revoke|preview [--day YYYY-MM-DD]|transfer-on|transfer-off]
                                                (app name + category only; preview sends nothing)
"""

from __future__ import annotations

import argparse
from pathlib import Path
import unicodedata

from collector.config import DEFAULT_CONFIG_PATH, load_config
from collector.monitor.monitor import CycleReport, Monitor
from collector.permissions import folder_access
from collector.permissions.path_guard import BUILTIN_EXCLUSIONS, PathGuard
from collector.readers.extract import extract_file
from collector.security import outbox
from collector.security.filter import PENDING_REVIEW, filter_result
from collector.readers import base
from collector.security.review import APPROVE, IGNORE, save_decision, save_manual

PREVIEW_CHARS = 500


def show_status() -> None:
    result = load_config()
    config = result.config
    print("LearnLoop Local Collector – prototype")
    print(f"Settings file: {DEFAULT_CONFIG_PATH}")
    if result.warning:
        print(f"WARNING: {result.warning}")
    if config.approved_folder is None:
        print("Status: no folder approved – the Collector reads nothing.")
    elif config.paused:
        print(f"Status: PAUSED – approved folder: {config.approved_folder}")
    else:
        print(f"Status: active – approved folder: {config.approved_folder}")
    print(f"Your exclusions: {config.exclusions or 'none'}")
    print(f"Built-in exclusions: {', '.join(BUILTIN_EXCLUSIONS)}")


def show_extraction(path: str) -> None:
    result = extract_file(path, PathGuard(load_config().config))
    print(f"File:     {result.path}")
    print(f"Status:   {result.status}" + (f" ({result.error})" if result.error else ""))
    if result.reader:
        print(f"Reader:   {result.reader}")
    for warning in result.warnings:
        print(f"WARNING:  {warning}")
    if result.segments:
        print(f"Segments: {len(result.segments)} – " +
              ", ".join(s.location for s in result.segments[:5]) +
              (" ..." if len(result.segments) > 5 else ""))
        print(f"Characters: {len(result.text):,}")
        outcome = filter_result(result)
        print(f"Security: {outcome.status}" +
              (f" – {len(outcome.redactions)} item(s) removed" if outcome.redactions else ""))
        if outcome.status == PENDING_REVIEW:
            for u in outcome.uncertain:
                print(f"  possible secret: {u.kind} in {u.location}")
            print("--- no preview: content is held until reviewed ---")
        else:
            print("--- preview (after filtering) ---")
            print("\n\n".join(seg.text for seg in outcome.segments)[:PREVIEW_CHARS])


def shown(name: str) -> str:
    """Show a file name, warning if it contains invisible characters
    (Windows in Hebrew adds direction marks when copying/renaming)."""
    if any(unicodedata.category(ch) == "Cf" for ch in name):
        clean = "".join(ch for ch in name if unicodedata.category(ch) != "Cf")
        return f"{clean} [name contains hidden characters]"
    return name


class WatchPrinter:
    """In watch mode, show blocked files and duplicates only when they are NEW,
    instead of repeating them on every cycle."""

    def __init__(self):
        self.seen_blocked: set = set()
        self.seen_duplicates: set = set()

    def __call__(self, report: CycleReport) -> None:
        blocked = [b for b in report.blocked if b not in self.seen_blocked]
        duplicates = [g for g in report.duplicates if tuple(g) not in self.seen_duplicates]
        self.seen_blocked.update(report.blocked)
        self.seen_duplicates.update(tuple(g) for g in report.duplicates)
        report.blocked, report.duplicates = blocked, duplicates
        print_report(report, quiet_if_nothing=True)


def print_report(report: CycleReport, quiet_if_nothing: bool = False) -> None:
    lines = [f"  NEW:       {shown(r)}" for r in report.added]
    lines += [f"  CHANGED:   {shown(r)}" for r in report.modified]
    lines += [f"  RETRIED:   {shown(r)}" for r in report.retried]
    lines += [f"  REVIEWED:  {shown(r)} (your decision applied)" for r in report.released]
    lines += [f"  MOVED:     {shown(a)} -> {shown(b)}" for a, b in report.moved]
    lines += [f"  REMOVED:   {shown(r)}" for r in report.removed]
    lines += [f"  SAME CONTENT (date only): {shown(r)}" for r in report.touched]
    lines += [f"  WAITING (still being written): {shown(r)}" for r in report.pending]
    nothing_new = not (lines or report.notes or report.blocked or report.duplicates
                       or report.persistent_failures or report.review_needed)
    if quiet_if_nothing and nothing_new and report.status == "ok":
        return
    print(f"[{report.status}]")
    for note in report.notes:
        print(f"  NOTE: {note}")
    print("\n".join(lines) if lines else "  no changes")
    for result in report.results:
        extra = f" – {result.error}" if result.error else ""
        print(f"    {shown(result.path.name)}: {result.status}{extra}, {len(result.segments)} segments")
    for rel, count in report.redacted:
        print(f"  FILTERED:  {shown(rel)} – {count} sensitive item(s) removed before the outbox")
    for rel, reason in report.blocked:
        print(f"  BLOCKED:   {shown(rel)} ({reason})")
    for group in report.duplicates:
        print(f"  DUPLICATES (identical content): {', '.join(shown(g) for g in group)}")
    for rel, error in report.persistent_failures:
        print(f"  ATTENTION: {shown(rel)} could not be read after several attempts: {error}")
    for rel, where in report.review_needed:
        print(f"  ATTENTION: {shown(rel)} is HELD – possible secret ({where}). "
              f"Nothing from it was sent. Run: review")


def review_command(action: str | None, target: str | None, text: str | None = None) -> None:
    monitor = Monitor()
    held = {rel: r for rel, r in monitor.state.files.items()
            if r.status == PENDING_REVIEW or r.status == base.FAILED}
    if action is None:
        if not held:
            print("Nothing is waiting for review.")
        for rel, record in held.items():
            if record.status == PENDING_REVIEW:
                print(f"HELD: {shown(rel)}\n      reason: possible secret ({record.error})\n"
                      f"      options: approve / ignore / exclude")
            else:
                print(f"UNREADABLE: {shown(rel)}\n      reason: {record.error}\n"
                      f"      options: text \"<content>\" / ignore / exclude")
        return
    if target is None:
        print("Please name the file, for example: review approve notes.txt")
        return
    # Accept the name as shown, even without the hidden characters.
    strip = lambda n: "".join(ch for ch in n if unicodedata.category(ch) != "Cf").replace("\\", "/")
    matches = [rel for rel in held if strip(rel) == strip(target)]
    if not matches:
        print(f"'{target}' is not waiting for review.")
        return
    rel = matches[0]
    if action == "approve" and held[rel].status != PENDING_REVIEW:
        print("'approve' is for files held because of a possible secret. "
              "For an unreadable file use: text / ignore / exclude")
        return
    if action == "text":
        if not text or not text.strip():
            print('Please give the content, for example: review text scan.png "Binary trees, lecture 3"')
            return
        save_manual(held[rel].sha256, text)
        print(f"OK: your text will be used for {shown(rel)} (it still passes the security filter)")
    elif action == "exclude":
        print(("OK: " if folder_access.add_exclusion(rel).ok else "REFUSED: ") +
              f"{shown(rel)} will never be read again")
    else:
        save_decision(held[rel].sha256, APPROVE if action == "approve" else IGNORE)
        print(f"OK: decision '{action}' saved for this exact content of {shown(rel)}")
        twins = [r for r, rec in held.items() if r != rel and rec.sha256 == held[rel].sha256]
        if twins:
            print("    It also applies to identical copies: " + ", ".join(shown(t) for t in twins))
    print_report(Monitor(settle_seconds=0).run_once())


def outbox_command() -> None:
    config = load_config().config
    entries = outbox.list_entries(outbox.DEFAULT_OUTBOX)
    if not entries:
        print("The outbox is empty. (Run: scan)")
    for e in entries:
        allowed, reason = outbox.transfer_gate(e, config)
        text = "\n".join(seg.get("text", "") for seg in e.get("segments", []))
        print(f"=== {shown(str(e.get('source')))}  [{e.get('filter_status')}, "
              f"{len(e.get('redactions', []))} removed, {len(e.get('segments', []))} segments, "
              f"transfer: {'allowed' if allowed else 'BLOCKED – ' + reason}]")
        for warning in e.get("warnings", []):
            print(f"    WARNING: {warning}")
        print(text[:300] + (" ..." if len(text) > 300 else ""))
    isolated = outbox.list_quarantine(outbox.DEFAULT_OUTBOX)
    if isolated:
        print(f"\n{len(isolated)} item(s) isolated from an earlier approval – "
              f"kept locally, never transferred.")


def transfer_dry_run() -> None:
    """Real transfer is not enabled (transfer design not approved yet): dry run only."""
    from collector.transfer.client import TransferClient
    from collector.transfer.transports import DryRunTransport
    report = TransferClient(DryRunTransport()).run()
    print("DRY RUN – nothing was sent.")
    if report["stopped"]:
        print(f"Stopped: {report['stopped']}")
    for item in report.get("would_send", []):
        extra = f", {item['redactions']} redacted" if item["redactions"] else ""
        print(f"  would send: {shown(item['file'])} [{item['change']}, {item['chunks']} chunk(s), "
              f"{item['bytes']:,} bytes{extra}]")
        for w in item["warnings"]:
            print(f"    WARNING: {w}")
    if not report.get("would_send") and not report["stopped"]:
        print("Nothing new to send.")
    if "live_pending" in report:
        print(f"Queues: live {report['live_pending']} part(s) first, backfill {report['backfill_pending']} "
              f"part(s); {report['backfill_deferred']} backfill part(s) wait for a later day (daily limit).")


def activitywatch_command(action: str | None, day: str | None) -> None:
    """Separate permission for ActivityWatch (spec §27). preview shows LOCALLY what would be used:
    app name, category and minutes – nothing is sent."""
    from datetime import date as _date, timedelta
    from collector.config import save_config
    from collector.sources import activitywatch as aw
    config = load_config().config
    if action == "grant":
        config.activitywatch = {"grant_id": aw.new_grant()}
        save_config(config)
        print("ActivityWatch approved: app names and categories only (no window titles, no URLs).")
        return
    if action == "revoke":
        config.activitywatch = None
        save_config(config)
        print("ActivityWatch permission revoked – nothing is read from it.")
        return
    if not config.activitywatch:
        print("ActivityWatch is not approved. To approve: python -m collector.main activitywatch grant")
        return
    if action in ("transfer-on", "transfer-off"):
        config.activitywatch["transfer"] = action == "transfer-on"
        save_config(config)
        print("ActivityWatch day summaries " + ("WILL be sent to the local brain (app, category, minutes)."
                                                if action == "transfer-on" else "will NOT be sent."))
        return
    if action == "preview":
        target = _date.fromisoformat(day) if day else _date.today() - timedelta(days=1)
        try:
            usage = aw.day_usage(target)
        except aw.ActivityWatchUnavailable:
            print("ActivityWatch is not running – nothing to show (nothing is lost).")
            return
        except aw.ActivityWatchError as e:
            print(f"ActivityWatch is running but refused the request – {e}")
            return
        print(f"ActivityWatch {target.isoformat()} – app / category / minutes (local preview, nothing sent):")
        for u in usage:
            print(f"  {shown(u.app)} | {shown(u.category) if u.category else '-'} | {round(u.seconds / 60)}")
        if not usage:
            print("  (no activity of a minute or more)")
        return
    print("ActivityWatch: approved (app names and categories only).")


def git_command(action: str | None, path: str | None) -> None:
    """Approve Git repositories one by one (Q11). preview reads LOCALLY and shows only counts and
    filter results – nothing is sent and nothing from the repository is executed."""
    from collector.config import save_config
    from collector.sources import git_repos as g
    config = load_config().config
    if action == "add":
        ok, why, real = g.check_repository(path or "")
        if not ok:
            print(f"Not approved: {why}"); return
        if any(Path(r["path"]) == real for r in config.git_repos):
            print("Already approved."); return
        config.git_repos.append({"path": str(real), "grant_id": g.new_grant()})
        save_config(config)
        print(f"Approved Git repository: {shown(str(real))} (commit messages + code changes only).")
        return
    if action == "remove":
        target = Path(path or "").resolve()
        before = len(config.git_repos)
        config.git_repos = [r for r in config.git_repos if Path(r["path"]) != target]
        save_config(config)
        print("Permission revoked." if len(config.git_repos) < before else "That repository was not approved.")
        return
    if action in ("transfer-on", "transfer-off"):
        target = Path(path or "").resolve()
        repo = next((r for r in config.git_repos if Path(r["path"]) == target), None)
        if repo is None:
            print("Approve the repository first: git add PATH"); return
        repo["transfer"] = action == "transfer-on"
        save_config(config)
        print("Commits of this repository " + ("WILL be sent to the local brain (filtered)."
                                               if repo["transfer"] else "will NOT be sent."))
        return
    if action == "preview":
        repo = next((r for r in config.git_repos if Path(r["path"]) == Path(path or "").resolve()), None)
        if repo is None:
            print("Approve the repository first: git add PATH"); return
        try:
            commits = g.read_commits(Path(repo["path"]))
        except g.GitUnavailable as e:
            print(str(e)); return
        held = sum(c.status == "pending_review" for c in commits)
        red = sum(c.status == "redacted" for c in commits)
        print(f"{len(commits)} commit(s) read locally; {red} with secrets removed; {held} held for review "
              f"(not transferable). Nothing was sent.")
        return
    for r in config.git_repos:
        print(f"  {shown(r['path'])}")
    if not config.git_repos:
        print("No Git repository is approved.")


def upload_command(path: str | None) -> None:
    """Manual upload (Q13): one file, one confirmation, sent once to the local brain."""
    import json as _json
    from collector.sources.manual_upload import prepare
    from collector.transfer.brain_link import LocalBrainTransport
    from collector.transfer.transports import TransportUnavailable
    config = load_config().config
    if not config.brain_link:
        print("No brain link configured – nothing can be uploaded."); return
    envs, message = prepare(path or "")
    if envs is None:
        print(message); return
    name = Path(path).name
    answer = input(f"Send '{shown(name)}' ONCE to LearnLoop (filtered; {message})? type yes: ").strip().lower()
    if answer != "yes":
        print("Not sent."); return
    link = LocalBrainTransport.from_token_file(config.brain_link["port"], config.brain_link["token_file"])
    try:
        acks = [link.send(_json.dumps(e, ensure_ascii=False).encode("utf-8")) for e in envs]
    except TransportUnavailable:
        print("The brain is not running – nothing was sent."); return
    print("Sent." if all(a.get("accepted") for a in acks) else "The brain did not accept the file.")


SEND_EVERY_S = 600      # automatic sending to the LOCAL brain while `watch` runs [ASSUMPTION: 10 min]


def auto_sender(clock=None):
    """For `watch`: at most every SEND_EVERY_S, the same checked transfer as `send-to-brain`
    (permission + filter result re-checked per part, live first, daily ceiling). Local only –
    the brain decides itself when to use AI (2-hour cycle, budget)."""
    import time
    clock = clock or time.monotonic
    last = [None]

    def tick():
        if not load_config().config.brain_link:
            return
        if last[0] is not None and clock() - last[0] < SEND_EVERY_S:
            return
        last[0] = clock()
        transfer_to_brain(quiet=True)
    return tick


def brain_link_command(port: int, token_file: str) -> None:
    """Point the Collector at the local brain (127.0.0.1 only). Stores the port and the PATH of the
    brain's token file – never the token itself."""
    from collector.config import save_config
    config = load_config().config
    tf = Path(token_file)
    if not tf.is_absolute() or not tf.is_file():
        print("token file not found – start the brain once in --live mode first"); return
    config.brain_link = {"port": int(port), "token_file": str(tf)}
    save_config(config)
    print(f"Brain link set: 127.0.0.1:{port}")


def transfer_to_brain(quiet: bool = False) -> int:
    """Real transfer to the LOCAL brain (127.0.0.1). Every part re-checks permission and the
    filter result before it is sent (transfer_gate); live first, backfill within the daily limit."""
    from collector.transfer.brain_link import LocalBrainTransport, sync_classifications
    from collector.transfer.client import TransferClient
    config = load_config().config
    if not config.brain_link:
        print("No brain link configured (brain_link in local_data/config.json). Nothing was sent.")
        return 2
    transport = LocalBrainTransport.from_token_file(config.brain_link["port"], config.brain_link["token_file"])
    if not config.transfer_allowed() and config.approved_folder:
        from collector.transfer.envelope import source_id
        transport.revoke(source_id(config.approved_folder))     # the brain stops using it too
    report = TransferClient(transport).run()
    if quiet:
        if report["sent"]:
            print(f"[auto] sent {report['sent']} part(s) to the local brain.")
        elif report["stopped"] and "offline" in report["stopped"]:
            print("[auto] the brain is not running – kept for later.")
    else:
        print(f"Sent to the local brain: {report['sent']} part(s), acknowledged {report['acknowledged']}.")
        if report["stopped"]:
            print(f"Stopped: {report['stopped']}")
    if "live_pending" in report and not quiet:
        print(f"Queues: live {report['live_pending']}, backfill {report['backfill_pending']}; "
              f"{report['backfill_deferred']} backfill part(s) wait for a later day (daily limit).")
    from collector.sources.activity_transfer import send_days
    from collector.sources.git_transfer import send_commits
    git = send_commits(transport)
    if git["repos"] and (not quiet or git["sent"]):
        print(f"Git: {git['sent']} commit(s) sent, {git['held']} held for review." +
              (f" Stopped: {git['stopped']}" if git["stopped"] else ""))
    if config.activitywatch and config.activitywatch.get("transfer"):
        act = send_days(transport)
        if not quiet or act["sent"]:
            print(f"ActivityWatch: {act['sent']} day summary(ies) sent." + (f" Stopped: {act['stopped']}" if act["stopped"] else ""))
    try:
        synced = sync_classifications(transport)
    except Exception:                       # the brain may be down; classification sync is optional
        synced = []
    for folder in synced:
        print(f"Classification from the brain applied to folder: {shown(folder)}")
    return 0


# Example list prices (USD per 1M tokens) read on 24.11.2026 from ai.google.dev/gemini-api/docs/pricing.
# For estimates only; local_data/prices.json (same shape) replaces them. Verify before deciding.
# gpt-6-luna: OpenAI list price, checked 25.11.2026 (developers.openai.com) – the APPROVED model (Q4).
EXAMPLE_PRICES = {
    "gpt-6-luna (OpenAI, approved)": {"input_per_mtok": 0.10, "output_per_mtok": 0.50},
    "gemini-3.1-flash-lite (paid)": {"input_per_mtok": 0.25, "output_per_mtok": 1.50},
    "gemini-3.5-flash (paid)": {"input_per_mtok": 1.50, "output_per_mtok": 9.00},
}


STATUS_HE = {"completed": "completed (finished studying)", "active": "active (studying now)",
             "not_studied": "not studied yet", "unknown": "unknown (not classified)"}


def collections_command(action: str | None, folder: str | None, status: str | None, period: str | None) -> None:
    from collector import collections as col
    if action == "set":
        if not folder or not status:
            print('Usage: collections set "FOLDER" completed|active|not_studied|unknown [--period TEXT]')
            return
        ok, message = col.set_collection(folder, status, period)
        print(("OK: " if ok else "REFUSED: ") + message)
        return
    if action == "start":
        ok, message = col.start_live()
        print(("OK: " if ok else "REFUSED: ") + message)
        return
    data = col.overview()
    if "error" in data:
        print(f"Collections: {data['error']}")
        return
    print("Folders in the approved folder (file content is not opened). Classification is YOURS –")
    print("the Collector never guesses it. No extra permission is needed: these are inside the approved folder.")
    print("Ongoing work started: " + (data["live_since"] or "NOT YET – run: collections start  (after classifying)"))
    for r in data["collections"]:
        name = r["collection"] or "(files directly in the approved folder)"
        period = f", period: {r['period']}" if r["period"] else ""
        print(f"  {shown(name):40} {r['files']:>5} files  {r['subfolders']:>3} subfolders  -> "
              f"{STATUS_HE[r['study_status']]}{period}")
    if data["unclassified"]:
        print("Unclassified folders stay 'unknown' until you decide, for example:")
        print('  collections set "שנה א\'" completed --period "שנה א\'"')


def notice_new_collections() -> None:
    """Ask ONCE per new, unclassified folder (grouped per folder, never per file)."""
    from collector import collections as col
    data = col.overview()
    if "error" in data:
        return
    for name in col.new_unclassified(data["collections"]):
        print(f"  CLASSIFY: new folder '{shown(name or '(main folder)')}' is not classified "
              f"(finished / studying now / not studied yet?). Run: collections")


def progress_command() -> None:
    from collector import collections as col
    p = col.progress()
    if "error" in p:
        print(f"Progress: {p['error']}")
        return
    print("Ongoing work started: " + (p["live_since"] or "not yet (everything counts as backfill)"))
    for r in p["rows"]:
        name = r["collection"] or "(main folder)"
        print(f"  {r['queue']:9} {shown(name):35} {r['done']:>5}/{r['files']:<5} done   [{r['study_status']}]")
    print(f"Backfill: {p['backfill_done']}/{p['backfill_files']} files ({p['backfill_percent']}%) – "
          f"daily limit {p['daily_limit_chars']:,} characters; used today: {p['budget_today'].get('chars', 0):,}")
    print("Nothing is transferred until real transfer is approved (decision Q-D).")


def inventory_command(save: bool) -> None:
    import json
    from collector.config import LOCAL_DATA
    from collector.inventory import build_inventory, format_report
    prices_file = LOCAL_DATA / "prices.json"
    prices = EXAMPLE_PRICES
    if prices_file.exists():
        try:
            prices = json.loads(prices_file.read_text(encoding="utf-8"))
        except ValueError:
            print("WARNING: local_data/prices.json is not valid JSON – using the example prices")
    print("Counting the material in the approved folder (read-only, nothing is sent)...")
    report = build_inventory(prices=prices)
    print(format_report(report))
    if save and "error" not in report:
        out = LOCAL_DATA / "inventory.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Saved (aggregate numbers only): {out}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="collector")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("status")
    grant = sub.add_parser("grant")
    grant.add_argument("folder", nargs="?")
    sub.add_parser("revoke")
    sub.add_parser("pause")
    sub.add_parser("resume")
    sub.add_parser("exclude").add_argument("pattern")
    sub.add_parser("unexclude").add_argument("pattern")
    sub.add_parser("check").add_argument("path")
    sub.add_parser("read").add_argument("path")
    sub.add_parser("scan")
    sub.add_parser("watch")
    review = sub.add_parser("review")
    review.add_argument("action", nargs="?", choices=["approve", "ignore", "exclude", "text"])
    review.add_argument("file", nargs="?")
    review.add_argument("text", nargs="?")
    sub.add_parser("ocr-languages")
    sub.add_parser("outbox")
    sub.add_parser("transfer-permission").add_argument("state", choices=["on", "off"])
    sub.add_parser("transfer")
    sub.add_parser("send-to-brain")
    sub.add_parser("upload").add_argument("path")
    bl = sub.add_parser("brain-link")
    bl.add_argument("--port", type=int, default=8765)
    bl.add_argument("--token-file", required=True)
    gitp = sub.add_parser("git")
    gitp.add_argument("action", nargs="?", choices=["add", "remove", "preview", "transfer-on", "transfer-off"])
    gitp.add_argument("path", nargs="?")
    awp = sub.add_parser("activitywatch")
    awp.add_argument("action", nargs="?", choices=["grant", "revoke", "preview", "transfer-on", "transfer-off"])
    awp.add_argument("--day", help="YYYY-MM-DD (default: yesterday)")
    sub.add_parser("inventory").add_argument("--save", action="store_true")
    cols = sub.add_parser("collections")
    cols.add_argument("action", nargs="?", choices=["set", "start"])
    cols.add_argument("folder", nargs="?")
    cols.add_argument("status", nargs="?", choices=["completed", "active", "not_studied", "unknown"])
    cols.add_argument("--period")
    sub.add_parser("progress")
    args = parser.parse_args(argv)

    if args.command in (None, "status"):
        show_status()
        return

    if args.command == "grant":
        folder = args.folder or folder_access.choose_folder_dialog()
        if not folder:
            print("Cancelled – nothing changed.")
            return
        result = folder_access.grant_folder(folder)
    elif args.command == "revoke":
        result = folder_access.revoke_folder()
    elif args.command == "pause":
        result = folder_access.set_paused(True)
    elif args.command == "resume":
        result = folder_access.set_paused(False)
    elif args.command == "exclude":
        result = folder_access.add_exclusion(args.pattern)
    elif args.command == "unexclude":
        result = folder_access.remove_exclusion(args.pattern)
    elif args.command == "review":
        review_command(args.action, args.file, args.text)
        return
    elif args.command == "ocr-languages":
        from collector.ocr.engine import get_engine
        engine = get_engine()
        if engine is None:
            print("No OCR engine is active. Images and scanned PDFs can be typed in manually "
                  "(review text).")
        else:
            print(f"OCR engine: {engine.name}, languages used by the Collector: {', '.join(engine.languages)}")
        try:
            from collector.ocr.windows_ocr import available_languages
            installed = available_languages()
            print("Installed in Windows: " + (", ".join(installed) or "none"))
            hebrew = [t for t in installed if t.lower().startswith(("he", "iw"))]
            print("Hebrew OCR: " + ("INSTALLED (" + ", ".join(hebrew) + ") – not used until approved"
                                    if hebrew else "not installed in Windows"))
        except ImportError:
            print("Installed in Windows: cannot check (Windows OCR packages not available here)")
        print("Note: the Collector uses only the languages above; Hebrew OCR needs approval (decision Q15).")
        return
    elif args.command == "outbox":
        outbox_command()
        return
    elif args.command == "transfer-permission":
        result = folder_access.set_transfer_permission(args.state == "on")
    elif args.command == "transfer":
        transfer_dry_run()
    elif args.command == "send-to-brain":
        transfer_to_brain()
        return
    elif args.command == "brain-link":
        brain_link_command(args.port, args.token_file)
        return
    elif args.command == "upload":
        upload_command(args.path)
        return
    elif args.command == "git":
        git_command(args.action, args.path)
        return
    elif args.command == "activitywatch":
        activitywatch_command(args.action, args.day)
        return
    elif args.command == "inventory":
        inventory_command(args.save)
        return
    elif args.command == "collections":
        collections_command(args.action, args.folder, args.status, args.period)
        return
    elif args.command == "progress":
        progress_command()
        return
    elif args.command == "scan":
        print_report(Monitor(settle_seconds=0).run_once())
        notice_new_collections()
        return
    elif args.command == "watch":
        from collector.monitor.watcher import watch
        print("Watching – press Ctrl+C to stop.")
        try:
            watch(Monitor(), WatchPrinter(), after_cycle=auto_sender())
        except KeyboardInterrupt:
            print("Stopped.")
        return
    elif args.command == "read":
        show_extraction(args.path)
        return
    else:  # check
        decision = PathGuard(load_config().config).check(args.path)
        print(("ALLOWED: " if decision.allowed else "BLOCKED: ") + decision.reason)
        return

    print(("OK: " if result.ok else "REFUSED: ") + result.message)
    if args.command in ("grant", "revoke") and result.ok:
        # Q3 (option A): isolate content from the previous approval right away,
        # not only at the next scan.
        isolated = outbox.quarantine_unauthorized(outbox.DEFAULT_OUTBOX, load_config().config)
        if isolated:
            print(f"{len(isolated)} outbox item(s) from the previous approval were isolated "
                  f"and will never be transferred.")


# Runs main() only when this file is executed directly, not when imported.
if __name__ == "__main__":
    main()
