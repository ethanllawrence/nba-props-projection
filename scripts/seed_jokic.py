#!/usr/bin/env python3
"""One-off seed: load Jokic's REAL 2025-26 recent-games log (already sourced
into docs/data/jokic.json) into the database, so the model/export pipeline
has real data to run against end to end.

This exists because nba_api's live endpoints are blocked from this sandbox
(see nproj/ingest/nba_stats.py's docstring) — it's a stopgap for proving the
pipeline works with real numbers for one player, not a replacement for real
backfill via nproj/ingest/nba_stats.py once that can run somewhere with
normal internet access (e.g. GitHub Actions).

Run: python -m scripts.seed_jokic
"""
import json

from nproj import config, db, util

JOKIC_ID = "203999"  # real nba_api player_id, confirmed via players.find_players_by_full_name


def main():
    with open(config.ROOT / "docs" / "data" / "jokic.json") as f:
        data = json.load(f)

    with db.session() as con:
        con.execute(
            "INSERT INTO players (player_id, name, team) VALUES (?,?,?) "
            "ON CONFLICT(player_id) DO UPDATE SET name=excluded.name, team=excluded.team",
            (JOKIC_ID, "Nikola Jokic", "DEN"),
        )

        n = 0
        for g in data["recent_games"]:
            game_id = f"seed-{g['date']}-{JOKIC_ID}"
            con.execute(
                "INSERT INTO games (game_id, date, home_team, away_team, status) "
                "VALUES (?,?,?,?,?) ON CONFLICT(game_id) DO NOTHING",
                (game_id, g["date"], "DEN" if g["opp"].startswith("vs") else g["opp"].split(" ")[-1],
                 g["opp"].split(" ")[-1] if g["opp"].startswith("vs") else "DEN", "Final"),
            )
            con.execute(
                "INSERT INTO player_game_logs "
                "(game_id, player_id, date, team, opp, minutes, points, rebounds, assists) "
                "VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(game_id, player_id) DO UPDATE SET "
                "points=excluded.points, rebounds=excluded.rebounds, assists=excluded.assists",
                (game_id, JOKIC_ID, g["date"], "DEN", g["opp"], None, g["pts"], g["reb"], g["ast"]),
            )
            n += 1

        # Tonight's game + probable-player row, using the same placeholder
        # matchup already shown in the mockup (DEN @ OKC). Uses the real
        # board-day clock (util.board_date) rather than jokic.json's stale
        # generated_at date, so it lines up with whatever date `python -m
        # nproj daily/export` actually queries for.
        today = util.iso(util.board_date())
        game_id = "seed-tonight"
        con.execute(
            "INSERT INTO games (game_id, date, home_team, away_team, status) "
            "VALUES (?,?,?,?,?) ON CONFLICT(game_id) DO UPDATE SET status=excluded.status",
            (game_id, today, "OKC", "DEN", "Scheduled"),
        )
        con.execute(
            "INSERT INTO probable_players (game_id, player_id, date, status) "
            "VALUES (?,?,?,?) ON CONFLICT(game_id, player_id) DO UPDATE SET status=excluded.status",
            (game_id, JOKIC_ID, today, "probable"),
        )

        print(f"[seed_jokic] loaded {n} real game logs for Jokic ({JOKIC_ID}), "
              f"seeded tonight's slate for {today}")


if __name__ == "__main__":
    main()
