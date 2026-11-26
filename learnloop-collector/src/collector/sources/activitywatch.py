"""ActivityWatch source (spec §24/§26/§27; decision 2026-11-24 Q12 – APPROVED).

What is read: per day, how long each APPLICATION was used while she was at the computer, and its
ActivityWatch CATEGORY if she defined category rules there. Nothing else.

How it is read – and why not from ActivityWatch's database file:
- Only through ActivityWatch's own local server at 127.0.0.1:5600 (any other address is refused).
- The summary is computed BY ActivityWatch (its query language): events are merged by app and
  category on its side, so window titles and URLs never reach LearnLoop.
- As a second safety layer every returned row is reduced to {app, category, seconds} here.
- The database file sits under AppData, which the Collector never reads (§33).
- The computer name (part of ActivityWatch's bucket ids) is used only to address the query and is
  never stored or sent.

Permissions (§27): its own read permission (`grant`) and its own transfer permission, separate
from the study folder's. Revoking the read permission also ends the transfer permission.
"""

from __future__ import annotations

import http.client
import json
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta

HOST, PORT = "127.0.0.1", 5600
MAX_APPS = 200                      # a day never has more distinct apps than this in practice
MAX_NAME = 120


class ActivityWatchUnavailable(ConnectionError):
    """ActivityWatch is not running – nothing is lost; ActivityWatch keeps its own history."""


class ActivityWatchError(RuntimeError):
    """ActivityWatch is running but refused a request (e.g. its query language differs by version).
    The message holds only ActivityWatch's own short explanation, without Hebrew text."""


@dataclass(frozen=True)
class AppUsage:
    app: str
    category: str | None
    seconds: int


class LocalApi:
    """The only network code here: HTTP to 127.0.0.1:5600."""

    def __init__(self, port: int = PORT, timeout_s: float = 10.0):
        if not isinstance(port, int) or not 1024 <= port <= 65535:
            raise ValueError("port must be between 1024 and 65535")
        self.port, self.timeout_s = port, timeout_s

    def call(self, method: str, path: str, body=None):
        conn = http.client.HTTPConnection(HOST, self.port, timeout=self.timeout_s)
        try:
            data = json.dumps(body).encode() if body is not None else None
            conn.request(method, path, data, {"Content-Type": "application/json"} if data else {})
            r = conn.getresponse()
            raw = r.read()
        except (ConnectionError, OSError) as e:
            raise ActivityWatchUnavailable("ActivityWatch is not running") from e
        finally:
            conn.close()
        if r.status == 404:
            return None
        if r.status >= 400:
            import re
            try:
                info = json.loads(raw or b"{}")
                why = info.get("message") or info.get("error") if isinstance(info, dict) else None
            except ValueError:
                why = None
            why = " ".join(str(why or "").split())
            why = re.sub(r"aw-watcher-[\w-]+|[\u0590-\u05FF]+|(\\u[0-9a-fA-F]{4})+", "…", why)[:200]
            # (bucket names carry the computer name, which includes her name – never shown)
            raise ActivityWatchError(f"ActivityWatch answered {r.status} to {path}" + (f": {why}" if why else ""))
        return json.loads(raw or b"null")


def _clean(text) -> str | None:
    if not isinstance(text, str):
        return None
    text = "".join(ch for ch in text if ch.isprintable()).strip()
    return text[:MAX_NAME] or None


def _bucket(buckets: dict, prefix: str) -> str | None:
    ids = sorted(b for b in buckets if isinstance(b, str) and b.startswith(prefix))
    return ids[0] if ids else None


def _category_rules(api) -> list:
    """Her category rules as defined in ActivityWatch ('classes'). Absent = no categories."""
    try:
        rules = api.call("GET", "/api/0/settings/classes")
    except ActivityWatchUnavailable:
        return []
    if not isinstance(rules, list):
        return []
    out = []
    for r in rules:
        name, rule = r.get("name") if isinstance(r, dict) else None, r.get("rule") if isinstance(r, dict) else None
        if isinstance(name, list) and all(isinstance(n, str) for n in name) and isinstance(rule, dict) \
                and rule.get("type") in ("regex", "none"):
            out.append([name, rule])
    return out


def build_query(window: str, afk: str, rules: list) -> list[str]:
    lines = [
        # ensure_ascii=False: ActivityWatch's query language does not decode \uXXXX escapes, and the
        # bucket names contain the computer name (Hebrew letters here).
        f'events = flood(query_bucket({json.dumps(window, ensure_ascii=False)}));',
        f'not_afk = flood(query_bucket({json.dumps(afk, ensure_ascii=False)}));',
        'not_afk = filter_keyvals(not_afk, "status", ["not-afk"]);',
        'events = filter_period_intersect(events, not_afk);',
    ]
    if rules:
        lines.append(f'events = categorize(events, {json.dumps(rules, ensure_ascii=False)});')
        lines.append('events = merge_events_by_keys(events, ["app", "$category"]);')
    else:
        lines.append('events = merge_events_by_keys(events, ["app"]);')
    lines.append("RETURN = sort_by_duration(events);")
    return lines


def day_usage(day: date, api=None) -> list[AppUsage]:
    api = api or LocalApi()
    buckets = api.call("GET", "/api/0/buckets/") or {}
    window, afk = _bucket(buckets, "aw-watcher-window_"), _bucket(buckets, "aw-watcher-afk_")
    if not window or not afk:
        return []
    start = datetime(day.year, day.month, day.day).astimezone()      # local midnight, with offset
    period = f"{start.isoformat()}/{(start + timedelta(days=1)).isoformat()}"
    result = api.call("POST", "/api/0/query/", {"timeperiods": [period],
                                                 "query": build_query(window, afk, _category_rules(api))})
    rows = result[0] if isinstance(result, list) and result and isinstance(result[0], list) else []
    usage: dict[tuple, float] = {}
    for ev in rows[:MAX_APPS * 4]:
        data = ev.get("data") if isinstance(ev, dict) else None
        if not isinstance(data, dict):
            continue
        app = _clean(data.get("app"))                  # ONLY these two fields are ever looked at
        cat = data.get("$category")
        category = " > ".join(c for c in (_clean(x) for x in cat) if c) if isinstance(cat, list) else None
        if app is None:
            continue
        if category in ("", "Uncategorized"):
            category = None
        key = (app, category)
        usage[key] = usage.get(key, 0.0) + float(ev.get("duration") or 0)
    out = [AppUsage(a, c, int(s)) for (a, c), s in usage.items() if s >= 60]     # under a minute: noise
    return sorted(out, key=lambda u: -u.seconds)[:MAX_APPS]


# --------------------------------------------------------------------------- permissions
def new_grant() -> str:
    return "aw-" + secrets.token_hex(8)


def summary_envelope(day: date, usage: list[AppUsage], grant_id: str) -> dict:
    """The activity_summary contract (contracts/activity_summary.schema.json). No titles, no URLs,
    no computer name."""
    return {"schema_version": 1, "type": "activity_summary", "source": {"type": "activitywatch",
            "source_id": "activitywatch", "grant_id": grant_id}, "day": day.isoformat(),
            "apps": [{"app": u.app, "category": u.category, "minutes": round(u.seconds / 60)} for u in usage],
            "content_trust": "untrusted_user_data"}
