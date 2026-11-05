"""Late injury check: what changed since the morning board.

Robin bets in the morning (best lines, boosts), so the site keeps the
morning board as a checkpoint and every later run says what moved since
then: who was ruled out (or cleared), and whose projection shifted because
of it. The Today page shows a banner and marks the moved cells; the Parlay
page flags any leg whose player or projection changed.

When the checkpoint is taken (docs/data/checkpoint.json):
  - the 6 AM run, the one that buys lines (the board you bet against), or
  - the first run for a new board date (the 8 PM flip) if there's none yet.
Later runs the same date compare against it and never overwrite it.

When checks run: the regular 3 PM Arizona run, plus the "Injury check"
workflow every 30 minutes through the afternoon and evening. That workflow
calls `python -m nproj injury-check`, which does the full refresh only when
a game tips off within the next WINDOW_MIN minutes (so each game gets one
or two checks shortly before tip-off, when late scratches are announced)
and otherwise exits in seconds.

Statuses come from ESPN's team rosters (the same feed as the morning run).
NBA rules require teams to update a player's status as soon as a decision is
made, so late scratches usually show up there before tip-off, but ESPN can
lag the official report by some minutes.
"""
import json
from datetime import datetime, timedelta, timezone

from . import config

STATS = ("points", "rebounds", "assists", "pra")
MOVE_MIN = {"points": 1.5, "rebounds": 1.0, "assists": 1.0, "pra": 2.0}
WINDOW_MIN = (10, 80)          # check when a game tips off 10-80 minutes from now
MIN_GAP_MIN = 20               # never re-check within this many minutes


def _path():
    return config.SITE_DATA_DIR / "checkpoint.json"


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _norm_status(s):
    s = (s or "active").lower()
    return "active" if s in ("active", "available", "probable") else s


# ------------------------------------------------------------- snapshot ----
def snapshot(con, date_s, board):
    """Everyone on tonight's slate: status, game, and (for board players)
    the projections and calls shown on the board."""
    rows = con.execute(
        """SELECT pp.player_id, pp.game_id, pp.status, p.name, p.team
           FROM probable_players pp JOIN players p ON p.player_id = pp.player_id
           WHERE pp.date = ?""", (date_s,)).fetchall()
    on_board = {str(p["player_id"]): p for p in board.get("players", []) if p.get("player_id")}
    players = {}
    for r in rows:
        pid = str(r["player_id"])
        entry = {"name": r["name"], "team": r["team"], "game_id": str(r["game_id"]),
                 "status": _norm_status(r["status"])}
        b = on_board.get(pid)
        if b:
            entry["proj"] = {k: b["stats"][k]["proj"] for k in STATS if k in b.get("stats", {})}
            entry["call"] = {k: b["stats"][k].get("call") for k in STATS
                             if k in b.get("stats", {}) and b["stats"][k].get("line") is not None}
            if b.get("minutes") is not None:
                entry["minutes"] = b["minutes"]
        players[pid] = entry
    return players


# ------------------------------------------------------------- compare ----
def compare(before, after):
    """-> (status_changes, moves) between two snapshots."""
    status_changes, moves = [], []
    for pid, now in after.items():
        was = before.get(pid)
        if not was:
            continue
        # only players who matter tonight: on the board before or after
        # (skips two-way and deep-bench players whose status flips all day)
        if was["status"] != now["status"] and ("proj" in was or "proj" in now):
            status_changes.append({"player_id": pid, "player": now["name"], "team": now["team"],
                                   "game_id": now["game_id"], "from": was["status"],
                                   "to": now["status"]})
        if "proj" in was and "proj" in now:
            delta = {k: round(now["proj"][k] - was["proj"][k], 1)
                     for k in STATS if k in now["proj"] and k in was["proj"]}
            big = {k: d for k, d in delta.items() if abs(d) >= MOVE_MIN[k]}
            flips = {k: {"from": was.get("call", {}).get(k), "to": now.get("call", {}).get(k)}
                     for k in now.get("call", {})
                     if was.get("call", {}).get(k) != now.get("call", {}).get(k)}
            if big or flips:
                moves.append({"player_id": pid, "player": now["name"], "team": now["team"],
                              "game_id": now["game_id"], "delta": delta,
                              "from": was["proj"], "to": now["proj"], "call_flips": flips})
    rank = {"out": 0, "doubtful": 1, "questionable": 2, "day-to-day": 3}
    status_changes.sort(key=lambda c: (rank.get(c["to"], 9), c["team"], c["player"]))
    moves.sort(key=lambda m: -abs(m["delta"].get("pra", 0)))
    return status_changes, moves


# -------------------------------------------------------------- update ----
def update(con, date_s, morning=False, now=None):
    """Take the checkpoint or compare against it; annotate today.json and
    parlay.json. -> log dict."""
    now = now or _now()
    site = config.SITE_DATA_DIR
    board = _load(site / "today.json")
    if board.get("date") != date_s or not board.get("players"):
        return {"skipped": "no board for this date"}
    cp = _load(_path())
    current = snapshot(con, date_s, board)

    if morning or cp.get("date") != date_s:
        label = "this morning's board" if morning else "last night's board"
        cp = {"date": date_s, "taken_at": _iso(now), "label": label, "players": current,
              "checks": []}
        _save(_path(), cp)
        board.pop("injury_check", None)
        for p in board["players"]:
            p.pop("since", None)
        _save(site / "today.json", board)
        _annotate_parlay(site, date_s, {}, {}, current)
        return {"checkpoint": label, "players": len(current)}

    changes, moves = compare(cp["players"], current)
    cp["checks"] = (cp.get("checks", []) + [_iso(now)])[-40:]
    _save(_path(), cp)

    names = {c["game_id"] for c in changes}
    board["injury_check"] = {
        "checked_at": _iso(now), "since": cp["taken_at"], "since_label": cp.get("label"),
        "status_changes": changes,
        "moves": [{k: m[k] for k in ("player", "team", "delta", "from", "to", "call_flips")}
                  for m in moves[:15]],
        "games_affected": sorted(names),
    }
    by_pid = {m["player_id"]: m for m in moves}
    for p in board["players"]:
        m = by_pid.get(str(p.get("player_id")))
        if m:
            p["since"] = {"delta": m["delta"], "call_flips": m["call_flips"]}
        else:
            p.pop("since", None)
    _save(site / "today.json", board)
    _annotate_parlay(site, date_s, {c["player_id"]: c for c in changes}, by_pid, current)
    return {"since": cp["taken_at"], "status_changes": len(changes), "moves": len(moves)}


def _annotate_parlay(site, date_s, changes, moves, current):
    """Flag parlay legs whose player changed status or whose projection moved
    against the leg since the checkpoint. The legs themselves never change
    after the morning pick (see parlay.update)."""
    path = site / "parlay.json"
    data = _load(path)
    today = data.get("today")
    if not today or today.get("date") != date_s or not today.get("legs"):
        return
    before = json.dumps(today, sort_keys=True)
    for leg in today["legs"]:
        pid = str(leg.get("player_id"))
        flag = {}
        c = changes.get(pid)
        if c:
            flag.update({"kind": "status", "from": c["from"], "to": c["to"]})
        m = moves.get(pid)
        if m and leg["stat"] in m["delta"] and abs(m["delta"][leg["stat"]]) >= MOVE_MIN[leg["stat"]]:
            d = m["delta"][leg["stat"]]
            flag.setdefault("kind", "projection")
            flag.update({"proj_from": m["from"][leg["stat"]], "proj_to": m["to"][leg["stat"]],
                         "against": (d < 0) if leg["side"] == "over" else (d > 0)})
        if flag:
            leg["check"] = flag
        else:
            leg.pop("check", None)
    if json.dumps(today, sort_keys=True) != before:
        _save(path, data)


# ------------------------------------------------------------- gating ----
def due(games, last_check=None, now=None):
    """Should the injury-check workflow run the full refresh now? `games`
    are schedule dicts with 'tipoff_utc'. -> (bool, reason)."""
    now = now or _now()
    if last_check:
        try:
            last = datetime.strptime(last_check, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if now - last < timedelta(minutes=MIN_GAP_MIN):
                return False, f"checked {int((now - last).total_seconds() // 60)} min ago"
        except ValueError:
            pass
    lo, hi = now + timedelta(minutes=WINDOW_MIN[0]), now + timedelta(minutes=WINDOW_MIN[1])
    soon = []
    for g in games:
        t = g.get("tipoff_utc")
        if not t:
            continue
        try:
            tip = datetime.fromisoformat(t.replace("Z", "+00:00"))
        except ValueError:
            continue
        if tip.tzinfo is None:
            tip = tip.replace(tzinfo=timezone.utc)
        if lo <= tip <= hi:
            soon.append(f"{g.get('away_team', '?')} @ {g.get('home_team', '?')}")
    if soon:
        return True, "tipping soon: " + ", ".join(soon)
    return False, "no game tips off in the next 10-80 minutes"


def last_check(date_s):
    cp = _load(_path())
    if cp.get("date") != date_s:
        return None
    return (cp.get("checks") or [None])[-1]
