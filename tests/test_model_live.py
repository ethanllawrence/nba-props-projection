"""The PRA model end to end on real history: rebuild the 2026-03-10 slate
from history/ (as if it were tonight), train on everything before it, and
check the projections, probabilities and the board export. Uses a small
tree count so it runs in about a minute."""
import json

import numpy as np
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATE = "2026-03-10"
os.environ["NPROJ_ESPN_PROPS"] = "0"     # never call ESPN's real props feed from tests


def test_live_model():
    hist = ROOT / "history"
    if not (hist / "2026" / "box.csv.gz").exists():
        print("SKIP live model test: no history/ folder")
        return
    tmp = Path(tempfile.mkdtemp())
    site = tmp / "site"
    shutil.copytree(ROOT / "tests" / "fixtures" / "site_data", site)
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
        # triple-double chance: drawing points/rebounds/assists together
        # (positive correlation) must beat multiplying them separately
        tdp = max(live.PROJ, key=lambda p: min(live.PROJ[p]["rebounds"], live.PROJ[p]["assists"]))
        p_joint = live.td_probability(tdp)
        p_indep = live.td_probability(tdp, corr=[[1, 0, 0], [0, 1, 0], [0, 0, 1]])
        assert 0 < p_indep < p_joint < 1, (p_indep, p_joint)
        # early-look parlay: real alt prices + model veto/ranking, about +200 real odds
        from nproj.model import early_parlay as EP
        from nproj.ingest.odds import norm_name
        assert EP.pick(con, DATE, live.PROJ, {})["status"] == "waiting"
        names = dict(con.execute("SELECT player_id, name FROM players").fetchall())
        stars = sorted(live.PROJ, key=lambda p: -live.PROJ[p]["pra"])[:10]
        alts = {}
        for i, pid in enumerate(stars):
            mu = live.PROJ[pid]["pra"]
            ladder = [{"line": round(mu) - 8.5, "over": -400, "book": "FanDuel"},
                      {"line": round(mu) - 5.5, "over": -260, "book": "FanDuel"},
                      {"line": round(mu) - 2.5, "over": -160, "book": "FanDuel"}]
            if i == 0:      # a trap: the book is far surer than the model -> vetoed
                ladder = [{"line": round(mu) - 1.5, "over": -300, "book": "FanDuel"}]
            alts[norm_name(names[pid])] = {"pra": ladder}
        ep = EP.pick(con, DATE, live.PROJ, alts)
        legs = ep["legs"]
        assert ep["status"] == "pending" and EP.MIN_LEGS <= len(legs) <= EP.MAX_LEGS, ep
        assert stars[0] not in {l["player_id"] for l in legs}, "trap leg must be vetoed"
        assert all(EP.LEG_ODDS[0] <= l["odds"] <= EP.LEG_ODDS[1] and l["gap"] >= EP.VETO
                   and l["proj"] > l["line"] and l["side"] == "over" for l in legs)
        assert EP.MIN_ACCEPT <= ep["combined_odds"] <= EP.TARGET_HI
        assert ep["combined_odds"] == EP.combined_odds([l["odds"] for l in legs])
        assert len({l["player_id"] for l in legs}) == len(legs)
        assert EP.combo_ok(legs)
        assert [l["gap"] for l in legs] == sorted((l["gap"] for l in legs), reverse=True)
        # the chosen combination has the largest total CAPPED gap among in-range combinations
        from itertools import combinations
        cands = EP.candidates(con, DATE, live.PROJ, alts)
        capped = lambda k: round(sum(min(c["gap"], EP.GAP_CAP) for c in k), 4)
        best = max((capped(k) for n in (3, 4) for k in combinations(cands[:EP.SEARCH_TOP], n)
                    if EP.combo_ok(k)
                    and EP.TARGET_LO <= EP.combined_odds([c["odds"] for c in k]) <= EP.TARGET_HI),
                   default=None)
        assert best is None or abs(capped(legs) - best) < 1e-9
        print("OK early parlay:", ep["combined_odds"], [(l["player"], l["line"], l["odds"], l["gap"]) for l in legs])
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
        pool = parlay.candidates(con, DATE, load_lines(DATE), qualify=False)
        # the pool alt lines are bought for: every over with a line, model-driven
        assert pool and all(c["model"] and c["side"] == "over" for c in pool)
        # qualified main-line legs: overs only, 60%+, projection above the line
        assert all(c["side"] == "over" and c["prob"] >= parlay.MODEL_MIN_PROB_MAIN
                   and c["proj"] > c["line"] and c["odds"] >= parlay.MIN_LEG_ODDS for c in cands)

        # Parlay of the Day, price-first: the same engine on today's prices
        (site / "parlay.json").write_text(json.dumps({"history": []}))
        (site / "early_lines.json").unlink(missing_ok=True)
        (site / "lines.json").write_text(json.dumps(
            {"date": DATE, "lines": {norm_name(k): v for k, v in lines.items()}, "alts": alts}))
        con.execute("UPDATE games SET tipoff_utc='2030-01-01T00:00Z'")
        log_p = parlay.update(con, DATE)
        pj = json.loads((site / "parlay.json").read_text())["today"]
        assert pj["method"] == "price-first" and 3 <= len(pj["legs"]) <= 4, (log_p, pj)
        assert all(l["side"] == "over" and l["book_prob"] and "gap" in l and l["reasoning"] for l in pj["legs"])
        assert parlay.update(con, DATE).get("locked") == DATE            # a later run doesn't change it
        # manual top-up re-pick: the first leg's game has started -> re-picked without it
        gone = next(l["game_id"] for l in pj["legs"])
        con.execute("UPDATE games SET tipoff_utc='2020-01-01T00:00Z' WHERE game_id=?", (gone,))
        log_r = parlay.update(con, DATE, repick=True)
        pr = json.loads((site / "parlay.json").read_text())["today"]
        if log_r.get("repicked"):
            assert all(l["game_id"] != gone for l in pr["legs"]) and pr["replaced"]["legs"], pr
        else:
            assert pr["legs"] == pj["legs"]                              # nothing found: morning pick stays
        # night pick (ESPN lines only, no paid lines yet), then the 6 AM check
        from nproj.ingest import espn_props
        (site / "parlay.json").write_text(json.dumps({"history": []}))
        (site / "lines.json").unlink(missing_ok=True)
        con.execute("UPDATE games SET tipoff_utc='2030-01-01T00:00Z'")
        star_games = {r["game_id"] for r in con.execute(
            f"SELECT game_id FROM probable_players WHERE date=? AND player_id IN ({','.join('?' * len(stars))})",
            (DATE, *stars))}
        games_cov = {}
        for g in con.execute("SELECT game_id, home_team, away_team FROM games WHERE date=?", (DATE,)):
            if g["game_id"] in star_games:
                games_cov[f"{g['away_team']} @ {g['home_team']}"] = {
                    "home": g["home_team"], "away": g["away_team"], "lines": 1, "ladders": 1,
                    "line_stats": ["points"], "ladder_stats": ["pra"]}
        espn_props.save({"date": DATE, "fetched_at": "x", "games": games_cov,
                         "lines": {norm_name(k): v for k, v in lines.items()}, "alts": alts})
        lg_n = parlay.update(con, DATE, mode="night")
        pn = json.loads((site / "parlay.json").read_text())["today"]
        assert pn["method"] == "night" and 3 <= len(pn["legs"]) <= 4, (lg_n, pn)
        assert EP.combo_ok([{**l, "gap": l["gap"]} for l in pn["legs"]])
        assert len(pn["missing_games"]) == len(set(r["game_id"] for r in con.execute(
            "SELECT game_id FROM games WHERE date=?", (DATE,)))) - len(games_cov)
        assert parlay.update(con, DATE, mode="night")["night"]["kept"] == DATE      # 11:30 PM: same games, kept
        # 6 AM: one leg's player is ruled out overnight -> that leg (only) is swapped
        gone = pn["legs"][0]
        con.execute("UPDATE probable_players SET status='out' WHERE player_id=? AND date=?",
                    (gone["player_id"], DATE))
        (site / "lines.json").write_text(json.dumps(
            {"date": DATE, "lines": {norm_name(k): v for k, v in lines.items()}, "alts": {}}))
        # ... and another leg's price only got SHORTER past -400 overnight: it must stay
        short = pn["legs"][1]
        e2 = espn_props.load(DATE)
        for step in e2["alts"][norm_name(short["player"])][short["stat"]]:
            if step["line"] == short["line"]:
                step["over"] = -520
        espn_props.save(e2)
        lg_m = parlay.update(con, DATE, mode="morning")
        pm = json.loads((site / "parlay.json").read_text())
        pm = pm.get("today") or {}
        if pm.get("legs"):
            assert pm["morning_checked"] and pm["changes"][0]["out"]["player"] == gone["player"], (lg_m, pm)
            assert gone["player_id"] not in {l["player_id"] for l in pm["legs"]}
            kept_short = [l for l in pm["legs"] if l["player_id"] == short["player_id"]]
            assert kept_short and kept_short[0]["odds"] == -520, (pm["legs"], pm["changes"])
            assert all(c["out"]["player"] != short["player"] for c in pm["changes"])
            kept = {l["player_id"] for l in pn["legs"][1:]}
            assert kept <= {l["player_id"] for l in pm["legs"]} or pm["changes"], pm
        assert parlay.update(con, DATE, mode="morning").get("morning_check") is None   # checked once
        con.execute("UPDATE probable_players SET status='active' WHERE player_id=? AND date=?",
                    (gone["player_id"], DATE))
        print("OK night pick + morning check:", pn["combined_odds"], [l["player"] for l in pn["legs"]],
              "->", pm.get("combined_odds"), [c.get("in", {}).get("player") for c in pm.get("changes", [])])
        print("OK price-first parlay:", pj["combined_odds"], [(l["player"], l["line"], l["odds"]) for l in pj["legs"]],
              "| repick:", log_r.get("repicked") or log_r.get("repick"))
    board = json.loads((site / "today.json").read_text())
    assert board["model"] == "lightgbm" and n > 20
    lined = [p for p in board["players"] if p["stats"]["points"].get("line") is not None]
    assert len(lined) == 6, len(lined)
    assert all("p_over" in p["stats"]["points"] for p in lined)
    calls = [p["stats"]["points"]["call"] for p in lined]
    assert "under" in calls, calls
    # far below the projection: an over call, or no call when 1.5x+ (live.FAR_RATIO)
    for p in lined:
        st = p["stats"]["points"]
        if st["proj"] - st["line"] > 8:
            assert st["call"] == "over" or (st["call"] is None and st.get("flag") == "far"
                                             and st["proj"] / st["line"] >= 1.5), st
    assert all(p["stats"]["pra"]["proj"] > p["stats"]["points"]["proj"] for p in board["players"])
    print(f"OK live model: {log['projected_players']} players, points MAE {mae:.2f}, calls {calls}")


if __name__ == "__main__":
    test_live_model()
