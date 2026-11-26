"""ActivityWatch day summaries -> the local brain (Q12 + Q8). Only with its own transfer permission
(`activitywatch transfer on`). Finished days only (not today), the last 7, each sent once per
approval (local_data/aw_sent.json)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from collector.config import load_config
from collector.sources import activitywatch as aw

STATE = Path(__file__).resolve().parents[3] / "local_data" / "aw_sent.json"


def send_days(link, config_path=None, state_path: Path = STATE, today=None, api=None, days: int = 7) -> dict:
    cfg = load_config(config_path).config if config_path else load_config().config
    report = {"sent": 0, "stopped": None}
    if not cfg.activitywatch or not cfg.activitywatch.get("transfer"):
        report["stopped"] = "ActivityWatch transfer is not switched on"
        return report
    grant = cfg.activitywatch["grant_id"]
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    sent = set(state.get(grant, []))
    today = today or date.today()
    for back in range(days, 0, -1):
        day = today - timedelta(days=back)
        if day.isoformat() in sent:
            continue
        try:
            usage = aw.day_usage(day, api)
        except (aw.ActivityWatchUnavailable, aw.ActivityWatchError) as e:
            report["stopped"] = str(e)
            break
        ack = link.send_activity(aw.summary_envelope(day, usage, grant))
        if ack.get("accepted"):
            sent.add(day.isoformat())
            report["sent"] += 1
    state[grant] = sorted(sent)[-60:]
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return report
