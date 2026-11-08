"""3-pointers made (3PM) model, beta (Robin, 2026-11-08).

Built from what the research and our own data say about today's NBA:

1. ATTEMPTS are the stable part (2025-26 split-half r = 0.97 for 3PA per 36),
   so a LightGBM model (same features and training as the PRA model) projects
   each player's 3PA tonight: minutes and role, recent 3PA per minute (short
   and long memory), teammates out, pace/total/spread, the opponent's 3PA
   allowed (defenses control attempts much more than makes), and coming back
   from an absence (ABSENCE_FEATURES; walk-forward: first-3-games-back 3PA
   error 1.22 -> 1.19 and bias +0.17 -> +0.07 in 2025-26, 1.39 -> 1.31 and
   +0.38 -> +0.24 this season; overall 3PA error 1.485 -> 1.481).
2. MAKE RATE is mostly noise (3P% split-half r about 0.2-0.3), so each
   player's recent 3P% is pulled toward a baseline for his kind of shooter,
   as if he had PRIOR_ATTEMPTS extra attempts at that baseline. In 2023-26
   3P% took about 400-630 attempts to be half skill, half noise (an older
   2014 study said about 750); the backtest picks the value.
   Recent shots count more (DECAY_GAMES half-life), and the opponent's 3P%
   allowed is NOT used (it's mostly luck).
3. DISTRIBUTION: attempts are a negative binomial around the projection
   (variance curve fitted on held-out games, as for PRA), and each attempt
   goes in with the shrunk make rate, so 3PM = binomial thinning of 3PA.
   That gives the chance of 1+, 2+, 3+ ... threes, the way the books sell them.

Market blend (step 7) waits until a few weeks of 3PM lines are archived
(history/<season>/threes_props.csv.gz, nproj/ingest/espn_props.py).
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as st

from .pra_model import FEATURES, PARAMS, fit_var_curve, trainable

# Backtest (scripts/threes_backtest.py, binomial log-likelihood of makes given
# attempts): best on 2024-25 at 300 attempts and a 120-game half-life, and
# also best on 2025-26. 150-500 were within a hair; 800+ were clearly worse.
PRIOR_ATTEMPTS = 300        # shrinkage weight (attempts at the baseline rate)
DECAY_GAMES = 120           # half-life, in games, of a player's own shooting history
MAX_RUNG = 8


# Coming back from an absence (Nov 8): first games back often come with a
# minutes limit the player's normal form doesn't show. Built from the box
# scores alone (injured players are listed as DNP with a reason), so the
# backtest can test it: days since he last played, games missed in his
# team's last 10, games played since returning from a 2+ game absence (capped),
# and how long that absence was.
ABSENCE_FEATURES = ["days_since_played", "missed_last10", "games_back", "absence_len"]
import os as _os
USE_ABSENCE = _os.environ.get("NPROJ_3PM_NO_ABSENCE", "0") != "1"
FEATURES3 = FEATURES + (ABSENCE_FEATURES if USE_ABSENCE else [])


def absence_features(box):
    """Pre-game absence features for every row of box (box score rows plus,
    for tonight, upcoming rows with dnp=0/1 from the injury report).
    -> DataFrame [game_id, player_id] + ABSENCE_FEATURES."""
    b = box[["game_id", "player_id", "date", "dnp", "minutes"]].copy()
    b["date"] = pd.to_datetime(b["date"])
    b = b.sort_values(["player_id", "date", "game_id"]).reset_index(drop=True)
    played = ((b["dnp"].fillna(0) == 0) & (b["minutes"].fillna(1) > 0)).astype(int).to_numpy()
    upcoming = b["game_id"].astype(str).str.startswith("up-").to_numpy()
    played = np.where(upcoming, (b["dnp"].fillna(0) == 0).astype(int).to_numpy(), played)
    pid = b["player_id"].astype(str).to_numpy()
    dates = b["date"].to_numpy()
    n = len(b)
    days = np.full(n, 30.0)
    miss10 = np.zeros(n)
    back = np.full(n, 6.0)
    alen = np.zeros(n)
    last_pid, hist, last_play, streak, run, prev_run = None, [], None, 6, 0, 0
    for i in range(n):
        if pid[i] != last_pid:
            last_pid, hist, last_play, streak, run, prev_run = pid[i], [], None, 6, 0, 0
        if last_play is not None:
            days[i] = min((dates[i] - last_play) / np.timedelta64(1, "D"), 30.0)
        miss10[i] = sum(1 - x for x in hist[-10:])
        if run >= 2:                                  # tonight would be his first game back
            back[i], alen[i] = 0, min(run, 15)
        else:
            back[i], alen[i] = min(streak, 6), min(prev_run, 15)
        # update with this game's outcome (not for upcoming rows: nothing happened yet)
        if upcoming[i]:
            continue
        if played[i]:
            if run >= 2:
                streak, prev_run = 0, run                 # back from a 2+ game absence
            streak = min(streak + 1, 6)
            run = 0
            last_play = dates[i]
        else:
            run += 1
        hist.append(played[i])
    out = b[["game_id", "player_id"]].copy()
    out["days_since_played"] = days
    out["missed_last10"] = miss10
    out["games_back"] = back
    out["absence_len"] = alen
    return out


def add_absence(df, frame):
    """df (features frame) + ABSENCE_FEATURES computed on frame (box rows,
    plus upcoming rows for tonight)."""
    a = absence_features(frame)
    a["player_id"] = a["player_id"].astype(str)
    d = df.copy()
    d["_pid"] = d["player_id"].astype(str)
    d = d.merge(a.rename(columns={"player_id": "_pid"}), on=["game_id", "_pid"], how="left").drop(columns="_pid")
    d.index = df.index
    return d


def _lgbm():
    import lightgbm as lgb
    return lgb.LGBMRegressor(**PARAMS)          # poisson objective, like the PRA stats


def make_rates(box, prior=PRIOR_ATTEMPTS, decay=DECAY_GAMES):
    """Pre-game shrunk 3P% for every player-game in box (only earlier games
    count). -> DataFrame [game_id, player_id, p3, w_att, base]."""
    b = box[(box["dnp"] == 0) & (box["minutes"].fillna(0) > 0)].copy()
    b = b.sort_values(["player_id", "date", "game_id"])
    for c in ("fg3a", "fg3m", "minutes"):
        b[c] = b[c].fillna(0).astype(float)
    lam = 0.5 ** (1.0 / decay)
    g = b.groupby("player_id", sort=False)

    def ewsum(col):
        # exponentially weighted running sum of PREVIOUS games
        def f(x):
            out, acc = np.empty(len(x)), 0.0
            for i, v in enumerate(x.to_numpy()):
                out[i] = acc
                acc = acc * lam + v
            return pd.Series(out, index=x.index)
        return g[col].transform(f)

    b["w_att"] = ewsum("fg3a")
    b["w_made"] = ewsum("fg3m")
    b["w_min"] = ewsum("minutes")
    # shooter type = how often he shoots threes (attempts per 36 minutes)
    rate36 = np.where(b["w_min"] > 0, 36 * b["w_att"] / b["w_min"].clip(lower=1), np.nan)
    b["rate36"] = rate36
    base = _baseline(b)
    b["base"] = base
    b["p3"] = (b["w_made"] + prior * b["base"]) / (b["w_att"] + prior)
    return b[["game_id", "player_id", "p3", "w_att", "base", "rate36"]]


# league 3P% by shooter volume (3PA per 36), 2023-26: high-volume shooters make more
BASE_BINS = [0, 2, 4, 6, 8, 10, 99]


def _baseline(b):
    """Baseline 3P% for each row from its shooter-volume bucket, fitted on
    the rows' own earlier seasons would be ideal; the curve is very stable,
    so it's fitted once on everything before each row's season."""
    out = pd.Series(np.nan, index=b.index)
    seasons = sorted(b["season"].dropna().unique())
    for s in seasons:
        prev = b[(b["season"] < s) & (b["fg3a"] > 0)]
        if len(prev) < 2000:                     # first season: use all of it
            prev = b[b["fg3a"] > 0]
        bucket = pd.cut(prev["rate36"], BASE_BINS)
        curve = prev.groupby(bucket, observed=False).apply(
            lambda x: x["fg3m"].sum() / max(x["fg3a"].sum(), 1), include_groups=False)
        league = prev["fg3m"].sum() / max(prev["fg3a"].sum(), 1)
        rows = b["season"] == s
        cur = pd.cut(b.loc[rows, "rate36"], BASE_BINS)
        out.loc[rows] = cur.map(curve).astype(float).fillna(league).to_numpy()
    return out.fillna(0.36)


class ThreesModel:
    def __init__(self):
        self.model, self.var = None, (1.0, 0.0)

    def fit(self, df, holdout_frac=0.2):
        d = trainable(df).sort_values("date")
        d = d[d["fg3a"].notna()]
        cut = d["date"].quantile(1 - holdout_frac)
        early, late = d[d["date"] < cut], d[d["date"] >= cut]
        cols = [c for c in FEATURES3 if c in d.columns]
        self.cols = cols
        m0 = _lgbm().fit(early[cols], early["fg3a"].astype(float))
        self.var = fit_var_curve(m0.predict(late[cols]), late["fg3a"].to_numpy(float))
        self.model = _lgbm().fit(d[cols], d["fg3a"].astype(float))
        return self

    def predict_attempts(self, df):
        return np.maximum(self.model.predict(df[self.cols]), 0.0)


def rung_probs(mu_a, p3, var=(1.0, 0.0), max_rung=MAX_RUNG):
    """P(3PM >= k) for k = 1..max_rung, for arrays of projected attempts and
    make rates. Attempts ~ negative binomial (variance a*mu + b*mu^2), each
    attempt made with prob p3. -> array (n, max_rung)."""
    mu_a = np.maximum(np.asarray(mu_a, float), 0.02)
    p3 = np.clip(np.asarray(p3, float), 0.01, 0.95)
    a, b = var
    out = np.zeros((len(mu_a), max_rung))
    for i, (mu, p) in enumerate(zip(mu_a, p3)):
        top = int(mu + 8 * np.sqrt(a * mu + b * mu * mu) + 10)
        n_att = np.arange(top + 1)
        v = a * mu + b * mu * mu
        if v > mu * 1.0001:
            n = mu * mu / (v - mu)
            q = mu / v
            pa = st.nbinom.pmf(n_att, n, q)
        else:
            pa = st.poisson.pmf(n_att, mu)
        # P(M >= k) = sum_a P(A=a) * P(Bin(a, p) >= k)
        for k in range(1, max_rung + 1):
            out[i, k - 1] = float(np.sum(pa * st.binom.sf(k - 1, n_att, p)))
    return out


def mean_made(mu_a, p3):
    return np.asarray(mu_a, float) * np.asarray(p3, float)


# Rung calibration (like the star calibration for PRA): the raw chances ran a
# little high on 1+ and 2+ (more zero-make nights than the model expected).
# A logistic fit per rung on walk-forward chances (threes_calibration.json),
# fitted on 2024-25 and checked on 2025-26 and this season, fixed most of it.
CAL = json.loads((Path(__file__).parent / "threes_calibration.json").read_text())


def calibrate(rungs):
    """(n, k) raw P(3PM >= k) -> calibrated, still falling with k."""
    r = np.clip(np.asarray(rungs, float), 1e-4, 1 - 1e-4)
    out = np.empty_like(r)
    for j in range(r.shape[1]):
        c = CAL["rungs"].get(str(j + 1)) or CAL["rungs"][max(CAL["rungs"], key=int)]
        z = c["intercept"] + c["slope"] * np.log(r[:, j] / (1 - r[:, j]))
        out[:, j] = 1 / (1 + np.exp(-z))
    return np.minimum.accumulate(out, axis=1)


def current_rates(box, prior=PRIOR_ATTEMPTS, decay=DECAY_GAMES):
    """Shrunk 3P% for every player as of AFTER his last game in box (for
    tonight). -> {player_id: (p3, weighted attempts, baseline)}."""
    b = box[(box["dnp"] == 0) & (box["minutes"].fillna(0) > 0)].copy()
    b = b.sort_values(["player_id", "date", "game_id"])
    for c in ("fg3a", "fg3m", "minutes"):
        b[c] = b[c].fillna(0).astype(float)
    lam = 0.5 ** (1.0 / decay)
    acc = {}
    for pid, g in b.groupby("player_id", sort=False):
        wa = wm = wn = 0.0
        for a, m, n in zip(g["fg3a"].to_numpy(), g["fg3m"].to_numpy(), g["minutes"].to_numpy()):
            wa, wm, wn = wa * lam + a, wm * lam + m, wn * lam + n
        acc[str(pid)] = (wa, wm, wn, g["season"].iloc[-1])
    # baseline by shooter volume, from every season in box
    hist = make_rates(box, prior, decay)
    hist = hist.merge(b[["game_id", "player_id", "fg3a", "fg3m"]], on=["game_id", "player_id"])
    hist = hist[hist["fg3a"] > 0]
    bucket = pd.cut(hist["rate36"], BASE_BINS)
    curve = hist.groupby(bucket, observed=False).apply(
        lambda x: x["fg3m"].sum() / max(x["fg3a"].sum(), 1), include_groups=False)
    league = hist["fg3m"].sum() / max(hist["fg3a"].sum(), 1)
    out = {}
    for pid, (wa, wm, wn, _) in acc.items():
        rate = 36 * wa / max(wn, 1) if wn > 0 else np.nan
        base = league
        if rate == rate:
            iv = pd.cut([rate], BASE_BINS)[0]
            base = float(curve.get(iv, league)) if iv == iv else league
        out[pid] = ((wm + prior * base) / (wa + prior), wa, base)
    return out
