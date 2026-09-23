"""Does anything predict Jokic's nightly triple-doubles better than a coin flip?

Walk-forward (every two months, the PRA model is retrained on games before
that block and projects the block blind), then for each of Jokic's games
compares these triple-double chances:
  coin     0.5 every night
  rate80   his TD rate over his last 80 games
  half     halfway between rate80 and 50% (what the site uses)
  blend    old site method: 0.6 * season-to-date rate + 0.4 * last-20 rate
  model    the PRA model's points/rebounds/assists projections drawn together
           (Gaussian copula, correlation measured on other TD-capable players
           from the PREVIOUS season; nproj/model/live.td_probability)
Scores: Brier (lower is better; a coin flip scores 0.25), log loss, and how
often the >= 50% call was right. Writes nproj/model/td_calibration.json (the
correlation) and reports/td_backtest.json.

First run (2026-09-23), 2025-26 season, 71 games, actual rate 49.3%:
  coin 0.2500 | half 0.2514 | rate80 0.2551 | blend 0.2656 (calls 41% right)
  | model 0.2576 (averaged 36%, about 10 points low: under-projects assists)
2024-25 (82 games, 43.0%): coin 0.2500 | half 0.2514 | rate80 0.2619 | blend 0.2596
Conclusion: nothing beat the coin flip, so the site shows `half` and makes
the call against the price. Re-run as seasons are added:
    python scripts/td_backtest.py        (about 20-40 minutes)
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nproj.model import features as F, pra_model as PM  # noqa: E402
from nproj.model.live import _nb_ppf  # noqa: E402

JOKIC = "3112335"
S = ("points", "rebounds", "assists")


def walk(df):
    PM.TARGETS = list(S)
    PM.PARAMS.update(n_estimators=200, learning_rate=0.08)
    out = []
    for start in pd.date_range("2024-11-01", df["date"].max(), freq="2MS"):
        end = start + pd.DateOffset(months=2)
        test = PM.trainable(df[(df["date"] >= start) & (df["date"] < end)])
        if test.empty:
            continue
        model = PM.PRAModel().fit(df[df["date"] < start])
        pr = model.predict(test).join(test[["player_id", "date", "season", "rebounds_ewm_l",
                                            "assists_ewm_l", *S]])
        for s in S:
            pr[f"{s}_a"], pr[f"{s}_b"] = model.var[s]
        out.append(pr[(pr.rebounds_ewm_l >= 6) & (pr.assists_ewm_l >= 5)])
        print(start.date(), len(out[-1]), flush=True)
    return pd.concat(out)


def normal_scores(P, rng):
    for s in S:
        mu = np.maximum(P[f"{s}_proj"].to_numpy(), .05)
        var = P[f"{s}_a"].to_numpy() * mu + P[f"{s}_b"].to_numpy() * mu ** 2
        n, q = mu ** 2 / np.maximum(var - mu, 1e-9), mu / var
        hi, lo = st.nbinom.cdf(P[s], n, q), st.nbinom.cdf(P[s] - 1, n, q)
        P[f"{s}_z"] = st.norm.ppf(np.clip(lo + rng.random(len(P)) * (hi - lo), 1e-6, 1 - 1e-6))
    return P


def main():
    rng = np.random.default_rng(1)
    box, games, _ = F.load_history(ROOT / "history")
    P = normal_scores(walk(F.build(box, games)), rng)
    corr = {s: np.corrcoef(P[P.season == s][[f"{k}_z" for k in S]].T.to_numpy()).tolist()
            for s in sorted(P.season.unique())}
    jb = box[(box.player_id == JOKIC) & (box.dnp == 0) & (box.minutes > 0)].sort_values("date").copy()
    jb["td"] = ((jb.points >= 10) & (jb.rebounds >= 10) & (jb.assists >= 10)).astype(int)
    J = P[P.player_id == JOKIC].sort_values("date").copy()
    J["td"] = ((J.points >= 10) & (J.rebounds >= 10) & (J.assists >= 10)).astype(int)
    rows = []
    for _, r in J.iterrows():
        prev = jb[jb.date < r.date]
        cur = prev[prev.season == r.season]
        seas = cur.td.mean() if len(cur) >= 10 else prev[prev.season >= r.season - 1].td.mean()
        C = corr.get(r.season - 1)
        if C is None:
            model_p = np.nan
        else:
            Z = rng.multivariate_normal(np.zeros(3), np.array(C), size=20000)
            u = st.norm.cdf(Z)
            hit = np.ones(len(u), bool)
            for i, s in enumerate(S):
                hit &= _nb_ppf(u[:, i], r[f"{s}_proj"], r[f"{s}_a"], r[f"{s}_b"]) >= 10
            model_p = hit.mean()
        rows.append({"season": r.season, "td": r.td, "coin": .5, "rate80": prev.td.tail(80).mean(),
                     "half": (prev.td.tail(80).mean() + .5) / 2,
                     "blend": .6 * seas + .4 * prev.td.tail(20).mean(), "model": model_p})
    R = pd.DataFrame(rows)
    report = {}
    for season, x in R.groupby("season"):
        rep = {"games": len(x), "actual_rate": round(x.td.mean(), 3)}
        for m in ("coin", "half", "rate80", "blend", "model"):
            p = x[m].dropna().clip(1e-3, 1 - 1e-3)
            y = x.loc[p.index, "td"]
            if len(p):
                rep[m] = {"mean": round(p.mean(), 3), "brier": round(((p - y) ** 2).mean(), 4),
                          "logloss": round(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean(), 4),
                          "calls_right": round(((p >= .5) == y).mean(), 3)}
        report[str(season)] = rep
    print(json.dumps(report, indent=1))
    latest = corr[max(corr)]
    (ROOT / "nproj" / "model" / "td_calibration.json").write_text(json.dumps(
        {"corr": latest, "fitted_on_season": int(max(corr)),
         "note": "points/rebounds/assists correlation (normal scores of model residuals) for "
                 "triple-double-capable players; used by live.td_probability"}, indent=1) + "\n")
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "td_backtest.json").write_text(json.dumps(report, indent=1) + "\n")


if __name__ == "__main__":
    main()
