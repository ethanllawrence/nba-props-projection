"""Save every run's injury statuses for tonight's slate (Nov 8), so that a
later backtest can test how to treat day-to-day / questionable players: we
have no past statuses today, only who actually sat.

history/<season>/statuses.csv.gz: one row per (date, game, player) with the
status ESPN listed, updated each run until the game tips (then frozen, so the
file keeps the last status before tip-off) and when it was first seen.
"""
import csv
import gzip
import io
import os
from datetime import date as _date, datetime, timezone
from pathlib import Path

from .. import config

COLS = ["date", "game_id", "player_id", "status", "first_status", "first_seen", "last_seen"]


def _root():
    return Path(os.environ.get("NPROJ_HISTORY_DIR", Path(config.ROOT) / "history"))


def archive(con, date_s, now=None, root=None):
    """-> number of rows added or changed."""
    from .espn import season_year
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = con.execute(
        """SELECT pp.player_id, pp.game_id, pp.status, g.tipoff_utc FROM probable_players pp
           JOIN games g ON g.game_id = pp.game_id WHERE pp.date = ?""", (date_s,)).fetchall()
    if not rows:
        return 0
    f = (root or _root()) / str(season_year(_date.fromisoformat(date_s))) / "statuses.csv.gz"
    table = {}
    if f.exists():
        with gzip.open(f, "rt", encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                table[(r["date"], r["game_id"], r["player_id"])] = r
    changed = 0
    for r in rows:
        try:
            tip = datetime.fromisoformat(str(r["tipoff_utc"]).replace("Z", "+00:00"))
            tip = tip if tip.tzinfo else tip.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            tip = None
        if tip is not None and tip <= now:
            continue                                   # game under way: keep the pre-tip status
        k = (date_s, str(r["game_id"]), str(r["player_id"]))
        st = (r["status"] or "active").lower()
        old = table.get(k)
        if old is None:
            table[k] = {"date": date_s, "game_id": k[1], "player_id": k[2], "status": st,
                        "first_status": st, "first_seen": stamp, "last_seen": stamp}
            changed += 1
        elif old["status"] != st:
            old.update(status=st, last_seen=stamp)
            changed += 1
    if changed:
        f.parent.mkdir(parents=True, exist_ok=True)
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=COLS)
        w.writeheader()
        for k in sorted(table):
            w.writerow(table[k])
        with open(f, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as fh:
            fh.write(buf.getvalue().encode("utf-8"))
    return changed
