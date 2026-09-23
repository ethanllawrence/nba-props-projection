"""The PRA model end to end on real history: rebuild the 2026-03-10 slate
from history/ (as if it were tonight), train on everything before it, and
check the projections, probabilities and the board export. Uses a small
tree count so it runs in about a minute."""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATE = "2026-03-10"


def test_live_model():
    hist = ROOT / "history"
    if not (hist / "2026" / "box.csv.gz").exists():
        print("SKIP live model test: no history/ folder")
        return
    tmp = Path(tempfile.mkdtemp())
    site = tmp / "site"
    shutil.copytree(ROOT / "docs" / "data", site)
    os.environ.update(NPROJ_DB=str(tmp / "t.db"), NPROJ_DATA_DIR=str(tmp), NPROJ_SITE_DATA=str(site))
    import importlib
    from nproj import config
    importlib.reload(config)
    from nproj import db
    from nproj.model import features as F, live, pra_model
    pra_model.PARAMS["n_estimators"] = 80
    importlib.reload(live)

    box, games, _ = F.load_history(hist)
    b, g = box[box.date == DATE], games[games.date == DATE]
    with db.session() as con:
        for i, (_, r) in enumerate(g.iterrows()):
            # past games come back from ESPN with no line (and a slate can be
            # built before lines post): every other game has none here
            sp, tot = (None, None) if i % 2 == 0 else (r.spread_home, r.total)
            con.execute("INSERT INTO games (game_id,date,home_team,away_team,status,spread_home,total,"
                        "tipoff_utc) VALUES (?,?,?,?,?,?,?,?)",
                        (r.game_id, DATE, r.home, r.away, "Scheduled", sp, tot,
                         "2026-03-11T00:00Z"))
        for _, r in b.iterrows():
            st = "out" if r.dnp == 1 and "COACH" not in str(r.dnp_reason) else "active"
            con.execute("INSERT OR REPLACE INTO players (player_id,name,team,status) VALUES (?,?,?,?)",
                        (r.player_id, r.player, r.team, st))
            con.execute("INSERT OR REPLACE INTO probable_players (game_id,player_id,date,status) "
                        "VALUES (?,?,?,?)", (r.game_id, r.player_id, DATE, st))
        log = live.run(con, DATE, history_root=hist)
        assert log["projected_players"] > 150, log
        actual = b[b.dnp == 0].set_index("player_id")
        errs = [abs(live.PROJ[p]["points"] - actual.loc[p, "points"]) for p in actual.index
                if p in live.PROJ]
        mae = sum(errs) / len(errs)
        assert 3.0 < mae < 6.5, mae                       # sane, not leaking the answer
        # a missing line once shrank everyone to ~17 pts: the top scorer
        # on this slate (Luka) must still project like a star
        assert max(v["points"] for v in live.PROJ.values()) > 25, "projections shrunk"
        top = sorted(live.PROJ, key=lambda p: -live.PROJ[p]["points"])[:6]
        # lines: two below the projection, two above, two right at it
        lines = {}
        for i, pid in enumerate(top):
            mu = live.PROJ[pid]["points"]
            ln = round(mu) + (-9.5 if i < 2 else 9.5 if i < 4 else 0.5)
            name = actual.loc[pid, "player"]
            lines[name.lower()] = {"points": {"line": ln, "over": -110, "under": -110, "book": "FanDuel"}}
            v = live.assess(pid, "points", ln, -110, -110)
            assert 0 < v["p_over"] < 1
            if i < 2:
                assert v["p_over"] > 0.5
            if 2 <= i < 4:
                assert v["p_over"] < 0.5
        from nproj.ingest.odds import norm_name
        (site / "lines.json").write_text(json.dumps(
            {"date": DATE, "lines": {norm_name(k): v for k, v in lines.items()}}))
        from nproj.export.site_export import _export_today_board
        n = _export_today_board(con, DATE)
        from nproj.ingest.odds import load_lines
        from nproj.model import parlay
        cands = parlay.candidates(con, DATE, load_lines(DATE))
        assert cands and all(c["model"] for c in cands)
        assert all(c["prob"] >= parlay.MODEL_MIN_PROB_MAIN for c in cands)
    board = json.loads((site / "today.json").read_text())
    assert board["model"] == "lightgbm" and n > 20
    lined = [p for p in board["players"] if p["stats"]["points"].get("line") is not None]
    assert len(lined) == 6, len(lined)
    assert all("p_over" in p["stats"]["points"] for p in lined)
    calls = [p["stats"]["points"]["call"] for p in lined]
    assert "over" in calls and "under" in calls, calls
    assert all(p["stats"]["pra"]["proj"] > p["stats"]["points"]["proj"] for p in board["players"])
    print(f"OK live model: {log['projected_players']} players, points MAE {mae:.2f}, calls {calls}")


if __name__ == "__main__":
    test_live_model()
