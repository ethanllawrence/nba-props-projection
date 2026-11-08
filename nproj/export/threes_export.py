"""docs/data/threes.json: the 3PM tab (beta), and its own track record.

One row per player on tonight's slate (not ruled out) projected for at least
MIN_ATTEMPTS three-point attempts, or with a 3PM line posted. Each row has the
model's projected attempts, make rate and made threes, the chance of 1+ .. 6+,
and, when ESPN's free DraftKings feed has them, the book's main 3PM line and
"N+" ladder with the model's chance next to the book's (vig removed).

No calls yet: the model isn't blended with the market (that needs a few
weeks of archived 3PM lines, history/<season>/threes_props.csv.gz), so the
page shows the gap but doesn't flag bets.

Track record: each run saves the pre-tip projections for the night
(docs/data/threes_log.json, a row is frozen once its game tips) and grades
finished nights from the box scores: average miss in made threes, and each
rung's predicted chance against how often it hit.
"""
import json
from datetime import datetime, timezone

from .. import config, util

MIN_ATTEMPTS = 1.5
SHOW_RUNGS = 6
LOG_DAYS = 120
ALT_HOLD = 1.045


def _path(name):
    return config.SITE_DATA_DIR / name


def _load(name):
    try:
        with open(_path(name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(name, data):
    with open(_path(name), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write("\n")


def _implied(price):
    p = int(price)
    return -p / (-p + 100) if p < 0 else 100 / (p + 100)


def _chance_over(rungs, line):
    """Model P(3PM > line) from the 1+ .. N+ chances."""
    k = int(line) + 1                                 # 2.5 -> 3+
    if k <= 0:
        return 1.0
    return rungs[k - 1] if k <= len(rungs) else 0.0


def _tipped(tipoff_utc, now):
    try:
        t = datetime.fromisoformat(str(tipoff_utc).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t <= now


def export_threes(con, date_s, now=None):
    from ..model import live
    from ..ingest import espn_props
    from ..ingest.odds import norm_name
    from .site_export import _time_et
    now = now or datetime.now(timezone.utc)
    have = {pid: p["threes"] for pid, p in live.PROJ.items() if p.get("threes")}
    if not have:
        return {"skipped": "no 3PM projections this run"}
    free = (espn_props.load(date_s) or {}).get("threes") or {}
    lines, ladders = free.get("lines") or {}, free.get("alts") or {}
    rows = con.execute(
        """SELECT pp.player_id, pp.status, p.name, p.team, g.game_id, g.home_team, g.away_team, g.tipoff_utc
           FROM probable_players pp JOIN players p ON p.player_id = pp.player_id
           JOIN games g ON g.game_id = pp.game_id
           WHERE pp.date = ? AND COALESCE(pp.status, '') != 'out'""", (date_s,)).fetchall()
    players = []
    for r in rows:
        pid = str(r["player_id"])
        t = have.get(pid)
        if not t:
            continue
        key = norm_name(r["name"])
        ln, lad = lines.get(key), ladders.get(key) or []
        if t["attempts"] < MIN_ATTEMPTS and not ln:
            continue
        home = r["team"] == r["home_team"]
        rungs = [round(x, 4) for x in t["rungs"][:SHOW_RUNGS]]
        row = {"player": r["name"], "player_id": pid, "team": r["team"],
               "opp": r["away_team"] if home else r["home_team"], "home": home,
               "game_id": str(r["game_id"]), "time_et": _time_et(r["tipoff_utc"]),
               "status": r["status"], "minutes": round(float(live.PROJ[pid].get("minutes") or 0), 1),
               "attempts": round(t["attempts"], 2), "p3": round(t["p3"], 4), "made": round(t["made"], 2),
               "rungs": rungs, "tipped": _tipped(r["tipoff_utc"], now)}
        if ln and ln.get("line") is not None:
            m = _chance_over(t["rungs"], float(ln["line"]))
            b = live.market_prob_over(ln.get("over"), ln.get("under"))
            row["line"] = {"line": ln["line"], "over": ln.get("over"), "under": ln.get("under"),
                           "model_over": round(m, 4), "book_over": None if b is None else round(b, 4),
                           "edge": None if b is None else round(m - b, 4), "book": ln.get("book")}
        steps = []
        for st in lad:
            if st.get("over") is None or st.get("line") is None:
                continue
            k = int(float(st["line"])) + 1                # 1.5 -> 2+
            if k < 1 or k > len(t["rungs"]):
                continue
            mk, bk = t["rungs"][k - 1], min(_implied(st["over"]) / ALT_HOLD, 0.995)
            steps.append({"n": k, "price": int(st["over"]), "model": round(mk, 4), "book": round(bk, 4),
                          "edge": round(mk - bk, 4)})
        if steps:
            row["ladder"] = sorted(steps, key=lambda s: s["n"])
        players.append(row)
    players.sort(key=lambda p: (-p["made"], p["player"]))
    log = _update_log(con, date_s, players, now)
    data = {"generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "date": date_s, "beta": True,
            "lines_from": "ESPN (DraftKings)" if lines or ladders else None,
            "lines_fetched_at": (espn_props.load(date_s) or {}).get("fetched_at"),
            "players": players, "track": log}
    _save("threes.json", data)
    return {"players": len(players), "with_line": sum(1 for p in players if p.get("line")),
            "graded_days": log.get("days", 0)}


def _update_log(con, date_s, players, now):
    """Save tonight's pre-tip projections, grade finished nights, summarize."""
    log = _load("threes_log.json")
    days = log.setdefault("days", {})
    tonight = days.setdefault(date_s, {})
    for p in players:
        if tonight.get(p["player_id"], {}).get("frozen"):
            continue                                  # game tipped: keep the pre-tip numbers
        tonight[p["player_id"]] = {"player": p["player"], "team": p["team"], "attempts": p["attempts"],
                                   "made": p["made"], "rungs": p["rungs"],
                                   "line": (p.get("line") or {}).get("line"),
                                   "model_over": (p.get("line") or {}).get("model_over"),
                                   "book_over": (p.get("line") or {}).get("book_over"),
                                   "frozen": p["tipped"]}
    box = None
    for d, rows in days.items():
        if d >= date_s or not util.day_is_final(d, now) or all("actual" in r for r in rows.values()):
            continue
        if box is None:
            box = _box_threes()
        for pid, r in rows.items():
            if "actual" in r:
                continue
            got = box.get((d, pid))
            if got is None:                           # box store first (every game), then the db
                g = con.execute("SELECT minutes, threes FROM player_game_logs WHERE player_id=? AND date=?",
                                (pid, d)).fetchone()
                got = None if g is None else (g["minutes"], g["threes"])
            if got is None:
                if d < _days_before(date_s, 3):
                    r["actual"] = None                # never showed up in a box score: drop it
                continue
            mins, made = got
            r["actual"] = None if not mins else int(made or 0)    # didn't play: not graded
    for d in sorted(days)[:-LOG_DAYS]:
        del days[d]
    _save("threes_log.json", log)
    return summarize(days)


def _days_before(date_s, n):
    from datetime import date, timedelta
    return (date.fromisoformat(date_s) - timedelta(days=n)).isoformat()


def _box_threes():
    """{(date, player_id): (minutes, 3PM)} from history/<season>/box.csv.gz."""
    import os
    from pathlib import Path
    import pandas as pd
    root = Path(os.environ.get("NPROJ_HISTORY_DIR", Path(config.ROOT) / "history"))
    out = {}
    for f in sorted(root.glob("*/box.csv.gz"))[-2:]:
        try:
            b = pd.read_csv(f, usecols=["date", "player_id", "minutes", "fg3m"], dtype={"player_id": str})
        except (OSError, ValueError):
            continue
        for d, pid, m, x in b.itertuples(index=False):
            out[(str(d)[:10], pid)] = (0 if m != m else m, 0 if x != x else x)
    return out


def summarize(days):
    graded = [r for rows in days.values() for r in rows.values() if r.get("actual") is not None]
    nights = sum(1 for rows in days.values() if any(r.get("actual") is not None for r in rows.values()))
    if not graded:
        return {"days": 0}
    out = {"days": nights, "player_games": len(graded),
           "avg_miss_made": round(sum(abs(r["made"] - r["actual"]) for r in graded) / len(graded), 3),
           "bias_made": round(sum(r["made"] - r["actual"] for r in graded) / len(graded), 3),
           "rungs": {}}
    for k in range(1, 5):
        said = sum(r["rungs"][k - 1] for r in graded) / len(graded)
        hit = sum(1 for r in graded if r["actual"] >= k) / len(graded)
        out["rungs"][f"{k}+"] = {"said": round(said, 4), "hit": round(hit, 4)}
    lined = [r for r in graded if r.get("line") is not None and r.get("model_over") is not None]
    if lined:
        out["lines"] = {"n": len(lined),
                        "model_said_over": round(sum(r["model_over"] for r in lined) / len(lined), 4),
                        "book_said_over": round(sum(r["book_over"] or 0 for r in lined) / len(lined), 4),
                        "over_hit": round(sum(1 for r in lined if r["actual"] > r["line"]) / len(lined), 4)}
    return out
