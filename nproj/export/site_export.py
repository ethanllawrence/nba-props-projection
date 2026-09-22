"""Regenerate docs/data/*.json from the database.

STATUS: real for the slice of data that's actually real right now (Jokic,
seeded via scripts/seed_jokic.py — see nproj/ingest/nba_stats.py's docstring
for why the rest of the league isn't backfilled from this sandbox yet).
For every other player, this intentionally LEAVES the existing hand-written
mock numbers in docs/data/today.json alone rather than inventing projections
for players with no real game log in the database — a missing model isn't
the same thing as a zero, and overwriting good-enough mock data with a
worse guess would be a regression, not progress.

Current site schema (see docs/assets/app.js): each player has
stats.{points,rebounds,assists} = {proj, line, book}. This export only ever
touches `proj` — `line`/`book` stay whatever they already are until The
Odds API is wired in (see nproj/ingest/odds.py), since there's no live prop
line to replace them with yet.
"""
import json
from datetime import datetime, timezone

from .. import config


def _load(path):
    with open(path) as f:
        return json.load(f)


def _save(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def export_all(con, date_s: str):
    from ..model import predict

    updated_players = []
    updated_players += _export_today_board(con, date_s)
    jokic_updated = _export_jokic_call(con)

    return {"today_board_players_updated": updated_players, "jokic_call_updated": jokic_updated}


def _export_today_board(con, date_s: str):
    path = config.SITE_DATA_DIR / "today.json"
    data = _load(path)

    players_by_name = {p["name"]: p["player_id"]
                        for p in con.execute("SELECT player_id, name FROM players").fetchall()}

    updated = []
    for player in data["players"]:
        pid = players_by_name.get(player["player"])
        if not pid:
            continue  # no real data for this player yet — leave their mock row alone

        changed_any = False
        for stat in config.TARGET_STATS:
            row = con.execute(
                "SELECT point FROM projections WHERE player_id=? AND date=? AND stat=?",
                (pid, date_s, stat),
            ).fetchone()
            if not row or row["point"] is None:
                continue
            if stat in player["stats"]:
                player["stats"][stat]["proj"] = row["point"]
                changed_any = True
        if changed_any:
            updated.append(player["player"])

    if updated:
        data["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        data["note"] = (
            "MOCKUP DATA, partially real. " + ", ".join(updated) + "'s points/rebounds/assists "
            "projections are now real model output (recency-weighted rolling average of real "
            "game logs — see nproj/model/predict.py), not hand-typed. Everyone else's "
            "projections, and all book lines for everyone including " + ", ".join(updated) +
            ", are still illustrative — no real game log backfilled for other players yet, and "
            "no live odds feed wired up yet. PRA is the sum of the three lines."
        )
        _save(path, data)
    return updated


def _export_jokic_call(con):
    from ..model import predict

    path = config.SITE_DATA_DIR / "jokic.json"
    data = _load(path)
    jokic_id = next(
        (p["player_id"] for p in con.execute("SELECT player_id, name FROM players").fetchall()
         if p["name"] == "Nikola Jokic"),
        None,
    )
    if not jokic_id:
        return False

    season_hit_rate = data["season_summary"]["hit_rate"]
    prob = predict.project_triple_double_prob(con, jokic_id, season_hit_rate=season_hit_rate)
    if prob is None:
        return False

    data["tonight"]["td_projection"]["model_prob"] = prob
    data["tonight"]["td_projection"]["note"] = (
        f"Real, but a placeholder method: {season_hit_rate * 100:.1f}% season hit rate blended "
        "60/40 with the empirical hit rate over games actually in the database, NOT a real joint "
        "probability model across points/rebounds/assists. See planning doc / methodology page."
    )
    data["tonight"]["call"] = "yes" if prob >= 0.5 else "no"
    _save(path, data)
    return True
