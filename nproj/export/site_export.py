"""Regenerate docs/data/*.json from the database.

today.json: rebuilt from scratch whenever the date has a real slate. One
row per player who is on a roster for tonight, isn't listed as out, and is
averaging at least MIN_MINUTES_FOR_BOARD over his recent games (so deep
bench players don't flood the table). On a night with no games (offseason,
All-Star break) the existing file is left alone.

Book lines come from docs/data/lines.json (written by the morning odds
run, see nproj/ingest/odds.py). Stats with no posted line get line = null,
and the site shows those projections uncolored. A real PRA line, when
posted, goes in stats.pra; otherwise the page sums the three lines.

The board keeps at most MAX_BOARD_PLAYERS rows: players with any posted line
first (books only post props for players who matter), then by projected PRA.

jokic.json: season summary, recent games and the triple-double call are
all recomputed from his real ESPN game log. The market price is his
triple-double Yes price when the odds run fetched it, otherwise null.
"""
import json
from datetime import datetime, timezone

from .. import config
from ..ingest.odds import load_lines, match_lines
from ..pipeline import JOKIC_ESPN_ID

MIN_MINUTES_FOR_BOARD = 18.0
MAX_BOARD_PLAYERS = 60
MINUTES_LOOKBACK = 10


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def export_all(con, date_s: str):
    n_board = _export_today_board(con, date_s)
    jokic = _export_jokic(con, date_s)
    return {"today_board_players": n_board, "jokic_updated": jokic}


def _recent_minutes(con, player_id, date_s):
    rows = con.execute(
        """SELECT minutes FROM player_game_logs
           WHERE player_id=? AND date < ? AND minutes IS NOT NULL
           ORDER BY date DESC LIMIT ?""",
        (player_id, date_s, MINUTES_LOOKBACK),
    ).fetchall()
    return sum(r["minutes"] for r in rows) / len(rows) if rows else 0.0


def _time_et(tipoff_utc):
    from ..ingest.espn import et_date
    try:
        return et_date(tipoff_utc).strftime("%I:%M %p").lstrip("0")
    except (TypeError, ValueError, AttributeError):
        return ""


def _export_today_board(con, date_s: str):
    rows = con.execute(
        """SELECT pp.player_id, pp.status, p.name, p.team, g.home_team, g.away_team, g.tipoff_utc
           FROM probable_players pp
           JOIN players p ON p.player_id = pp.player_id
           JOIN games g ON g.game_id = pp.game_id
           WHERE pp.date = ? AND COALESCE(pp.status, '') != 'out'""",
        (date_s,),
    ).fetchall()
    if not rows:
        return 0

    book = load_lines(date_s)
    everyone = [x["name"] for x in con.execute(
        """SELECT p.name FROM probable_players pp JOIN players p ON p.player_id = pp.player_id
           WHERE pp.date = ?""", (date_s,))]          # incl. players ruled out
    matched, unmatched = match_lines(everyone, book.get("lines", {}))
    if unmatched:
        print(f"[warn] {len(unmatched)} sportsbook names with a line but no ESPN match "
              f"(add them to NAME_ALIASES in nproj/ingest/odds.py if they're real players "
              f"on tonight's slate): {', '.join(unmatched)}")

    from ..model import live
    use_model = bool(live.PROJ)

    def proj_of(pid, stat):
        row = con.execute("SELECT point FROM projections WHERE player_id=? AND date=? AND stat=?",
                          (pid, date_s, stat)).fetchone()
        return row["point"] if row and row["point"] is not None else None

    players = []
    for r in rows:
        pid = r["player_id"]
        mine = matched.get(r["name"], {})
        stats = {}
        for stat in list(config.TARGET_STATS) + ["pra"]:
            proj = proj_of(pid, stat)
            if proj is None and stat == "pra" and all(k in stats for k in config.TARGET_STATS):
                proj = round(sum(stats[k]["proj"] for k in config.TARGET_STATS), 1)
            if proj is None:
                continue
            ln = mine.get(stat, {})
            entry = {"proj": proj, "line": ln.get("line"), "book": ln.get("book"),
                     "over": ln.get("over"), "under": ln.get("under")}
            if use_model and ln.get("line") is not None:
                view = live.assess(pid, stat, ln["line"], ln.get("over"), ln.get("under"))
                if view:
                    entry.update(view)
            stats[stat] = entry
        if not all(k in stats for k in config.TARGET_STATS):
            continue
        minutes = proj_of(pid, "minutes") if use_model else None
        if (minutes if minutes is not None else _recent_minutes(con, pid, date_s)) < MIN_MINUTES_FOR_BOARD:
            continue
        home = r["team"] == r["home_team"]
        players.append({
            "player": r["name"],
            "player_id": pid,
            "team": r["team"],
            "opp": r["away_team"] if home else r["home_team"],
            "home": home,
            "time_et": _time_et(r["tipoff_utc"]),
            "status": r["status"] if r["status"] not in (None, "active") else None,
            "minutes": minutes,
            "stats": stats,
        })

    def has_line(p):
        return any(v.get("line") is not None for v in p["stats"].values())

    players.sort(key=lambda p: (not has_line(p), -p["stats"]["pra"]["proj"]))
    players = players[:MAX_BOARD_PLAYERS]
    players.sort(key=lambda p: -p["stats"]["points"]["proj"])
    n_lined = sum(1 for p in players if has_line(p))
    n_calls = sum(1 for p in players for v in p["stats"].values() if v.get("call"))
    if n_lined:
        lines_note = (f"Lines from FanDuel/DraftKings, pulled each morning ({n_lined} of "
                      f"{len(players)} players have at least one). ")
        lines_note += (f"{n_calls} cells are colored: the model's chance beats the price's "
                       f"break-even by {round(live.EDGE_MIN * 100)}+ points."
                       if use_model else "No model probabilities today, so nothing is colored.")
    else:
        lines_note = "No sportsbook lines for this date yet, so nothing is colored."
    method = ("Projections come from the PRA model (LightGBM trained on three seasons: recent form, "
              "minutes, opponent, pace and spread, rest, and teammates out). " if use_model else
              "Projections are a recency-weighted average of recent games (the model didn't run). ")
    _save(config.SITE_DATA_DIR / "today.json", {
        "generated_at": _now(),
        "date": date_s,
        "model": "lightgbm" if use_model else "baseline",
        "note": method + lines_note,
        "lines_fetched_at": book.get("fetched_at"),
        "unmatched_lines": unmatched,
        "players": players,
    })
    return len(players)


def _season_label(season):
    return f"{season - 1}-{str(season)[-2:]}"


def _export_jokic(con, date_s: str):
    from ..model import predict

    path = config.SITE_DATA_DIR / "jokic.json"
    data = _load(path)
    before = json.loads(json.dumps(data))

    logs = con.execute(
        """SELECT date, opp, points, rebounds, assists, season, playoff FROM player_game_logs
           WHERE player_id=? AND date < ? ORDER BY date DESC""",
        (JOKIC_ESPN_ID, date_s),
    ).fetchall()
    if not logs:
        return False

    def is_td(g):
        return g["points"] >= 10 and g["rebounds"] >= 10 and g["assists"] >= 10

    # Season summary: most recent season with regular-season games.
    reg = [g for g in logs if not g["playoff"]]
    if reg:
        season = reg[0]["season"]
        games = [g for g in reg if g["season"] == season]
        n = len(games)
        tds = sum(1 for g in games if is_td(g))
        summary = data.get("season_summary", {})
        if summary.get("season_label", "")[:7] != _season_label(season):
            summary.pop("fun_fact", None)  # hand-written for an earlier season
        summary.update({
            "season_label": _season_label(season),
            "games_played": n,
            "triple_doubles": tds,
            "hit_rate": round(tds / n, 3),
            "ppg": round(sum(g["points"] for g in games) / n, 1),
            "rpg": round(sum(g["rebounds"] for g in games) / n, 1),
            "apg": round(sum(g["assists"] for g in games) / n, 1),
        })
        data["season_summary"] = summary

    data["recent_games"] = [
        {"date": g["date"], "opp": g["opp"], "pts": g["points"], "reb": g["rebounds"],
         "ast": g["assists"], "triple_double": is_td(g)}
        for g in logs[:10]
    ]

    season_rate = data["season_summary"]["hit_rate"]
    prob = predict.project_triple_double_prob(con, JOKIC_ESPN_ID, season_hit_rate=season_rate,
                                              before_date=date_s)
    tonight = data.get("tonight") or {}
    game = con.execute(
        """SELECT g.home_team, g.away_team, g.tipoff_utc FROM probable_players pp
           JOIN games g ON g.game_id = pp.game_id
           WHERE pp.player_id=? AND pp.date=?""",
        (JOKIC_ESPN_ID, date_s),
    ).fetchone()
    if game:
        home = game["home_team"] == "DEN"
        tonight["opp"] = f"vs {game['away_team']}" if home else f"@ {game['home_team']}"
        tonight["time_et"] = _time_et(game["tipoff_utc"])
    else:
        tonight["opp"] = None  # Denver is off; the page says so instead of a stale matchup
        tonight["time_et"] = None
    td = load_lines(date_s).get("jokic_td") if game else None
    tonight["market"] = {"side": "yes", "odds": td["odds"], "book": td["book"]} if td else None
    if prob is not None:
        from ..model.live import implied
        n_games = predict.TD_WINDOW
        tp = tonight.setdefault("td_projection", {})
        tp["model_prob"] = prob
        if td:
            # the call is made against the price: yes when the price pays more
            # than his chance deserves
            be = implied(int(td["odds"]))
            tonight["call"] = "yes" if prob > be else "no"
            tp["break_even"] = round(be, 3)
            why = (f"The Yes price ({int(td['odds']):+d}) needs {be * 100:.0f}% to break even, so the call "
                   f"is {'YES' if prob > be else 'NO'}.")
        else:
            tonight["call"] = "yes" if prob >= 0.5 else "no"
            tp.pop("break_even", None)
            why = "No Yes price today, so the call is just whether his chance is above 50%."
        tp["note"] = (
            f"His chance is halfway between his triple-double rate over his last {n_games} games "
            "and 50%. We tested "
            "fancier versions (the PRA model's rebound and assist projections, recent-form blends) "
            "on the last two seasons and none predicted his nightly triple-doubles better than a "
            "coin flip, so his long-run rate pulled toward 50/50 is the honest number. " + why)
    data["tonight"] = tonight
    if {**data, "generated_at": None} == {**before, "generated_at": None}:
        return False  # nothing new (e.g. offseason); skip so the daily run doesn't commit noise
    data["generated_at"] = _now()
    data["note"] = ("Season summary, recent games and the model call come from Jokic's real ESPN "
                    "game log. The market price is his triple-double Yes price from the morning "
                    "odds pull, when there was room in the budget for it.")
    _save(path, data)
    return True
