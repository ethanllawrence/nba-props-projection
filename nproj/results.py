"""Results tracking for the Today board: grade every night's projections
against the real box scores, so the site keeps an honest record of how
accurate the model is and whether its edge calls beat the line.

Flow (every daily run):
  1. If the saved snapshot is from an earlier date and that night's games are
     over (util.day_is_final), grade it and add the night to the history.
     A snapshot whose games may still be running (the 8 PM Arizona run flips
     the board early) is left alone until the next morning.
  2. Snapshot today's board (docs/data/today.json) as the new pending night.
     Later runs the same day overwrite it, so the graded version is the last
     pre-game board.

What gets graded, per player/stat with a posted projection:
  - accuracy: absolute error (MAE) and signed error (bias) vs. the actual stat;
  - edge calls: any stat whose projection sits 6%+ from the book line (the
    board's green/red cells). Over call wins if actual > line, under call
    wins if actual < line. At -110 juice, 52.4% is break-even.
Players who didn't play are skipped.

State: docs/data/results.json (committed). Nightly summaries are kept for
the whole season; the individual edge calls for the last CALL_LOG_KEEP.
"""
import json
from datetime import date, datetime, timezone

from . import config, util

EDGE = 0.06
STATS = ("points", "rebounds", "assists", "pra")
CALL_LOG_KEEP = 400
BREAKEVEN = 0.524


def _path():
    return config.SITE_DATA_DIR / "results.json"


def _load():
    try:
        with open(_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"pending": None, "nights": [], "calls": []}


def _save(data):
    data["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def snapshot_from_board(board):
    """today.json -> the compact per-player record grading needs."""
    players = []
    for p in board.get("players", []):
        if not p.get("player_id"):
            continue
        s = p.get("stats", {})
        stats = {k: {"proj": s[k]["proj"], "line": s[k].get("line")}
                 for k in ("points", "rebounds", "assists") if k in s}
        if len(stats) == 3:
            pra_line = (s.get("pra") or {}).get("line")
            if pra_line is None and all(stats[k]["line"] is not None for k in stats):
                pra_line = sum(stats[k]["line"] for k in stats)
            stats["pra"] = {"proj": round(sum(stats[k]["proj"] for k in ("points", "rebounds", "assists")), 1),
                            "line": pra_line}
        players.append({"player_id": p["player_id"], "player": p["player"], "team": p.get("team"),
                        "stats": stats})
    return {"date": board["date"], "players": players}


def call_for(proj, line):
    """'over' / 'under' when the projection is 6%+ off the line, else None."""
    if line in (None, 0) or proj is None:
        return None
    edge = (proj - line) / line
    return "over" if edge >= EDGE else "under" if edge <= -EDGE else None


def grade_call(side, line, actual):
    if actual == line:
        return "push"
    return "win" if (actual > line) == (side == "over") else "loss"


def _actuals(con, player_id, date_s):
    r = con.execute(
        """SELECT minutes, points, rebounds, assists FROM player_game_logs
           WHERE player_id=? AND date=?""", (player_id, date_s)).fetchone()
    if not r or not r["minutes"]:
        return None
    return {"points": r["points"], "rebounds": r["rebounds"], "assists": r["assists"],
            "pra": r["points"] + r["rebounds"] + r["assists"]}


def grade_night(con, snap, load_logs=True):
    """-> (night summary, list of graded edge calls)."""
    from . import pipeline

    d = snap["date"]
    errs = {k: [] for k in STATS}
    calls = []
    graded = 0
    for p in snap["players"]:
        if load_logs:
            pipeline.load_player_logs(con, p["player_id"], date.fromisoformat(d), seasons=1)
        act = _actuals(con, p["player_id"], d)
        if act is None:
            continue
        graded += 1
        for k, v in p["stats"].items():
            errs[k].append(v["proj"] - act[k])
            side = call_for(v["proj"], v["line"])
            if side:
                calls.append({"date": d, "player": p["player"], "team": p.get("team"), "stat": k,
                              "side": side, "proj": v["proj"], "line": v["line"], "actual": act[k],
                              "result": grade_call(side, v["line"], act[k])})

    def mae(xs):
        return round(sum(abs(x) for x in xs) / len(xs), 2) if xs else None

    def bias(xs):
        return round(sum(xs) / len(xs), 2) if xs else None

    night = {
        "date": d, "players_graded": graded,
        "mae": {k: mae(errs[k]) for k in STATS},
        "bias": {k: bias(errs[k]) for k in STATS},
        "n": {k: len(errs[k]) for k in STATS},
        "calls": _record(calls),
    }
    return night, calls


def _record(calls):
    w = sum(1 for c in calls if c["result"] == "win")
    l = sum(1 for c in calls if c["result"] == "loss")
    return {"wins": w, "losses": l, "pushes": len(calls) - w - l}


def summarize(data):
    """Season totals for the page: record by stat and side, weighted MAE."""
    nights = data.get("nights", [])
    calls = data.get("calls", [])
    tot = {"wins": 0, "losses": 0, "pushes": 0}
    for n in nights:
        for k in tot:
            tot[k] += n["calls"][k]
    by_stat = {}
    for k in STATS:
        cs = [c for c in calls if c["stat"] == k]
        by_stat[k] = _record(cs)
    by_side = {s: _record([c for c in calls if c["side"] == s]) for s in ("over", "under")}
    mae = {}
    for k in STATS:
        num = sum((n["mae"][k] or 0) * n["n"][k] for n in nights)
        den = sum(n["n"][k] for n in nights)
        mae[k] = round(num / den, 2) if den else None
    decided = tot["wins"] + tot["losses"]
    return {"nights": len(nights), "record": tot,
            "hit_rate": round(tot["wins"] / decided, 3) if decided else None,
            "breakeven": BREAKEVEN, "mae": mae,
            "by_stat_recent": by_stat, "by_side_recent": by_side,
            "recent_calls_window": len(calls)}


def update(con, board_date_s):
    """Grade a finished night if there is one, then snapshot today's board.
    -> log dict. Writes results.json only when something changed."""
    data = _load()
    before = json.dumps({k: v for k, v in data.items() if k != "generated_at"}, sort_keys=True)
    log = {}

    pend = data.get("pending")
    if pend and pend["date"] < board_date_s:
        if not util.day_is_final(pend["date"]):
            log["waiting_to_grade"] = pend["date"]
            return log  # keep tonight's snapshot until its games are over
        night, calls = grade_night(con, pend)
        if not any(n["date"] == night["date"] for n in data["nights"]):
            data["nights"].append(night)
            data["calls"] = (data.get("calls", []) + calls)[-CALL_LOG_KEEP:]
        data["pending"] = None
        log["graded"] = {"date": night["date"], "players": night["players_graded"], **night["calls"]}

    try:
        with open(config.SITE_DATA_DIR / "today.json", encoding="utf-8") as f:
            board = json.load(f)
    except (OSError, ValueError):
        board = {}
    if board.get("date") == board_date_s and any(p.get("player_id") for p in board.get("players", [])):
        data["pending"] = snapshot_from_board(board)
        log["snapshot"] = {"date": board_date_s, "players": len(data["pending"]["players"])}

    data["summary"] = summarize(data)
    after = json.dumps({k: v for k, v in data.items() if k != "generated_at"}, sort_keys=True)
    if after != before:
        _save(data)
        log["written"] = True
    return log
