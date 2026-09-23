"""Parsers for the historical backfill, checked against real ESPN responses
(trimmed copies captured 2026-09-22)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nproj.ingest import espn_history as H  # noqa: E402


def test_parse_summary():
    s = json.loads((ROOT / "tests/fixtures/summary_401810791_slim.json").read_text())
    game, rows = H.parse_summary(s, "401810791", "2026-03-10", 2026, False)
    assert game["home"] == "PHI" and game["away"] == "MEM"
    assert (game["home_score"], game["away_score"]) == (139, 129)
    assert game["spread_home"] == -4.5 and game["total"] == 229.5
    cc = next(r for r in rows if r["player"] == "Cedric Coward")
    assert cc["team"] == "MEM" and cc["opp"] == "PHI" and cc["home"] == 0 and cc["starter"] == 1
    assert (cc["minutes"], cc["points"], cc["rebounds"], cc["assists"]) == (24, 13, 16, 3)
    assert (cc["fgm"], cc["fga"], cc["fg3m"], cc["fg3a"], cc["ftm"], cc["fta"]) == (4, 8, 2, 3, 3, 4)
    assert cc["oreb"] == 7 and cc["plus_minus"] == 2
    bc = next(r for r in rows if r["player"] == "Brandon Clarke")
    assert bc["dnp"] == 1 and bc["dnp_reason"] == "RIGHT CALF STRAIN" and bc["points"] is None
    qg = next(r for r in rows if r["player"] == "Quentin Grimes")
    assert qg["home"] == 1 and qg["plus_minus"] == 11 and qg["fta"] == 13


def test_parse_prop_item():
    ref = ("http://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/seasons/2025/"
           "athletes/6609?lang=en&region=us")
    over = {"type": {"id": "1", "name": "Total Points"}, "athlete": {"$ref": ref},
            "current": {"over": {"american": "-105"}, "target": {"value": 13.5}},
            "open": {"over": {"american": "-120"}, "target": {"value": 14.5}}}
    r = H.parse_prop_item(over, "g", "2025-03-01", "58")
    assert r == {"game_id": "g", "date": "2025-03-01", "player_id": "6609", "stat": "points",
                 "line": 13.5, "side": "over", "price": -105, "open_line": 14.5,
                 "open_price": -120, "provider": "58"}
    pra = {"type": {"id": "90"}, "athlete": {"$ref": ref},
           "current": {"under": {"american": "+110"}, "target": {"value": 22.5}}}
    assert H.parse_prop_item(pra, "g", "d", "58")["price"] == 110
    # target-only milestone rows (no side priced) and unwanted markets are skipped
    assert H.parse_prop_item({"type": {"id": "1"}, "athlete": {"$ref": ref},
                              "current": {"target": {"value": 9.5}}}, "g", "d", "58") is None
    assert H.parse_prop_item({"type": {"id": "5"}, "athlete": {"$ref": ref},
                              "current": {"over": {"american": "-110"}, "target": {"value": 1.5}}},
                             "g", "d", "58") is None


if __name__ == "__main__":
    test_parse_summary()
    test_parse_prop_item()
    print("OK history parsers")
