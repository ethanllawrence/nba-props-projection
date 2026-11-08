#!/usr/bin/env python3
"""Walk-forward backtest of the 3PM model (nproj/model/threes.py).

1. Make-rate shrinkage: for each PRIOR_ATTEMPTS / DECAY_GAMES pair, score how
   well the pre-game make rate predicts that game's makes GIVEN the attempts
   (binomial log-likelihood). Tuned on 2024-25, checked on 2025-26.
2. Full model: each month from Nov 2024 on, train the attempts model on every
   game before that month and project the month blind; combine with the
   make rate; score 3PA and 3PM error and the 1+ .. 5+ rung calibration.
   Reported for 2025-26 and for this season's Oct 20 - Nov 6 window, the
   window of KSplit's public diagnostics (3PM error 0.86, 3PA error 1.48),
   on "shooters" (projected 3PA >= SHOOTER_3PA, about the players who get a
   3PM line) and on everyone who played.

Run: python scripts/threes_backtest.py [history_dir] [out_json]
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nproj.model import features as F  # noqa: E402
from nproj.model import threes as T  # noqa: E402
from nproj.model.pra_model import trainable  # noqa: E402

SHOOTER_3PA = 3.0
BENCH = ("2026-10-20", "2026-11-06")


def shrink_grid(box):
    played = box[(box["dnp"] == 0) & (box["minutes"].fillna(0) > 0) & (box["fg3a"].fillna(0) > 0)]
    res = []
    for prior in (150, 300, 500, 800, 1200):
        for decay in (30, 60, 120):
            mr = T.make_rates(box, prior, decay).merge(
                played[["game_id", "player_id", "season", "fg3a", "fg3m"]], on=["game_id", "player_id"])
            mr = mr[mr["w_att"] > 0]
            ll = st.binom.logpmf(mr["fg3m"], mr["fg3a"], mr["p3"].clip(0.01, 0.95))
            for season in (2025, 2026):
                m = mr["season"] == season
                res.append({"prior": prior, "decay": decay, "season": int(season),
                            "loglik_per_attempt": float(ll[m].sum() / mr.loc[m, "fg3a"].sum())})
    return pd.DataFrame(res)


def run(hist, out):
    box, games, _ = F.load_history(hist)
    grid = shrink_grid(box)
    tune = grid[grid.season == 2025].sort_values("loglik_per_attempt", ascending=False).iloc[0]
    prior, decay = int(tune["prior"]), int(tune["decay"])
    print("shrinkage grid (best on 2024-25):", prior, decay, flush=True)
    df = F.build(box, games)
    if T.USE_ABSENCE:
        df = T.add_absence(df, box)
    mr = T.make_rates(box, prior, decay)
    months = pd.period_range("2024-11", df["date"].max().to_period("M"), freq="M")
    rows = []
    for mth in months:
        start, end = mth.start_time, mth.end_time
        test = trainable(df[(df["date"] >= start) & (df["date"] <= end)])
        if test.empty:
            continue
        model = T.ThreesModel().fit(df[df["date"] < start])
        keep = ["game_id", "player_id", "date", "season", "fg3a", "fg3m", "minutes"] + \
            [c for c in T.ABSENCE_FEATURES if c in test.columns]
        t = test[keep].copy()
        t["proj_3pa"] = model.predict_attempts(test)
        t = t.merge(mr[["game_id", "player_id", "p3"]], on=["game_id", "player_id"], how="left")
        t["p3"] = t["p3"].fillna(0.33)
        rp = T.rung_probs(t["proj_3pa"], t["p3"], model.var, 5)
        for k in range(1, 6):
            t[f"r{k}"] = rp[:, k - 1]
        t["proj_3pm"] = T.mean_made(t["proj_3pa"], t["p3"])
        rows.append(t)
        print(f"{mth}: {len(t)} player-games, var {tuple(round(v, 3) for v in model.var)}", flush=True)
    P = pd.concat(rows)
    P.to_pickle(Path(out).with_suffix(".pkl"))
    report = {"shrinkage": {"chosen_prior": prior, "chosen_decay": decay,
                            "grid": grid.to_dict("records")}}
    for label, sel in (("2025-26", P["season"] == 2026),
                       ("this season Oct 20 - Nov 6", (P["date"] >= BENCH[0]) & (P["date"] <= BENCH[1])),
                       ("this season to date", P["season"] == 2027)):
        for pop, m in (("shooters (proj 3PA >= 3)", P["proj_3pa"] >= SHOOTER_3PA), ("everyone who played", True)):
            x = P[sel & m]
            if x.empty:
                continue
            cal = {}
            for k in range(1, 6):
                b = pd.cut(x[f"r{k}"], [0, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1])
                g = x.groupby(b, observed=True).apply(
                    lambda y: pd.Series({"n": len(y), "said": y[f"r{k}"].mean(), "hit": (y["fg3m"] >= k).mean()}),
                    include_groups=False)
                cal[f"{k}+"] = {"said": round(float(x[f"r{k}"].mean()), 4), "hit": round(float((x["fg3m"] >= k).mean()), 4),
                                "buckets": {str(i): {kk: round(float(v), 4) for kk, v in r.items()} for i, r in g.iterrows()}}
            report.setdefault(label, {})[pop] = {
                "player_games": int(len(x)), "per_day": round(len(x) / max(x["date"].nunique(), 1), 1),
                "mae_3pm": round(float((x["proj_3pm"] - x["fg3m"]).abs().mean()), 3),
                "bias_3pm": round(float((x["proj_3pm"] - x["fg3m"]).mean()), 3),
                "mae_3pa": round(float((x["proj_3pa"] - x["fg3a"]).abs().mean()), 3),
                "bias_3pa": round(float((x["proj_3pa"] - x["fg3a"]).mean()), 3),
                "naive_mae_3pm_recent_avg": None,
                "rungs": cal}
    Path(out).write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    hist = sys.argv[1] if len(sys.argv) > 1 else "history"
    out = sys.argv[2] if len(sys.argv) > 2 else "threes_backtest.json"
    rep = run(hist, out)
    for label in [k for k in rep if k != "shrinkage"]:
        for pop, r in rep[label].items():
            print(label, "|", pop, {k: r[k] for k in ("player_games", "per_day", "mae_3pm", "bias_3pm", "mae_3pa", "bias_3pa")})
            print("   rungs", {k: (v["said"], v["hit"]) for k, v in r["rungs"].items()})
