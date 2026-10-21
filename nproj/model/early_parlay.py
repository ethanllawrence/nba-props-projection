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
     Players who just changed teams are skipped until they have 3 of their
     last 5 games with the new team (the model still sees their old role).
  4. Veto: drop legs the model rates more than 10 points below the book.
     Evidence (archived main lines, star overs, 7,057 bets): 10+ points below,
     overs hit 44.5% vs the book's 49.8% (-16% return); 3-10 below, 50.3% vs
     49.7% (-5%, about the book's normal cut); +3 to +8 above, 54% (+1.8%).
     Only the 10+ group was a real trap, so that's the cutoff (it was 3
     points at first, which on opening-night lines left a single leg).
  5. Of all qualifying legs, the 3-4 leg combination (one per player, at most
     MAX_PER_GAME per game) whose real combined odds land at +TARGET_LO to
     +TARGET_HI with the largest total gap; if none, the best at +MIN_ACCEPT to
     +TARGET_LO; otherwise no parlay. (Picking each player's single best leg and
     adding greedily could end with four -400 legs at +144 and no parlay.)
     Each leg's gap counts at most GAP_CAP (+8 points). Evidence (2026-10-20,
     archived main lines, star overs, 14,396 bets, walk-forward projections):
       gap +3 to +8:  hit 53.1% vs book 49.9%  (model said 55%)
       gap +8 to +15: hit 53.0% vs book 49.9%  (model said 61%)
       gap +15 up:    hit 52.0% vs book 49.3%  (model said 71%)
     Beyond about +8 a bigger gap is NOT a bigger edge: the model is just
     overconfident there (often the book knows about a minutes limit the model
     can't see). So a +20 gap is scored like +8, and ties go to the
     combination the book itself rates likeliest to hit.

The same engine picks the morning Parlay of the Day (nproj/model/parlay.py,
price_first) from today's prices.

Saved in docs/data/parlay.json under "early" and graded the next morning
("early_history"), like the main parlay.
"""
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .pra_model import prob_over
from .live import FAR_RATIO

POOL_TOP = 30
MIN_MINUTES = 30.0
LEG_ODDS = (-400, -120)          # price range for a leg (American odds); alt ladders step
                                 # 5 points at a time, so prices jump (Brunson 30+ PRA -390,
                                 # 35+ -146): a narrower window missed both
TARGET_LO, TARGET_HI = 200, 320
MIN_ACCEPT = 150
MIN_LEGS, MAX_LEGS = 3, 4
MAX_PER_GAME = 3                 # same-game legs weren't more correlated in the replay
VETO = -0.10                     # drop legs the model rates 10+ points below the book
GAP_CAP = 0.08                   # a gap above +8 points scores like +8 (see 5. above)
BLOWOUT_SPREAD = 12              # skip games with a spread this big (when a spread is known)
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


def _tip(t):
    if not t:
        return None
    try:
        d = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def candidates(con, date_s, proj, alts, spreads=None, not_before=None, main_lines=None):
    """Every qualifying leg for pool players, best gap first.
    alts: {norm_name: {stat: ladder}}. spreads: {team: spread} to skip
    blowout games. not_before: UTC datetime; skip games tipping before it."""
    from ..ingest import odds
    rows = con.execute(
        """SELECT pp.player_id, pp.game_id, pp.status, p.name, p.team, g.home_team, g.away_team,
                  g.tipoff_utc
           FROM probable_players pp JOIN players p ON p.player_id = pp.player_id
           JOIN games g ON g.game_id = pp.game_id WHERE pp.date = ?""", (date_s,)).fetchall()
    spreads = spreads or {}
    pool = []
    for r in rows:
        tip = _tip(r["tipoff_utc"])
        if not_before is not None and tip is not None and tip <= not_before:
            continue
        sp = spreads.get(r["team"])
        if sp is not None and abs(sp) >= BLOWOUT_SPREAD:
            continue
        pr = proj.get(str(r["player_id"]))
        if (pr and (r["status"] or "active") == "active" and pr.get("minutes", 0) >= MIN_MINUTES
                and pr.get("has_history", True) and not pr.get("new_team")):
            pool.append((pr["pra"], r, pr))
    pool = sorted(pool, key=lambda x: -x[0])[:POOL_TOP]
    matched, _ = odds.match_lines([r["name"] for _, r, _ in pool], alts)
    main_matched = odds.match_lines([r["name"] for _, r, _ in pool], main_lines)[0] if main_lines else {}
    out = []
    for _, r, pr in pool:
        home = r["team"] == r["home_team"]
        who = {"player": r["name"], "player_id": str(r["player_id"]), "team": r["team"],
               "opp": r["away_team"] if home else r["home_team"], "home": home,
               "game_id": str(r["game_id"]), "side": "over", "tipoff_utc": r["tipoff_utc"]}
        mains = (main_matched.get(r["name"]) or {}) if main_lines else {}
        for stat, ladder in (matched.get(r["name"]) or {}).items():
            if stat not in pr:
                continue
            ml = (mains.get(stat) or {}).get("line")
            if ml and pr[stat] / ml >= FAR_RATIO:
                continue                # the book knows something (see live.FAR_RATIO)
            for step in ladder:
                price, line = step.get("over"), step.get("line")
                if price is None or line is None or not (LEG_ODDS[0] <= int(price) <= LEG_ODDS[1]):
                    continue
                if pr[stat] <= line:
                    continue            # the model must project over the line
                pm, pb = model_chance(pr, stat, line), book_chance(price)
                if pm - pb < VETO:
                    continue
                out.append({**who, "stat": stat, "line": line, "odds": int(price), "book": step.get("book"),
                            "model_prob": round(pm, 3), "book_prob": round(pb, 3), "gap": round(pm - pb, 3),
                            "proj": round(float(pr[stat]), 1)})
    # capped gap first, then the book's own chance (safer legs first on ties)
    return sorted(out, key=lambda c: (-min(c["gap"], GAP_CAP), -c["book_prob"]))


SEARCH_TOP = 24          # best-gap legs considered when combining


def select(cands):
    """The 3-4 leg combination (one leg per player, at most MAX_PER_GAME per
    game) whose REAL combined odds land in +TARGET_LO..+TARGET_HI and that the
    model likes most relative to the book (largest total gap). If none lands
    in that range, the best one between +MIN_ACCEPT and +TARGET_LO."""
    from itertools import combinations
    pool = cands[:SEARCH_TOP]
    best, fallback = None, None
    cap = lambda g: min(g, GAP_CAP)
    for n in range(MIN_LEGS, MAX_LEGS + 1):
        for combo in combinations(pool, n):
            if len({c["player_id"] for c in combo}) < n:
                continue
            games = [c["game_id"] for c in combo]
            if max(games.count(g) for g in games) > MAX_PER_GAME:
                continue
            price = combined_odds([c["odds"] for c in combo])
            score = (round(sum(cap(c["gap"]) for c in combo), 4),
                     math.prod(c["book_prob"] for c in combo))
            if TARGET_LO <= price <= TARGET_HI:
                if best is None or score > best[0]:
                    best = (score, combo, price)
            elif MIN_ACCEPT <= price < TARGET_LO:
                if fallback is None or score > fallback[0]:
                    fallback = (score, combo, price)
    pick = best or fallback
    if not pick:
        return [], None
    return sorted(pick[1], key=lambda c: -c["gap"]), pick[2]


def pick(con, date_s, proj, alts, spreads=None):
    """-> parlay dict (legs may be empty, with the reason in note)."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not alts:
        return {"date": date_s, "legs": [], "status": "waiting", "picked_at": now,
                "note": "Waiting for alt-line prices. They're bought on the morning run once the "
                        "games are within 2 days."}
    cands = candidates(con, date_s, proj, alts, spreads)
    legs, price = select(cands)
    if not legs:
        return {"date": date_s, "legs": [], "status": "none", "picked_at": now,
                "note": f"No early-look parlay: {len(cands)} overs passed the price range and the "
                        f"model's veto, but no {MIN_LEGS}-{MAX_LEGS} leg combination reaches +{MIN_ACCEPT}."}
    model_all = float(np.prod([l["model_prob"] for l in legs]))
    return {"date": date_s, "legs": legs, "combined_odds": price, "model_prob": round(model_all, 3),
            "book_prob": round(float(np.prod([l["book_prob"] for l in legs])), 3),
            "status": "pending", "picked_at": now, "candidates": len(cands)}
