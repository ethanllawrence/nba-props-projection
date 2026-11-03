"""Parlay of the Day engine (and the retired early-look parlay).

SINCE 2026-11-02 (Robin): 3 PRA legs from the "greenest" players on the Today
board, i.e. the projection furthest above the book's main PRA line as a share
of it. One leg per game; each leg is the book line or an alt below it (and
below our projection), priced -300 to +200; the 3 together land at +100 to
+300 real odds. Most total green wins; ties go to the combination the book
rates likeliest. If nothing fits, legs up to -350 are allowed, then 2 legs
(TIERS, Robin 2026-11-03); only if that fails too is there no parlay. The 1.5x rule
(live.FAR_RATIO) still applies. See the constants below; the notes that
follow describe the earlier gap-based rules, kept for the record.

Early-look parlay (history): 3-4 overs for the next game day at about +200 to +320
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

# Retired 2026-10-22: the early-look parlay (picked a morning ahead from paid
# alt lines) became the night pick of the Parlay of the Day (parlay.night_pick),
# which uses ESPN's free lines once posted. This module's candidates/select are
# that pick's engine; only its own day-ahead pick and purchase are switched off.
ENABLED = False
# ---- Parlay of the Day rules since 2026-11-02 (Robin): "green" PRA legs ----
# 3 PRA legs, the players the model likes most RELATIVE TO THE BOOK'S LINE
# (the greenest PRA numbers on the Today board: (projection - book line) / line),
# one leg per game, each leg at the book line or an alt below it (always below
# our projection) and priced no shorter than -300 (a -400 leg barely moves the
# payout), and the three together at +100 to +300. Of every combination in
# range, the one with the most total green wins; ties go to the combination
# the book rates likeliest to hit. Earlier versions (3-4 legs by model
# chance minus book chance, any stat) went 2-11 to start the season (through Nov 1).
POOL_TOP = 60                    # the night's top players by projected PRA ...
MIN_MINUTES = 26.0               # ... projected for this many minutes or more
LEG_ODDS = (-350, 200)           # widest price range for one leg (American odds); see TIERS
TARGET_LO, TARGET_HI = 100, 300  # the legs together
MIN_ACCEPT = TARGET_LO
# Tried in order until one lands in range (Robin, 2026-11-03: a night with games
# and a board shouldn't go without a parlay): 3 legs at -300 or longer, then 3
# legs allowing -350, then 2 legs allowing -350. Nothing fits even then -> none.
TIERS = ((3, -300), (3, -350), (2, -350))
TIER_NOTE = {1: "one or more legs allowed at up to -350 (nothing fit at -300)",
             2: "2 legs (no 3-leg parlay fit at up to -350)"}
MIN_LEGS, MAX_LEGS = 2, 3
MAX_PER_GAME = 1
BLOWOUT_SPREAD = 12              # skip games with a spread this big (when a spread is known)
ALT_HOLD = 1.045                 # typical book cut on one-sided alt prices
# Stats a parlay leg can use. Assists-only and rebounds-only legs were dropped
# 2026-10-30 (through 10 parlays: assists legs 7 of 12, rebounds 7 of 11, where
# the books expected about 8.5 and 7.7; points 12 of 12); points-or-PRA ran
# Oct 31 - Nov 2. PRA only since Nov 2.
PARLAY_STATS = ("pra",)
MAX_PRA_LEGS = MAX_LEGS
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
            if stat not in pr or stat not in PARLAY_STATS:
                continue
            ml = (mains.get(stat) or {}).get("line")
            if not ml:
                continue                # "green" needs the book's main line
            if pr[stat] / ml >= FAR_RATIO:
                continue                # the book knows something (see live.FAR_RATIO)
            if pr[stat] <= ml:
                continue                # not green: the model isn't over the book's line
            green = float((pr[stat] - ml) / ml)
            for step in ladder:
                price, line = step.get("over"), step.get("line")
                if price is None or line is None or not (LEG_ODDS[0] <= int(price) <= LEG_ODDS[1]):
                    continue
                if line > ml or pr[stat] <= line:
                    continue            # the book line or an alt below it, below our projection
                pm, pb = model_chance(pr, stat, line), book_chance(price)
                out.append({**who, "stat": stat, "line": line, "odds": int(price), "book": step.get("book"),
                            "model_prob": round(pm, 3), "book_prob": round(pb, 3), "gap": round(pm - pb, 3),
                            "proj": round(float(pr[stat]), 1), "book_line": ml, "green": round(green, 4)})
    # greenest first, then the book's own chance (safer legs first on ties)
    return sorted(out, key=lambda c: (-c["green"], -c["book_prob"]))


SEARCH_TOP = 60          # greenest legs considered when combining


def combo_ok(combo, fixed=()):
    """One leg per player and at most MAX_PER_GAME legs from one game."""
    legs = list(fixed) + list(combo)
    if len({c["player_id"] for c in legs}) < len(legs):
        return False
    if sum(1 for c in legs if c.get("stat") == "pra") > MAX_PRA_LEGS:
        return False
    by_game = {}
    for c in legs:
        by_game[c["game_id"]] = by_game.get(c["game_id"], 0) + 1
    return max(by_game.values(), default=0) <= MAX_PER_GAME


def _green(c):
    g = c.get("green")
    if g is None and c.get("book_line"):
        g = (float(c["proj"]) - float(c["book_line"])) / float(c["book_line"])
    return g or 0.0


def select(cands, fixed=(), sizes=None):
    """The best combination under the first of TIERS that has one in range:
    one leg per player and per game, REAL combined odds at +TARGET_LO..+TARGET_HI,
    the most total green (projection over the book's line, as a share of the
    line); ties go to the combination the book rates likeliest. Returned legs
    carry "tier" (0 = the normal 3 legs at -300; see TIER_NOTE). Nothing in
    range -> ([], None). fixed: legs that must stay (the morning check keeps the
    night pick's healthy legs, whatever their price now, and only replaces the
    rest). sizes: unused, kept for older callers."""
    from itertools import combinations
    fixed = list(fixed)
    taken = {f["player_id"] for f in fixed}
    pool = [c for c in cands if c["player_id"] not in taken][:SEARCH_TOP]
    for tier, (n_legs, cap) in enumerate(TIERS):
        n = n_legs - len(fixed)
        if n < 0:
            continue
        sub = [c for c in pool if int(c["odds"]) >= cap]
        best = None
        for combo in combinations(sub, n):
            if not combo_ok(combo, fixed):
                continue
            legs = fixed + list(combo)
            price = combined_odds([c["odds"] for c in legs])
            if not TARGET_LO <= price <= TARGET_HI:
                continue
            score = (round(sum(_green(c) for c in legs), 4), math.prod(c["book_prob"] for c in legs))
            if best is None or score > best[0]:
                best = (score, legs, price)
        if best:
            legs = [{**c, "tier": tier} for c in sorted(best[1], key=lambda c: -_green(c))]
            return legs, best[2]
    return [], None


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
