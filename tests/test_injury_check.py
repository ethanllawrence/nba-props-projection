"""Injury check: morning checkpoint, afternoon comparison, board and parlay
flags, the 'is a game tipping soon' gate, and the parlay lock."""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATE = "2026-11-10"


def _setup():
    tmp = Path(tempfile.mkdtemp())
    site = tmp / "site"
    site.mkdir()
    os.environ.update(NPROJ_DB=str(tmp / "t.db"), NPROJ_DATA_DIR=str(tmp), NPROJ_SITE_DATA=str(site))
    import importlib
    from nproj import config, db
    importlib.reload(config)
    importlib.reload(db)
    from nproj import injury_check
    importlib.reload(injury_check)
    return tmp, site, db, injury_check


def _board(proj):
    """today.json as the export writes it: Star + Teammate on DEN, Rival on OKC."""
    def stats(pts, reb, ast, call=None):
        s = {"points": {"proj": pts, "line": 25.5, "call": call},
             "rebounds": {"proj": reb, "line": None}, "assists": {"proj": ast, "line": None},
             "pra": {"proj": round(pts + reb + ast, 1), "line": None}}
        return s
    players = [{"player": n, "player_id": pid, "team": t, "minutes": 30.0, "stats": stats(*v)}
               for (pid, n, t), v in proj.items()]
    return {"date": DATE, "model": "lightgbm", "players": players}


def test_injury_check_flow():
    tmp, site, db, ic = _setup()
    with db.session() as con:
        con.execute("INSERT INTO games (game_id,date,home_team,away_team,status,tipoff_utc) "
                    "VALUES ('g1',?,'OKC','DEN','Scheduled','2026-11-11T01:00Z')", (DATE,))
        for pid, name, team in (("1", "Star", "DEN"), ("2", "Teammate", "DEN"), ("3", "Rival", "OKC"),
                                ("4", "Deep Bench", "DEN")):
            con.execute("INSERT INTO players (player_id,name,team,status) VALUES (?,?,?,?)",
                        (pid, name, team, "active"))
            con.execute("INSERT INTO probable_players (game_id,player_id,date,status) VALUES ('g1',?,?,?)",
                        (pid, DATE, "active"))
        morning = {("1", "Star", "DEN"): (28.0, 12.0, 9.0, None),
                   ("2", "Teammate", "DEN"): (15.0, 5.0, 3.0, None),
                   ("3", "Rival", "OKC"): (30.0, 5.0, 6.0, "over")}
        (site / "today.json").write_text(json.dumps(_board(morning)))
        legs = [{"player": "Teammate", "player_id": "2", "stat": "points", "side": "under", "line": 16.5},
                {"player": "Rival", "player_id": "3", "stat": "points", "side": "over", "line": 25.5}]
        (site / "parlay.json").write_text(json.dumps({"today": {"date": DATE, "legs": legs,
                                                                "status": "pending"}}))
        log = ic.update(con, DATE, morning=True)
        assert log["checkpoint"] == "this morning's board", log
        cp = json.loads((site / "checkpoint.json").read_text())
        assert cp["players"]["1"]["proj"]["points"] == 28.0 and "proj" not in cp["players"]["4"]

        # afternoon: Star ruled out, Teammate's projection jumps, Rival's edge
        # disappears, a deep-bench player's status flips (noise, ignored)
        con.execute("UPDATE probable_players SET status='out' WHERE player_id IN ('1','4')")
        afternoon = {("2", "Teammate", "DEN"): (19.5, 6.5, 4.5, None),
                     ("3", "Rival", "OKC"): (29.8, 5.0, 6.0, None)}
        (site / "today.json").write_text(json.dumps(_board(afternoon)))
        log = ic.update(con, DATE, now=datetime(2026, 11, 11, 0, 5, tzinfo=timezone.utc))
        assert log["status_changes"] == 1 and log["moves"] == 2, log

    board = json.loads((site / "today.json").read_text())
    chk = board["injury_check"]
    assert [c["player"] for c in chk["status_changes"]] == ["Star"]
    assert chk["status_changes"][0]["to"] == "out"
    assert chk["moves"][0]["player"] == "Teammate" and chk["moves"][0]["delta"]["pra"] == 7.5
    assert chk["moves"][1]["call_flips"]["points"] == {"from": "over", "to": None}
    tm = next(p for p in board["players"] if p["player"] == "Teammate")
    assert tm["since"]["delta"]["points"] == 4.5

    parlay = json.loads((site / "parlay.json").read_text())
    l0, l1 = parlay["today"]["legs"]
    assert l0["check"]["against"] is True and l0["check"]["proj_to"] == 19.5   # under leg, proj went up
    assert "check" not in l1                                                  # -0.2 pts: not a real move
    assert [l["player"] for l in parlay["today"]["legs"]] == ["Teammate", "Rival"]  # legs unchanged
    shutil.rmtree(tmp, ignore_errors=True)
    print("OK injury check flow")


def test_gate():
    from nproj import injury_check as ic
    now = datetime(2026, 11, 11, 0, 0, tzinfo=timezone.utc)
    games = [{"home_team": "OKC", "away_team": "DEN", "tipoff_utc": "2026-11-11T01:00Z"}]
    assert ic.due(games, now=now)[0]                                   # tips in 60 min
    assert not ic.due(games, now=datetime(2026, 11, 10, 22, 0, tzinfo=timezone.utc))[0]  # 3 h away
    assert not ic.due(games, now=datetime(2026, 11, 11, 0, 55, tzinfo=timezone.utc))[0]  # 5 min: too late
    assert not ic.due(games, last_check="2026-11-10T23:50:00Z", now=now)[0]              # just checked
    assert not ic.due([], now=now)[0]
    print("OK injury check gate")


if __name__ == "__main__":
    test_gate()
    test_injury_check_flow()
