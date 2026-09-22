"""Offline end-to-end test: fake ESPN responses (same JSON shape as the real
feeds, checked 2026-09-22) run through ingest -> model -> export.
Run: python -m pytest tests/  (or python tests/test_pipeline_offline.py)"""
import json
import random
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NAMES = ["minutes", "fieldGoalsMade-fieldGoalsAttempted", "fieldGoalPct",
         "threePointFieldGoalsMade-threePointFieldGoalsAttempted", "threePointPct",
         "freeThrowsMade-freeThrowsAttempted", "freeThrowPct", "totalRebounds", "assists",
         "blocks", "steals", "fouls", "turnovers", "points"]
TEAMS = {"7": "DEN", "25": "OKC", "20": "PHI", "29": "MEM"}


def fake_gamelog(pid, season):
    rnd = random.Random(f"{pid}-{season}")
    base_min = 34 if pid in ("3112335", "p25_0", "p20_0") else rnd.choice([12, 26, 31])
    events, reg = {}, []
    for i in range(30):
        eid = f"{season}{pid}{i}"
        month = 11 + i // 10  # Nov..Jan of the season's first year
        yr = season - 1 if month <= 12 else season
        mo = month if month <= 12 else month - 12
        events[eid] = {"id": eid, "atVs": "@" if i % 2 else "vs",
                       "gameDate": f"{yr}-{mo:02d}-{(i % 10) * 2 + 2:02d}T01:00:00.000+00:00",
                       "opponent": {"id": "9", "abbreviation": "GS"},
                       "team": {"id": "7", "abbreviation": "DEN"}}
        pts, reb, ast = rnd.randint(8, 35), rnd.randint(2, 16), rnd.randint(1, 13)
        reg.append({"eventId": eid, "stats": [str(base_min), "9-17", "50", "2-5", "40", "3-4", "75",
                                             str(reb), str(ast), "1", "1", "2", "3", str(pts)]})
    # All-Star game filed under the regular season (must be dropped)
    events["as1"] = {"id": "as1", "atVs": "@", "gameDate": f"{season}-02-15T22:00:00.000+00:00",
                     "eventNote": "NBA All-Star - Round Robin",
                     "opponent": {"id": "132374", "abbreviation": "STARS"},
                     "team": {"id": "111386", "abbreviation": "WORLD"}}
    reg.append({"eventId": "as1", "stats": ["5", "0-1", "0", "0-0", "0", "0-0", "0", "2", "0", "0",
                                            "0", "0", "0", "0"]})
    events["pre1"] = {"id": "pre1", "atVs": "vs", "gameDate": f"{season - 1}-10-05T01:00:00.000+00:00",
                      "opponent": {"id": "16", "abbreviation": "MIN"}, "team": {"id": "7", "abbreviation": "DEN"}}
    return {"names": NAMES, "events": events, "seasonTypes": [
        {"displayName": f"{season - 1}-{str(season)[-2:]} Regular Season",
         "categories": [{"type": "event", "events": reg}, {"type": "total"}]},
        {"displayName": f"{season - 1}-{str(season)[-2:]} Preseason",
         "categories": [{"type": "event", "events": [{"eventId": "pre1", "stats": ["20"] * 14}]}]},
    ]}


def fake_get(path, params=None, retries=3):
    if path.endswith("/scoreboard"):
        return {"events": [
            {"id": "g1", "date": "2026-03-11T02:00Z", "season": {"type": 2},
             "status": {"type": {"description": "Scheduled"}},
             "competitions": [{"competitors": [
                 {"homeAway": "home", "team": {"id": "7", "abbreviation": "DEN"}},
                 {"homeAway": "away", "team": {"id": "25", "abbreviation": "OKC"}}]}]},
            {"id": "g2", "date": "2026-03-10T23:00Z", "season": {"type": 2},
             "status": {"type": {"description": "Scheduled"}},
             "competitions": [{"competitors": [
                 {"homeAway": "home", "team": {"id": "20", "abbreviation": "PHI"}},
                 {"homeAway": "away", "team": {"id": "29", "abbreviation": "MEM"}}]}]},
            {"id": "pre", "date": "2026-03-10T23:00Z", "season": {"type": 1}, "competitions": []},
        ]}
    if "/roster" in path:
        tid = path.split("/teams/")[1].split("/")[0]
        ath = [{"id": "3112335", "displayName": "Nikola Jokic", "position": {"abbreviation": "C"},
                "injuries": [], "status": {"type": "active"}}] if tid == "7" else []
        ath += [{"id": f"p{tid}_{i}", "displayName": f"Player {tid}-{i}", "position": {"abbreviation": "G"},
                 "injuries": [{"status": "Out"}] if i == 2 else [], "status": {"type": "active"}}
                for i in range(4)]
        return {"team": {"abbreviation": TEAMS[tid]}, "athletes": ath}
    if "/gamelog" in path:
        pid = path.split("/athletes/")[1].split("/")[0]
        return fake_gamelog(pid, int(params["season"]))
    raise AssertionError(path)


def test_daily_offline():
    tmp = Path(tempfile.mkdtemp())
    site = tmp / "site"
    shutil.copytree(ROOT / "docs" / "data", site)
    import os
    os.environ["NPROJ_DB"] = str(tmp / "t.db")
    os.environ["NPROJ_DATA_DIR"] = str(tmp)
    os.environ["NPROJ_SITE_DATA"] = str(site)
    import importlib
    from nproj import config
    importlib.reload(config)
    from nproj import cli, pipeline
    from nproj.ingest import espn
    espn._get = fake_get
    pipeline.REQUEST_PAUSE = 0

    # All-Star and preseason filtered; ET dating; abbreviation fix
    rows = espn.fetch_player_game_log("3112335", 2026)
    assert len(rows) == 30 and all(r["team"] == "DEN" for r in rows)
    assert all(r["opp"].endswith("GSW") for r in rows)
    sched = espn.fetch_schedule("2026-03-10")
    assert [g["game_id"] for g in sched] == ["g1", "g2"]
    assert sched[0]["date"] == "2026-03-10" and sched[0]["time_et"] == "10:00 PM"  # EDT

    cli.main(["daily", "--date", "2026-03-10"])

    today = json.loads((site / "today.json").read_text())
    names = [p["player"] for p in today["players"]]
    assert today["date"] == "2026-03-10"
    assert "Nikola Jokic" in names
    assert not any(n.endswith("-2") for n in names), "players listed as out must be excluded"
    for p in today["players"]:
        assert set(p["stats"]) == {"points", "rebounds", "assists"}
        assert all(s["line"] is None for s in p["stats"].values())
    jok = next(p for p in today["players"] if p["player"] == "Nikola Jokic")
    assert jok["home"] is True and jok["opp"] == "OKC"

    jk = json.loads((site / "jokic.json").read_text())
    assert jk["tonight"]["opp"] == "vs OKC"
    assert len(jk["recent_games"]) == 10
    assert jk["recent_games"][0]["date"] < "2026-03-10"
    assert 0 <= jk["tonight"]["td_projection"]["model_prob"] <= 1
    print("OK:", len(names), "players on board;", jk["season_summary"])


if __name__ == "__main__":
    test_daily_offline()
