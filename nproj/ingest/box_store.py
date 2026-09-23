"""Keep history/<season>/ current during the season, so the live model's
features come from the same box-score table it was trained and tested on.

Each run looks for finished games (regular season and playoffs) between the
last stored date and yesterday, fetches their box scores from ESPN (one
summary call per game, free), and appends them to
history/<season>/games.csv.gz and box.csv.gz. A normal morning adds one
night of games (~10 calls); the first run of a season fills in from
October 1. Props aren't stored here: the live site's lines come from The
Odds API.

The daily workflow commits history/ along with docs/data/.
"""
import csv
import gzip
from datetime import date, timedelta
from pathlib import Path

from . import espn_history as H
from .espn import season_year


def _read_ids(path):
    if not path.exists():
        return set(), None
    ids, last = set(), None
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            ids.add(r["game_id"])
            last = max(last or r["date"], r["date"])
    return ids, last


def _append(path, cols, rows):
    exists = path.exists()
    old = []
    if exists:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            old = list(csv.DictReader(f))
    with gzip.open(path, "wt", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(old)
        w.writerows(rows)


def update(root, today: date, max_days=250):
    """Add finished games up to yesterday. -> summary dict."""
    season = season_year(today)
    folder = Path(root) / str(season)
    have, last = _read_ids(folder / "games.csv.gz")
    start = date.fromisoformat(last) if last else date(season - 1, 10, 1)
    end = today - timedelta(days=1)
    if start > end:
        return {"season": season, "new_games": 0}
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)][:max_days]

    new = []
    for day in days:
        data = H._get(f"{H.SITE}/scoreboard", {"dates": day.strftime("%Y%m%d")}) or {}
        for e in data.get("events", []):
            t = (e.get("season") or {}).get("type")
            done = ((e.get("status") or {}).get("type") or {}).get("completed")
            gid = str(e["id"])
            if t in (2, 3) and done and gid not in have:
                new.append((gid, H.et_date(e["date"]).strftime("%Y-%m-%d"), t == 3))

    games, box = [], []
    for gid, d, po in new:
        summ = H._get(f"{H.SITE}/summary", {"event": gid})
        if not summ:
            continue
        try:
            g, b = H.parse_summary(summ, gid, d, season, po)
        except (KeyError, IndexError, TypeError):
            continue
        if g["spread_home"] is None:
            sp, tot, src, _ = H.core_odds(gid)
            g.update(spread_home=sp, total=tot, odds_source=src)
        games.append(g)
        box += b
    if games:
        folder.mkdir(parents=True, exist_ok=True)
        _append(folder / "games.csv.gz", H.GAME_COLS, games)
        _append(folder / "box.csv.gz", H.BOX_COLS, box)
    return {"season": season, "new_games": len(games), "new_box_rows": len(box),
            "through": end.isoformat()}
