"""Regenerate docs/data/*.json from the database.

today.json: rebuilt from scratch whenever the date has a real slate. One
row per player who is on a roster for tonight, isn't listed as out, and is
averaging at least MIN_MINUTES_FOR_BOARD over his recent games (so deep
bench players don't flood the table). On a night with no games (offseason,
All-Star break) the existing file is left alone.

Book lines: none yet. Until The Odds API is wired in (nproj/ingest/odds.py),
every stat is written with line/book = null, and the site shows the
projection without edge coloring.

jokic.json: season summary, recent games and the triple-double call are
all recomputed from his real ESPN game log. The "market" price is still
hand-typed until odds are wired in.
"""
import json
from datetime import datetime, timezone

from .. import config
from ..pipeline import JOKIC_ESPN_ID

MIN_MINUTES_FOR_BOARD = 20.0
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

    players = []
    for r in rows:
        stats = {}
        for stat in config.TARGET_STATS:
            proj = con.execute(
                "SELECT point FROM projections WHERE player_id=? AND date=? AND stat=?",
                (r["player_id"], date_s, stat),
            ).fetchone()
            if proj and proj["point"] is not None:
                stats[stat] = {"proj": proj["point"], "line": None, "book": None}
        if len(stats) < len(config.TARGET_STATS):
            continue
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

    players.sort(key=lambda p: -p["stats"]["points"]["proj"])
    _save(config.SITE_DATA_DIR / "today.json", {
        "generated_at": _now(),
        "date": date_s,
        "note": ("Projections are real: a recency-weighted average of each player's ESPN game "
                 "log (this season plus last). No sportsbook lines yet, so there's no edge "
                 "coloring until The Odds API is wired in."),
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
    data["tonight"] = tonight
    if {**data, "generated_at": None} == {**before, "generated_at": None}:
        return False  # nothing new (e.g. offseason); skip so the daily run doesn't commit noise
    data["generated_at"] = _now()
    data["note"] = ("Season summary, recent games and the model call come from Jokic's real ESPN "
                    "game log. The market price is still a placeholder until odds are wired in.")
    _save(path, data)
    return True
