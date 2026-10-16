"""Early-look parlay: 3-4 overs for the next game day at about +200 to +320
REAL sportsbook odds, built from real alt-line prices and filtered by the model.

Robin's goal (2026-10-16): "picking lines that incorporate books AND our model to
selectively snipe out picks", 3-4 legs, about +200 on the books. A first
version picked legs by model chance alone (78-86%); the books priced those
same legs at -1100 to -7000 (-589 parlayed), because the books rate stars as
far safer than the model does. Lesson: legs must come from real prices.

How it works:
  1. Prices: alt-line ladders (PRA and points) for the next game day, bought on
     the morning odds run once the day is within 2 days (ingest/odds.py
     refresh_early_alts). No prices yet -> no parlay, the page says it's waiting.
  2. Pool: the night's top POOL_TOP players by projected PRA, projected for
     MIN_MINUTES+, not on the injury report, with game history.
  3. Each player's candidate legs: alt overs priced between LEG_ODDS (-300 to
     -150, so 3-4 legs land near +200), where the model projects above the line.
     For each: the model's chance (star-calibrated, star_calibration.json) and
     the book's chance (price with its cut removed). gap = model - book.
  4. Veto: drop legs the model rates more than VETO below the book. Evidence
     (archived main lines, star overs, 7,057 bets): when the model was 10+
     points below the book, overs hit 44.5% vs the book's 49.8% (-16% return);
     at +3 to +8 points above, they hit 54% (+1.8%). The model's disagreement
     is most useful for spotting traps.
  5. Each player's best leg (largest gap), then legs in order of gap, one per
     player, at most MAX_PER_GAME per game, until the real combined odds reach
     TARGET_LO (never past TARGET_HI), 3-4 legs. Short of MIN_ACCEPT -> none.

Saved in docs/data/parlay.json under "early" and graded the next morning
("early_history"), like the main parlay.
"""
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .pra_model import prob_over

POOL_TOP = 30
MIN_MINUTES = 30.0
LEG_ODDS = (-300, -150)          # price range for a leg (American odds)
TARGET_LO, TARGET_HI = 200, 320
MIN_ACCEPT = 150
MIN_LEGS, MAX_LEGS = 3, 4
MAX_PER_GAME = 2
VETO = -0.03                     # model may be at most 3 points below the book
ALT_HOLD = 1.045                 # typical book cut on one-sided alt prices
STAR_CAL = json.loads((Path(__file__).parent / "star_calibration.json").read_text())


def to_decimal(american):
    a = int(american)
    return 1 + a / 100.0 if a > 0 else 1 + 100.0 / abs(a)


def combined_odds(prices):
    dec = math.prod(to_decimal(p) for p in prices)
    return round((dec - 1) * 100) if dec >= 2 else round(-100 / (dec - 1))


def book_chance(american):
    a = int(american)
    implied = -a / (-a + 100) if a < 0 else 100 / (a + 100)
    return implied / ALT_HOLD


def model_chance(pr, stat, line):
    """Calibrated chance of going over `line` (e.g. 29.5 -> 30+)."""
    a, b = pr["var"][stat]
    p = float(prob_over(np.array([pr[stat]]), np.array([line]), a, b)[0])
    p = min(max(p, 1e-4), 1 - 1e-4)
    z = STAR_CAL["intercept"] + STAR_CAL["slope"] * math.log(p / (1 - p))
    return 1 / (1 + math.exp(-z))


def candidates(con, date_s, proj, alts):
    """Best qualifying leg per pool player. alts: {norm_name: {stat: ladder}}."""
    from ..ingest import odds
    rows = con.execute(
        """SELECT pp.player_id, pp.game_id, pp.status, p.name, p.team, g.home_team, g.away_team
           FROM probable_players pp JOIN players p ON p.player_id = pp.player_id
           JOIN games g ON g.game_id = pp.game_id WHERE pp.date = ?""", (date_s,)).fetchall()
    pool = []
    for r in rows:
        pr = proj.get(str(r["player_id"]))
        if (pr and (r["status"] or "active") == "active" and pr.get("minutes", 0) >= MIN_MINUTES
                and pr.get("has_history", True)):
            pool.append((pr["pra"], r, pr))
    pool = sorted(pool, key=lambda x: -x[0])[:POOL_TOP]
    matched, _ = odds.match_lines([r["name"] for _, r, _ in pool], alts)
    out = []
    for _, r, pr in pool:
        best = None
        for stat, ladder in (matched.get(r["name"]) or {}).items():
            if stat not in pr:
                continue
            for step in ladder:
                price, line = step.get("over"), step.get("line")
                if price is None or line is None or not (LEG_ODDS[0] <= int(price) <= LEG_ODDS[1]):
                    continue
                if pr[stat] <= line:
                    continue            # the model must project over the line
                pm, pb = model_chance(pr, stat, line), book_chance(price)
                gap = pm - pb
                if gap < VETO:
                    continue
                leg = {"stat": stat, "line": line, "odds": int(price), "book": step.get("book"),
                       "model_prob": round(pm, 3), "book_prob": round(pb, 3), "gap": round(gap, 3),
                       "proj": round(float(pr[stat]), 1)}
                if best is None or gap > best["gap"]:
                    best = leg
        if best:
            home = r["team"] == r["home_team"]
            out.append({"player": r["name"], "player_id": str(r["player_id"]), "team": r["team"],
                        "opp": r["away_team"] if home else r["home_team"], "home": home,
                        "game_id": str(r["game_id"]), "side": "over", **best})
    return sorted(out, key=lambda c: -c["gap"])


def select(cands):
    legs, per_game = [], {}
    for c in cands:
        if per_game.get(c["game_id"], 0) >= MAX_PER_GAME:
            continue
        if legs and combined_odds([l["odds"] for l in legs] + [c["odds"]]) > TARGET_HI:
            continue
        legs.append(c)
        per_game[c["game_id"]] = per_game.get(c["game_id"], 0) + 1
        price = combined_odds([l["odds"] for l in legs])
        if (len(legs) >= MIN_LEGS and price >= TARGET_LO) or len(legs) == MAX_LEGS:
            break
    if len(legs) < MIN_LEGS:
        return [], None
    price = combined_odds([l["odds"] for l in legs])
    return (legs, price) if price >= MIN_ACCEPT else ([], None)


def pick(con, date_s, proj, alts):
    """-> parlay dict (legs may be empty, with the reason in note)."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not alts:
        return {"date": date_s, "legs": [], "status": "waiting", "picked_at": now,
                "note": "Waiting for alt-line prices. They're bought on the morning run once the "
                        "games are within 2 days."}
    cands = candidates(con, date_s, proj, alts)
    legs, price = select(cands)
    if not legs:
        return {"date": date_s, "legs": [], "status": "none", "picked_at": now,
                "note": f"No early-look parlay: {len(cands)} overs passed the price range and the "
                        f"model's veto, not enough to reach +{MIN_ACCEPT} with {MIN_LEGS}-{MAX_LEGS} legs."}
    model_all = float(np.prod([l["model_prob"] for l in legs]))
    return {"date": date_s, "legs": legs, "combined_odds": price, "model_prob": round(model_all, 3),
            "book_prob": round(float(np.prod([l["book_prob"] for l in legs])), 3),
            "status": "pending", "picked_at": now, "candidates": len(cands)}
