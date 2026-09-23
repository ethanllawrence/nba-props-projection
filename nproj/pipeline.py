"""Glue between ESPN ingest and the SQLite database.

The daily run is stateless on purpose: GitHub Actions starts with an empty
database every time, so refresh_slate() pulls everything tonight's board
needs (schedule, rosters, and each rostered player's game log for this
season and last season) in one pass. That's roughly 300 small requests for
a 10-game night, about a minute of wall time.
"""
import time
from datetime import date

from .ingest import espn

JOKIC_ESPN_ID = "3112335"
REQUEST_PAUSE = 0.15  # seconds between game-log calls; be polite to ESPN


def upsert_game(con, g):
    con.execute(
        """INSERT INTO games (game_id, date, home_team, away_team, status, tipoff_utc)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(game_id) DO UPDATE SET status=excluded.status, tipoff_utc=excluded.tipoff_utc""",
        (g["game_id"], g["date"], g["home_team"], g["away_team"], g["status"], g["tipoff_utc"]),
    )


def upsert_player(con, p):
    con.execute(
        """INSERT INTO players (player_id, name, team, status) VALUES (?,?,?,?)
           ON CONFLICT(player_id) DO UPDATE SET name=excluded.name, team=excluded.team,
             status=excluded.status""",
        (p["player_id"], p["name"], p["team"], p["status"]),
    )


def upsert_logs(con, rows, season):
    for r in rows:
        con.execute(
            """INSERT INTO player_game_logs
               (game_id, player_id, date, team, opp, minutes, points, rebounds, assists, threes,
                season, playoff)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(game_id, player_id) DO UPDATE SET
                 minutes=excluded.minutes, points=excluded.points, rebounds=excluded.rebounds,
                 assists=excluded.assists, threes=excluded.threes""",
            (r["game_id"], r["player_id"], r["date"], r["team"], r["opp"], r["minutes"],
             r["points"], r["rebounds"], r["assists"], r["threes"], season, int(r["playoff"])),
        )
    return len(rows)


def load_player_logs(con, player_id, on_date: date, seasons=2):
    """This season's and last season's games for one player. Last season
    matters most in October/November, when a player has only a handful of
    games; the model's recency weighting phases it out as the year goes on.
    seasons=1 fetches only the current season (enough for grading a night)."""
    season = espn.season_year(on_date)
    n = 0
    for s in (season, season - 1)[:seasons]:
        try:
            n += upsert_logs(con, espn.fetch_player_game_log(player_id, s), s)
        except RuntimeError as exc:
            print(f"  [warn] game log {player_id} season {s}: {exc}")
        time.sleep(REQUEST_PAUSE)
    return n


def refresh_slate(con, date_s: str):
    """Schedule + rosters + game logs for every team playing on date_s.
    Returns a short summary dict for the run log."""
    on_date = date.fromisoformat(date_s)
    games = espn.fetch_schedule(date_s)
    for g in games:
        upsert_game(con, g)
    if not games:
        return {"games": 0, "players": 0, "logs": 0}

    team_ids = {}
    for g in games:
        team_ids[g["home_team_id"]] = g["game_id"]
        team_ids[g["away_team_id"]] = g["game_id"]

    players = logs = 0
    for team_id, game_id in team_ids.items():
        for p in espn.fetch_roster(team_id):
            upsert_player(con, p)
            con.execute(
                """INSERT INTO probable_players (game_id, player_id, date, status) VALUES (?,?,?,?)
                   ON CONFLICT(game_id, player_id) DO UPDATE SET status=excluded.status""",
                (game_id, p["player_id"], date_s, p["status"]),
            )
            players += 1
            if p["status"] != "out":
                logs += load_player_logs(con, p["player_id"], on_date)
    return {"games": len(games), "players": players, "logs": logs}


def refresh_jokic(con, date_s: str):
    """The Jokic tab needs his log every day, even when Denver is off."""
    exists = con.execute("SELECT 1 FROM players WHERE player_id=?", (JOKIC_ESPN_ID,)).fetchone()
    if not exists:
        upsert_player(con, {"player_id": JOKIC_ESPN_ID, "name": "Nikola Jokic", "team": "DEN",
                            "status": "active"})
        return load_player_logs(con, JOKIC_ESPN_ID, date.fromisoformat(date_s))
    return 0


def backfill(con, season: int):
    """Every rostered player's game log for one ESPN season year. Not needed
    for the daily board; useful for building a real training set later."""
    n_players = n_logs = 0
    for t in espn.fetch_teams():
        for p in espn.fetch_roster(t["team_id"]):
            upsert_player(con, p)
            try:
                n_logs += upsert_logs(con, espn.fetch_player_game_log(p["player_id"], season), season)
            except RuntimeError as exc:
                print(f"  [warn] {p['name']}: {exc}")
            n_players += 1
            time.sleep(REQUEST_PAUSE)
        print(f"  {t['abbr']}: done ({n_players} players so far)")
    return {"players": n_players, "logs": n_logs}
