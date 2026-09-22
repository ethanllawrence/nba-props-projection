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
from ..ingest.odds import load_lines, norm_name
from ..pipeline import JOKIC_ESPN_ID

MIN_MINUTES_FOR_BOARD = 20.0
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
    all_lines = book.get("lines", {})

    players = []
    for r in rows:
        mine = all_lines.get(norm_name(r["name"]), {})
        stats = {}
        for stat in config.TARGET_STATS:
            proj = con.execute(
                "SELECT point FROM projections WHERE player_id=? AND date=? AND stat=?",
                (r["player_id"], date_s, stat),
            ).fetchone()
            if proj and proj["point"] is not None:
                ln = mine.get(stat, {})
                stats[stat] = {"proj": proj["point"], "line": ln.get("line"), "book": ln.get("book"),
                               "over": ln.get("over"), "under": ln.get("under")}
        if len(stats) < len(config.TARGET_STATS):
            continue
        if "pra" in mine:
            stats["pra"] = {"line": mine["pra"].get("line"), "book": mine["pra"].get("book"),
                            "over": mine["pra"].get("over"), "under": mine["pra"].get("under")}
        if _recent_minutes(con, r["player_id"], date_s) < MIN_MINUTES_FOR_BOARD:
            continue
        home = r["team"] == r["home_team"]
        players.append({
            "player": r["name"],
            "team": r["team"],
            "opp": r["away_team"] if home else r["home_team"],
            "home": home,
            "time_et": _time_et(r["tipoff_utc"]),
            "status": r["status"] if r["status"] not in (None, "active") else None,
            "stats": stats,
        })

    def has_line(p):
        return any(v.get("line") is not None for v in p["stats"].values())

    def pra(p):
        return sum(p["stats"][k]["proj"] for k in ("points", "rebounds", "assists"))

    players.sort(key=lambda p: (not has_line(p), -pra(p)))
    players = players[:MAX_BOARD_PLAYERS]
    players.sort(key=lambda p: -p["stats"]["points"]["proj"])
    n_lined = sum(1 for p in players if has_line(p))
    if n_lined:
        lines_note = (f"Lines from FanDuel/DraftKings, pulled once each morning "
                      f"({n_lined} of {len(players)} players have at least one line).")
    else:
        lines_note = "No sportsbook lines for this date, so projections are shown uncolored."
    _save(config.SITE_DATA_DIR / "today.json", {
        "generated_at": _now(),
        "date": date_s,
        "note": ("Projections are real: a recency-weighted average of each player's ESPN game "
                 "log (this season plus last). " + lines_note),
        "lines_fetched_at": book.get("fetched_at"),
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
    if prob is not None:
        tonight["call"] = "yes" if prob >= 0.5 else "no"
        tonight.setdefault("td_projection", {})
        tonight["td_projection"]["model_prob"] = prob
        tonight["td_projection"]["note"] = (
            f"Placeholder method: his {season_rate * 100:.1f}% season hit rate blended 60/40 with "
            "his hit rate over his last 20 games. Not a real joint model of points, rebounds and "
            "assists yet. See the methodology page."
        )
    td = load_lines(date_s).get("jokic_td") if game else None
    tonight["market"] = {"side": "yes", "odds": td["odds"], "book": td["book"]} if td else None
    data["tonight"] = tonight
    if {**data, "generated_at": None} == {**before, "generated_at": None}:
        return False  # nothing new (e.g. offseason); skip so the daily run doesn't commit noise
    data["generated_at"] = _now()
    data["note"] = ("Season summary, recent games and the model call come from Jokic's real ESPN "
                    "game log. The market price is his triple-double Yes price from the morning "
                    "odds pull, when there was room in the budget for it.")
    _save(path, data)
    return True
