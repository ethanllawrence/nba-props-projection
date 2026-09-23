"""docs/data/status.json: a heartbeat the site uses to warn when the daily
job has stopped updating (see docs/assets/stale.js).

Rewritten when anything besides the timestamp changes, or when the saved
heartbeat is 12+ hours old, so there's at most about two heartbeat-only
commits a day, even in the offseason."""
import json
from datetime import datetime, timedelta, timezone

from .. import config

HEARTBEAT_EVERY = timedelta(hours=12)


def write_status(board_date_s, games):
    path = config.SITE_DATA_DIR / "status.json"
    now = datetime.now(timezone.utc)
    try:
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, ValueError):
        old = {}
    new = {"board_date": board_date_s, "games": games}
    last = old.get("last_run")
    stale = True
    if last:
        try:
            stale = now - datetime.fromisoformat(last.replace("Z", "+00:00")) >= HEARTBEAT_EVERY
        except ValueError:
            stale = True
    if not stale and {k: old.get(k) for k in new} == new:
        return {"written": False}
    new["last_run"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(new, f, indent=2)
        f.write("\n")
    return {"written": True, **new}
