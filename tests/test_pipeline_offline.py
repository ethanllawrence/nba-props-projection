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
import os  # noqa: E402
# these tests cover the plumbing with fake ESPN data: keep the recent-average
# projections and don't touch history/ (tests/test_model_live.py covers the model)
os.environ.setdefault("NPROJ_MODEL", "baseline")
os.environ.setdefault("NPROJ_BOX_STORE", "0")

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
        assert set(p["stats"]) == {"points", "rebounds", "assists", "pra"}
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


# ---------------------------------------------------------------- odds ----
class FakeResp:
    def __init__(self, body, remaining):
        self._body, self.status_code = body, 200
        self.headers = {"x-requests-remaining": str(remaining), "x-requests-used": "100"}
        self.text = ""

    def json(self):
        return self._body


def fake_odds_api(start_remaining=440):
    state = {"remaining": start_remaining, "calls": []}

    def get(url, params=None, timeout=None):
        state["calls"].append((url, dict(params or {})))
        if url.endswith("/sports"):
            return FakeResp([], state["remaining"])
        if url.endswith("/events"):
            return FakeResp([
                {"id": "e1", "home_team": "Denver Nuggets", "away_team": "Oklahoma City Thunder",
                 "commence_time": "2026-03-11T02:00:00Z"},
                {"id": "e2", "home_team": "Philadelphia 76ers", "away_team": "Memphis Grizzlies",
                 "commence_time": "2026-03-10T23:00:00Z"},
                {"id": "other-day", "home_team": "Utah Jazz", "away_team": "LA Clippers",
                 "commence_time": "2026-03-12T02:00:00Z"},
            ], state["remaining"])
        markets = params["markets"].split(",")
        state["remaining"] -= len(markets)
        eid = url.split("/events/")[1].split("/")[0]
        who = "Nikola Jokić" if eid == "e1" else "Player 20-0"
        mk = []
        for m in markets:
            if m == "player_triple_double":
                mk.append({"key": m, "outcomes": [{"name": "Yes", "description": who, "price": 120}]})
            else:
                base = {"player_points_rebounds_assists": 49.5, "player_points": 26.5}.get(m, 9.5)
                mk.append({"key": m, "outcomes": [
                    {"name": "Over", "description": who, "price": -115, "point": base},
                    {"name": "Under", "description": who, "price": -105, "point": base}]})
        # DraftKings listed first but FanDuel preferred: parser must pick FanDuel
        dk = {"key": "draftkings", "title": "DraftKings",
              "markets": [{"key": markets[0], "outcomes": [
                  {"name": "Over", "description": who, "price": -110, "point": 99.5}]}]}
        return FakeResp({"bookmakers": [dk, {"key": "fanduel", "title": "FanDuel", "markets": mk}]},
                        state["remaining"])
    return get, state


def test_odds_rationing_and_board():
    from datetime import date
    from nproj import config
    from nproj.ingest import odds

    # allowance math: (440 - 60) * 0.5 / 22 days = 8
    assert odds.days_until_reset(date(2026, 3, 10)) == 22
    assert odds.daily_allowance(440, date(2026, 3, 10)) == int(0.5 * 380 / 22)
    assert odds.plan(2, 9, ["a", "b", "c", "d"]) == (["a", "b", "c", "d"], 2)
    assert odds.plan(8, 9, ["a", "b"]) == (["a"], 8)
    assert odds.plan(12, 9, ["a", "b"]) == (["a"], 9)       # partial: first 9 games only
    assert odds.plan(5, 0, ["a"]) == (["a"], 0)
    assert odds.norm_name("Jaren Jackson Jr.") == "jaren jackson"
    assert odds.norm_name("Nikola Jokić") == "nikola jokic"

    tmp = Path(tempfile.mkdtemp())
    site = tmp / "site"
    shutil.copytree(ROOT / "docs" / "data", site)
    import os
    import importlib
    os.environ.update(NPROJ_DB=str(tmp / "t.db"), NPROJ_DATA_DIR=str(tmp), NPROJ_SITE_DATA=str(site),
                      NPROJ_ODDS_MODE="props", ODDS_API_KEY="test", NPROJ_ODDS_SHARE="0.5")
    importlib.reload(config)
    from nproj import cli, pipeline
    from nproj.ingest import espn
    espn._get = fake_get
    pipeline.REQUEST_PAUSE = 0
    get, state = fake_odds_api(440)
    odds.requests.get = get

    cli.main(["daily", "--date", "2026-03-10"])

    ln = json.loads((site / "lines.json").read_text())
    allowance = odds.daily_allowance(440, date(2026, 3, 10))       # 8 -> 4 markets x 2 games
    assert ln["games_on_slate"] == 2 and ln["markets"] == config.ODDS_MARKETS[:min(4, allowance // 2)]
    assert ln["credits_spent"] <= allowance
    assert ln["lines"]["nikola jokic"]["pra"]["book"] == "FanDuel"
    assert ln["lines"]["nikola jokic"]["pra"]["line"] == 49.5

    today = json.loads((site / "today.json").read_text())
    jok = next(p for p in today["players"] if p["player"] == "Nikola Jokic")
    assert jok["stats"]["pra"]["line"] == 49.5
    assert jok["stats"]["points"]["line"] == 26.5 and jok["stats"]["points"]["over"] == -115
    assert len(today["players"]) <= 60
    lined = {p["player"] for p in today["players"]
             if any(v.get("line") is not None for v in p["stats"].values())}
    assert lined == {"Nikola Jokic", "Player 20-0"}, lined

    jk = json.loads((site / "jokic.json").read_text())
    if config.ODDS_JOKIC_TD and len(ln["markets"]) * 2 + 1 <= allowance:
        assert jk["tonight"]["market"] == {"side": "yes", "odds": 120, "book": "FanDuel"}

    # Afternoon run: odds off, lines must survive without any paid call
    os.environ["NPROJ_ODDS_MODE"] = "off"
    importlib.reload(config)
    n_paid = sum(1 for u, _ in state["calls"] if "/odds" in u)
    cli.main(["daily", "--date", "2026-03-10"])
    assert sum(1 for u, _ in state["calls"] if "/odds" in u) == n_paid
    today2 = json.loads((site / "today.json").read_text())
    jok2 = next(p for p in today2["players"] if p["player"] == "Nikola Jokic")
    assert jok2["stats"]["pra"]["line"] == 49.5
    print("OK odds:", {k: ln[k] for k in ("markets", "credits_spent", "credits_remaining")})



# -------------------------------------------------------------- parlay ----
def fake_odds_full(start_remaining):
    """Odds API fake with lines for every rostered player, alt ladders and spreads."""
    state = {"remaining": start_remaining, "calls": []}
    rosters = {"e1": ["Nikola Jokic"] + [f"Player {t}-{i}" for t in ("7", "25") for i in range(4)],
               "e2": [f"Player {t}-{i}" for t in ("20", "29") for i in range(4)]}
    main = {"player_points": 16.5, "player_rebounds": 5.5, "player_assists": 4.5,
            "player_points_rebounds_assists": 27.5}
    alt = {"player_points_alternate": [(10.5, -500), (12.5, -300), (14.5, -220)],
           "player_rebounds_alternate": [(3.5, -400), (4.5, -250)],
           "player_assists_alternate": [(2.5, -400), (3.5, -250)],
           "player_points_rebounds_assists_alternate": [(20.5, -350), (23.5, -240)]}

    def get(url, params=None, timeout=None):
        state["calls"].append((url, dict(params or {})))
        if url.endswith("/sports"):
            return FakeResp([], state["remaining"])
        if url.endswith("/events"):
            return FakeResp([
                {"id": "e1", "home_team": "Denver Nuggets", "away_team": "Oklahoma City Thunder",
                 "commence_time": "2026-03-11T02:00:00Z"},
                {"id": "e2", "home_team": "Philadelphia 76ers", "away_team": "Memphis Grizzlies",
                 "commence_time": "2026-03-10T23:00:00Z"}], state["remaining"])
        if url.endswith("/basketball_nba/odds"):   # bulk spreads
            state["remaining"] -= 1
            return FakeResp([
                {"commence_time": "2026-03-11T02:00:00Z", "bookmakers": [{"key": "fanduel", "markets": [
                    {"key": "spreads", "outcomes": [{"name": "Denver Nuggets", "point": -3.5},
                                                    {"name": "Oklahoma City Thunder", "point": 3.5}]}]}]},
                {"commence_time": "2026-03-10T23:00:00Z", "bookmakers": [{"key": "fanduel", "markets": [
                    {"key": "spreads", "outcomes": [{"name": "Philadelphia 76ers", "point": -9.5},
                                                    {"name": "Memphis Grizzlies", "point": 9.5}]}]}]},
            ], state["remaining"])
        markets = params["markets"].split(",")
        state["remaining"] -= len(markets)
        eid = url.split("/events/")[1].split("/")[0]
        mk = []
        for m in markets:
            outs = []
            for who in rosters[eid]:
                if m in main:
                    outs += [{"name": "Over", "description": who, "price": -115, "point": main[m]},
                             {"name": "Under", "description": who, "price": -105, "point": main[m]}]
                elif m in alt:
                    outs += [{"name": "Over", "description": who, "price": pr, "point": pt}
                             for pt, pr in alt[m]]
            mk.append({"key": m, "outcomes": outs})
        return FakeResp({"bookmakers": [{"key": "fanduel", "title": "FanDuel", "markets": mk}]},
                        state["remaining"])
    return get, state


def _fresh_env(odds_mode, remaining):
    import os
    import importlib
    tmp = Path(tempfile.mkdtemp())
    site = tmp / "site"
    shutil.copytree(ROOT / "docs" / "data", site)
    os.environ.update(NPROJ_DB=str(tmp / "t.db"), NPROJ_DATA_DIR=str(tmp), NPROJ_SITE_DATA=str(site),
                      NPROJ_ODDS_MODE=odds_mode, ODDS_API_KEY="test", NPROJ_ODDS_SHARE="0.5")
    from nproj import config
    importlib.reload(config)
    from nproj import pipeline
    from nproj.ingest import espn, odds
    espn._get = fake_get
    pipeline.REQUEST_PAUSE = 0
    get, state = fake_odds_full(remaining)
    odds.requests.get = get
    return site, state


def test_results_pure():
    from nproj import results as R
    from nproj import util
    from datetime import datetime, timezone
    assert R.call_for(28.0, 25.5) == "over" and R.call_for(24.0, 25.5) is None
    assert R.call_for(22.0, 25.5) == "under" and R.call_for(10, None) is None
    assert R.grade_call("over", 25.5, 26) == "win" and R.grade_call("under", 25.5, 26) == "loss"
    # 8 PM Arizona on game night (03:00 UTC next day) is NOT final; 8 AM next morning is
    assert not util.day_is_final("2026-03-10", datetime(2026, 3, 11, 3, 0, tzinfo=timezone.utc))
    assert util.day_is_final("2026-03-10", datetime(2026, 3, 11, 15, 0, tzinfo=timezone.utc))
    snap = R.snapshot_from_board({"date": "2026-03-10", "players": [
        {"player_id": "1", "player": "A", "team": "DEN", "stats": {
            "points": {"proj": 20, "line": 18.5}, "rebounds": {"proj": 5, "line": 5.5},
            "assists": {"proj": 5, "line": None}, "pra": {"line": 29.5}}}]})
    assert snap["players"][0]["stats"]["pra"] == {"proj": 30, "line": 29.5}


def test_parlay_pure():
    from nproj.model import parlay as P
    assert P.combined_odds([-110, -110]) == 264
    assert P.combined_odds([-250, -250, -250]) == 174
    assert P.blowout_risk(None) == "unknown" and P.blowout_risk(-13.5) == "high"
    assert P.blowout_risk(4) == "low" and P.blowout_risk(-8) == "medium"
    base = {"game_id": "g", "line_type": "main"}
    opts = [dict(base, player_id=f"p{i}", game_id=f"g{i}", prob=0.9 - i * 0.01, odds=-250)
            for i in range(6)]
    legs, price = P.select_legs(opts)
    assert len(legs) == 4 and 200 <= price <= 320, (len(legs), price)
    legs, price = P.select_legs([dict(base, player_id="a", game_id="g1", prob=0.8, odds=-120),
                                 dict(base, player_id="a", game_id="g1", prob=0.7, odds=-120)])
    assert legs == [] and price is None        # one player can't carry a parlay
    same_game = [dict(base, player_id=f"p{i}", game_id="g1", prob=0.9, odds=-200) for i in range(4)]
    legs, _ = P.select_legs(same_game)
    assert len(legs) <= 2


def test_parlay_daily_and_settle():
    from datetime import date
    site, state = _fresh_env("props", 700)      # allowance (700-60)*.5/22 = 14
    from nproj import cli
    from nproj.ingest import odds
    cli.main(["daily", "--date", "2026-03-10"])

    ln = json.loads((site / "lines.json").read_text())
    allowance = odds.daily_allowance(700, date(2026, 3, 10))
    assert ln["credits_spent"] + ln.get("extras_spent", 0) <= allowance, ln
    assert ln["spreads"]["DEN"] == -3.5 and ln["spreads"]["PHI"] == -9.5

    pj = json.loads((site / "parlay.json").read_text())
    assert pj["history"] == [], "mock history must be dropped on the first real day"
    t = pj["today"]
    assert t and t["status"] == "pending" and 2 <= len(t["legs"]) <= 4, pj
    assert 150 <= t["combined_odds"] <= 320
    assert all(l["side"] == "over" for l in t["legs"]), "parlay is overs only"
    assert len({l["player_id"] for l in t["legs"]}) == len(t["legs"])
    risk = {l["team"]: l["blowout_risk"] for l in t["legs"]}
    assert all(risk.get(tm, "medium") == "medium" for tm in ("PHI", "MEM"))
    # a high-blowout-risk game is excluded from candidates entirely
    from nproj import db
    from nproj.model import parlay as P
    with db.session() as con:
        blow = dict(ln, spreads={"PHI": -13.5, "MEM": 13.5, "DEN": -3.5, "OKC": 3.5})
        assert all(c["team"] not in ("PHI", "MEM") for c in P.candidates(con, "2026-03-10", blow))
    assert all(l["reasoning"] and 0 < l["prob"] < 1 for l in t["legs"])
    if ln.get("alts"):
        assert any(l["line_type"] == "alt" for l in t["legs"])
    n_paid = sum(1 for u, _ in state["calls"] if u.endswith("/odds"))

    # afternoon: nothing bought, parlay still there
    import os, importlib
    os.environ["NPROJ_ODDS_MODE"] = "off"
    from nproj import config
    importlib.reload(config)
    cli.main(["daily", "--date", "2026-03-10"])
    assert sum(1 for u, _ in state["calls"] if u.endswith("/odds")) == n_paid
    after = json.loads((site / "parlay.json").read_text())["today"]
    # locked: the afternoon run never swaps legs you may already have bet
    strip = lambda legs: [{k: v for k, v in l.items() if k != "check"} for l in legs]  # noqa: E731
    assert strip(after["legs"]) == strip(t["legs"]) and after["combined_odds"] == t["combined_odds"]
    # the morning run took the injury-check checkpoint; the afternoon run compared to it
    cp = json.loads((site / "checkpoint.json").read_text())
    assert cp["date"] == "2026-03-10" and cp["label"] == "this morning's board"
    assert "injury_check" in json.loads((site / "today.json").read_text())

    # settle: give every leg player a real game on 2026-03-10, then run the next day
    from nproj.ingest import espn
    orig = espn.fetch_player_game_log

    def with_game(pid, season):
        rows = orig(pid, season)
        if season == 2026:
            rows.append({"game_id": f"x{pid}", "player_id": pid, "date": "2026-03-10", "team": "DEN",
                         "opp": "vs OKC", "minutes": 34.0, "points": 40, "rebounds": 12,
                         "assists": 11, "threes": 2, "playoff": False})
        return rows
    espn.fetch_player_game_log = with_game
    cli.main(["daily", "--date", "2026-03-11"])
    espn.fetch_player_game_log = orig
    pj = json.loads((site / "parlay.json").read_text())
    h = pj["history"][-1]
    assert h["date"] == "2026-03-10" and h["result"] in ("win", "loss")
    for leg in h["legs"]:
        assert leg["actual"] is not None
        assert leg["hit"] == (leg["actual"] > leg["line"] if leg["side"] == "over"
                              else leg["actual"] < leg["line"])
    # results: last night's board graded against the same box scores
    res = json.loads((site / "results.json").read_text())
    night = res["nights"][-1]
    assert night["date"] == "2026-03-10" and night["players_graded"] >= 1
    assert all(night["mae"][k] is not None for k in ("points", "rebounds", "assists", "pra"))
    for c in res["calls"]:
        assert c["result"] == ("win" if (c["actual"] > c["line"]) == (c["side"] == "over") else "loss")
    assert res["summary"]["nights"] == 1
    assert res["pending"] is None or res["pending"]["date"] == "2026-03-11"
    print("OK results:", night["calls"], night["mae"])
    print("OK parlay:", t["combined_odds"], [(l["player"], l["stat"], l["side"], l["line"], l["line_type"],
                                              l["odds"], l["prob"]) for l in t["legs"]], "->", h["result"])


if __name__ == "__main__":
    test_odds_rationing_and_board()
    test_results_pure()
    test_parlay_pure()
    test_parlay_daily_and_settle()
