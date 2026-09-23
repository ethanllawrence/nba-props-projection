"""Run the PRA model for tonight's slate.

1. Load every season in history/ (the backfill plus the in-season store kept
   current by nproj/ingest/box_store.py).
2. Turn tonight's slate (from the database: rosters, injury status, spread
   and total) into rows shaped like box-score rows, with no stats yet.
   Players ESPN lists as out count as "teammates out" for everyone else.
3. Build features for history + tonight together (tonight's rows only look
   at games before tonight), train on history, predict tonight.
4. Save projections to the database (the site export reads them) and keep
   the fitted variance curves in PROJ so over/under probabilities can be
   computed for any line, including alt lines for the parlay.

Probabilities shown on the site are a blend of the model and the sportsbook
price (nproj/model/calibration.json, fitted on the backtest): the raw model
alone was overconfident, and the market carries information the model
doesn't (late news, lineups). An edge is flagged only when the blended
probability beats the price's break-even by EDGE_MIN.
"""
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config
from . import features as F
from .pra_model import PRAModel, prob_over

EDGE_MIN = 0.02
MIN_PROJ_MINUTES = 18.0
HISTORY_ROOT = Path(config.ROOT) / "history"
CAL = json.loads((Path(__file__).parent / "calibration.json").read_text())

PROJ = {}          # player_id -> {"points": mu, ..., "var": {stat: (a, b)}}


def _logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def implied(price):
    price = float(price)
    return -price / (-price + 100) if price < 0 else 100 / (price + 100)


ALT_HOLD = 1.045   # typical sportsbook margin, removed from one-sided (alt) prices


def market_prob_over(over_price, under_price):
    """No-vig chance of the over. With only the over priced (alt lines),
    strip a typical margin instead. None if there's no price at all."""
    if over_price is None:
        return None
    if under_price is None:
        return min(implied(over_price) / ALT_HOLD, 0.995)
    a, b = implied(over_price), implied(under_price)
    return a / (a + b)


def blended_p_over(p_model, p_market):
    if p_market is None:
        # no two-sided market (alt lines): shrink the model the way the
        # calibration does, around an even market
        p_market = 0.5
    z = CAL["intercept"] + CAL["w_model"] * _logit(p_model) + CAL["w_market"] * _logit(p_market)
    return 1 / (1 + math.exp(-z))


def assess(player_id, stat, line, over_price=None, under_price=None):
    """Model view of one prop. -> dict(p_over, call, edge) or None."""
    pr = PROJ.get(str(player_id))
    if not pr or stat not in pr or line is None:
        return None
    a, b = pr["var"][stat]
    p_model = float(prob_over(np.array([pr[stat]]), np.array([line]), a, b)[0])
    p = blended_p_over(p_model, market_prob_over(over_price, under_price))
    call, edge = None, 0.0
    if over_price is not None and under_price is None:        # alt line: over only
        edge = p - implied(over_price)
        return {"p_over": round(p, 3), "call": "over" if edge >= EDGE_MIN else None,
                "edge": round(edge, 3)}
    if over_price is not None and under_price is not None:
        eo, eu = p - implied(over_price), (1 - p) - implied(under_price)
        if eo >= EDGE_MIN and eo >= eu:
            call, edge = "over", eo
        elif eu >= EDGE_MIN:
            call, edge = "under", eu
    return {"p_over": round(p, 3), "call": call, "edge": round(edge, 3)}


TD_PATH = Path(__file__).parent / "td_calibration.json"
TD_SIMS = 40000


def td_probability(player_id, corr=None, seed=7):
    """Chance of 10+ points, rebounds and assists tonight, from the model's
    three projections and their count distributions (see pra_model), drawn
    TOGETHER: a Gaussian copula ties them with the correlation measured on
    held-out games (td_calibration.json), because a big-minutes night lifts
    all three. Multiplying three separate chances would ignore that.
    -> float or None."""
    from scipy import stats as st
    pr = PROJ.get(str(player_id))
    if not pr or not all(k in pr for k in ("points", "rebounds", "assists")):
        return None
    if corr is None:
        try:
            corr = json.loads(TD_PATH.read_text())["corr"]
        except (OSError, ValueError, KeyError):
            corr = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    rng = np.random.default_rng(seed)
    z = rng.multivariate_normal(np.zeros(3), np.array(corr, dtype=float), size=TD_SIMS)
    u = st.norm.cdf(z)
    hit = np.ones(TD_SIMS, dtype=bool)
    for i, stat in enumerate(("points", "rebounds", "assists")):
        hit &= _nb_ppf(u[:, i], pr[stat], *pr["var"][stat]) >= 10
    return float(hit.mean())


def _nb_ppf(u, mu, a, b):
    """Quantiles of the count distribution used everywhere else (NB with
    Var = a*mu + b*mu^2, Poisson when not over-dispersed)."""
    from scipy import stats as st
    mu = max(float(mu), 0.05)
    var = a * mu + b * mu ** 2
    if var <= mu * 1.0001:
        return st.poisson.ppf(u, mu)
    n, q = mu ** 2 / (var - mu), mu / var
    return st.nbinom.ppf(u, n, q)


def _upcoming(con, date_s):
    rows = con.execute(
        """SELECT pp.player_id, pp.game_id, pp.status, p.name, p.team,
                  g.home_team, g.away_team, g.spread_home, g.total
           FROM probable_players pp
           JOIN players p ON p.player_id = pp.player_id
           JOIN games g ON g.game_id = pp.game_id
           WHERE pp.date = ?""", (date_s,)).fetchall()
    out, games = [], {}
    for r in rows:
        home = r["team"] == r["home_team"]
        out.append({"game_id": f"up-{r['game_id']}", "date": pd.Timestamp(date_s),
                    "season": 0, "playoff": 0, "team": r["team"],
                    "opp": r["away_team"] if home else r["home_team"], "home": int(home),
                    "player_id": str(r["player_id"]), "player": r["name"],
                    "starter": np.nan, "dnp": int((r["status"] or "") == "out"),
                    "minutes": np.nan})
        games[f"up-{r['game_id']}"] = {"game_id": f"up-{r['game_id']}", "date": pd.Timestamp(date_s),
                                       "season": 0, "playoff": 0, "home": r["home_team"],
                                       "away": r["away_team"], "home_score": np.nan,
                                       "away_score": np.nan,
                                       "spread_home": np.nan if r["spread_home"] is None else float(r["spread_home"]),
                                       "total": np.nan if r["total"] is None else float(r["total"])}
    up = pd.DataFrame(out)
    if len(up):
        up["played"] = 1 - up["dnp"]
    return up, pd.DataFrame(list(games.values()))


def run(con, date_s, history_root=None):
    """Train on history, project tonight, write projections. -> log dict."""
    root = Path(history_root or HISTORY_ROOT)
    if not root.exists() or not any(p.name.isdigit() for p in root.iterdir()):
        return {"skipped": "no history/ folder"}
    up, up_games = _upcoming(con, date_s)
    if up.empty:
        return {"skipped": "no slate"}
    box, games, _ = F.load_history(root)
    box = box[box["date"] < pd.Timestamp(date_s)]
    games = games[games["date"] < pd.Timestamp(date_s)]
    df = F.build(box, pd.concat([games, up_games], ignore_index=True), upcoming=up)
    hist, tonight = df[df["game_id"].str.startswith("up-") == False], df[df["game_id"].str.startswith("up-")]  # noqa: E712
    model = PRAModel().fit(hist)
    tonight = tonight[tonight["played"] == 1]
    pred = model.predict(tonight)
    PROJ.clear()
    written = 0
    for idx, r in tonight.iterrows():
        pid = str(r["player_id"])
        gid = r["game_id"][3:]
        p = pred.loc[idx]
        PROJ[pid] = {"points": p["points_proj"], "rebounds": p["rebounds_proj"],
                     "assists": p["assists_proj"], "pra": p["pra_proj"],
                     "minutes": p["minutes_proj"], "var": dict(model.var),
                     "has_history": bool(pd.notna(r.get("minutes_ewm_l")))}
        for stat in ("points", "rebounds", "assists", "pra", "minutes"):
            con.execute(
                """INSERT INTO projections (game_id, player_id, date, stat, point, generated_at)
                   VALUES (?,?,?,?,?, datetime('now'))
                   ON CONFLICT(game_id, player_id, stat) DO UPDATE SET point=excluded.point,
                     generated_at=excluded.generated_at""",
                (gid, pid, date_s, stat, round(float(p[f"{stat}_proj"]), 1)))
            written += 1
    return {"model": "lightgbm", "trained_on": int((hist["played"] == 1).sum()),
            "projected_players": len(PROJ), "rows_written": written}
