"""The PRA projection model: gradient-boosted trees (LightGBM) on the
features in nproj/model/features.py, one model per stat (points, rebounds,
assists, PRA, plus minutes for display), and a count distribution around
each projection so the site can say how likely an over is, not just give a
number.

Distribution: projections are averages of whole-number outcomes with more
spread than a Poisson. Each stat gets a variance curve Var = a*mean +
b*mean^2 fitted on held-out games (never the games the model trained on),
and P(over a line) comes from a negative binomial with that mean and
variance. The backtest checks these probabilities for calibration.
"""
import numpy as np
import pandas as pd
from scipy import stats as st

import os

from .features import FEATURES as _ALL

# NPROJ_DROP_FEATURES=out_ (comma list of prefixes) removes feature groups,
# used by the backtest to measure how much each group matters.
_DROP = [p for p in os.environ.get("NPROJ_DROP_FEATURES", "").split(",") if p]
FEATURES = [f for f in _ALL if not any(f.startswith(p) for p in _DROP)]

TARGETS = ["points", "rebounds", "assists", "pra", "minutes"]
PARAMS = dict(objective="poisson", n_estimators=400, learning_rate=0.05, num_leaves=31,
              min_child_samples=60, subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
              reg_lambda=1.0, verbose=-1)


def _lgbm(target):
    import lightgbm as lgb
    p = dict(PARAMS)
    if target == "minutes":
        p["objective"] = "regression"
    return lgb.LGBMRegressor(**p)


def trainable(df):
    return df[(df["played"] == 1) & df["minutes_ewm_l"].notna()]


def fit_var_curve(mu, y):
    """Least-squares Var = a*mu + b*mu^2 on binned squared residuals."""
    d = pd.DataFrame({"mu": mu, "r2": (y - mu) ** 2})
    d["bin"] = pd.qcut(d["mu"], 20, duplicates="drop")
    g = d.groupby("bin", observed=True).agg(mu=("mu", "mean"), var=("r2", "mean"))
    X = np.c_[g["mu"], g["mu"] ** 2]
    coef, *_ = np.linalg.lstsq(X, g["var"].to_numpy(), rcond=None)
    a, b = float(max(coef[0], 1.0)), float(max(coef[1], 0.0))
    return a, b


def prob_over(mu, line, a, b):
    """P(X > line) for a negative binomial with mean mu, var a*mu + b*mu^2
    (Poisson when that's not over-dispersed). Integer lines: P(X > line)."""
    mu = np.maximum(np.asarray(mu, dtype=float), 0.05)
    var = a * mu + b * mu ** 2
    k = np.floor(np.asarray(line, dtype=float))
    over_disp = var > mu * 1.0001
    p = np.empty_like(mu)
    # NB parameterisation: n = mu^2/(var-mu), p = mu/var
    n = np.where(over_disp, mu ** 2 / np.maximum(var - mu, 1e-9), 1.0)
    q = np.where(over_disp, mu / var, 0.5)
    p[over_disp] = st.nbinom.sf(k[over_disp], n[over_disp], q[over_disp])
    p[~over_disp] = st.poisson.sf(k[~over_disp], mu[~over_disp])
    return p


class PRAModel:
    def __init__(self):
        self.models, self.var = {}, {}

    def fit(self, df, holdout_frac=0.2):
        """Fit on df (played rows). The variance curves come from a first
        pass that holds out the most recent `holdout_frac` of dates."""
        d = trainable(df).sort_values("date")
        cut = d["date"].quantile(1 - holdout_frac)
        early, late = d[d["date"] < cut], d[d["date"] >= cut]
        for t in TARGETS:
            y = d[t].astype(float)
            ok = y.notna()
            if t != "minutes":
                m0 = _lgbm(t).fit(early.loc[early[t].notna(), FEATURES], early.loc[early[t].notna(), t])
                lm = late[late[t].notna()]
                self.var[t] = fit_var_curve(m0.predict(lm[FEATURES]), lm[t].to_numpy(float))
            self.models[t] = _lgbm(t).fit(d.loc[ok, FEATURES], y[ok])
        return self

    def predict(self, df):
        out = pd.DataFrame(index=df.index)
        for t, m in self.models.items():
            out[f"{t}_proj"] = m.predict(df[FEATURES])
        return out

    def p_over(self, stat, mu, line):
        a, b = self.var[stat]
        return prob_over(mu, line, a, b)
