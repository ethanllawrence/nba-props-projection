"""Early-look parlay: a strong-conviction parlay picked from the early look
(the next game day's model projections), before any sportsbook lines exist.

No lines means no prices, so legs are MILESTONE overs, the round-number props
books post for every star (30+ PRA, 20+ points, 8+ rebounds, ...). The model
gives a chance for any milestone straight from its projection and count
distribution (pra_model.prob_over).

Rules (Robin, 2026-10-14: stars, overs, strong conviction):
  - pool: the night's top POOL_TOP players by projected PRA, projected for
    30+ minutes, not on the injury report;
  - each player's leg: the highest milestone the model gives between
    BAND_LO and BAND_HI, preferring PRA, then points, rebounds, assists;
  - legs in order of projected PRA (biggest names first), one per player,
    at most MAX_PER_GAME per game, N_LEGS legs (needs at least MIN_LEGS).

Tested 2026-10-14 by replaying every night of 2024-25 and 2025-26 with the
model trained only on earlier games (scratchpad early_parlay_sim4.py): the
four-leg version hit 63% both seasons while the model predicted about 45%.
At milestone lines the model's chances were honest overall and cautious for
stars (75-80% predicted -> 79-82% actual), and legs from the same game
were no more correlated than legs from different games.

Saved in docs/data/parlay.json under "early" (pending picks by date) and
"early_history" (graded the morning after, a player who didn't play is void).
"""
from datetime import datetime, timezone

import numpy as np

from .pra_model import prob_over

BAND_LO, BAND_HI = 0.78, 0.86
N_LEGS, MIN_LEGS = 4, 3
POOL_TOP = 30
MIN_MINUTES = 30.0
MAX_PER_GAME = 2
MILESTONES = {"pra": list(range(15, 60, 5)), "points": list(range(10, 45, 5)),
              "rebounds": list(range(4, 16, 2)), "assists": list(range(2, 14, 2))}
PRIORITY = ("pra", "points", "rebounds", "assists")
LABEL = {"pra": "PRA", "points": "points", "rebounds": "rebounds", "assists": "assists"}
REPLAY_NOTE = ("Replayed on every night of 2024-25 and 2025-26 (model trained only on earlier games), "
               "this rule's four-leg parlay hit 63% of nights in both seasons; the model predicted "
               "about 45%, so its chances run cautious for stars.")


def chance(pr, stat, k):
    """Model chance of k or more."""
    a, b = pr["var"][stat]
    return float(prob_over(np.array([pr[stat]]), np.array([k - 0.5]), a, b)[0])


def pick(con, date_s, proj):
    """proj: live.PROJ for date_s. -> parlay dict or None."""
    rows = con.execute(
        """SELECT pp.player_id, pp.game_id, pp.status, p.name, p.team, g.home_team, g.away_team
           FROM probable_players pp JOIN players p ON p.player_id = pp.player_id
           JOIN games g ON g.game_id = pp.game_id WHERE pp.date = ?""", (date_s,)).fetchall()
    pool = []
    for r in rows:
        pr = proj.get(str(r["player_id"]))
        if (not pr or (r["status"] or "active") != "active" or pr.get("minutes", 0) < MIN_MINUTES
                or not pr.get("has_history", True)):
            continue
        pool.append((pr["pra"], r, pr))
    pool.sort(key=lambda x: -x[0])
    legs, per_game = [], {}
    for pra, r, pr in pool[:POOL_TOP]:
        if per_game.get(r["game_id"], 0) >= MAX_PER_GAME:
            continue
        leg = None
        for stat in PRIORITY:
            ok = [(k, chance(pr, stat, k)) for k in MILESTONES[stat]]
            ok = [(k, p) for k, p in ok if BAND_LO <= p <= BAND_HI]
            if ok:
                k, p = max(ok)
                leg = {"stat": stat, "milestone": k, "prob": round(p, 3)}
                break
        if not leg:
            continue
        home = r["team"] == r["home_team"]
        legs.append({"player": r["name"], "player_id": str(r["player_id"]), "team": r["team"],
                     "opp": r["away_team"] if home else r["home_team"], "home": home,
                     "game_id": str(r["game_id"]), **leg,
                     "proj": round(float(pr[leg["stat"]]), 1),
                     # graded like any over: line just under the milestone
                     "side": "over", "line": leg["milestone"] - 0.5})
        per_game[r["game_id"]] = per_game.get(r["game_id"], 0) + 1
        if len(legs) == N_LEGS:
            break
    if len(legs) < MIN_LEGS:
        return None
    p_all = float(np.prod([l["prob"] for l in legs]))
    fair = round((1 / p_all - 1) * 100) if p_all < 0.5 else -round(100 * p_all / (1 - p_all))
    return {"date": date_s, "legs": legs, "combined_prob": round(p_all, 3), "fair_odds": fair,
            "status": "pending", "picked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "note": REPLAY_NOTE}
