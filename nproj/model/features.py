"""Feature building for the PRA model, from one box-score table.

The same code builds features for training/backtesting (history/<season>/
box.csv.gz) and for tonight's slate, so the live model sees exactly what it
was tested on. Every feature for a game uses only games played BEFORE that
game's date (pandas merge_asof with allow_exact_matches=False).

Feature groups (see FEATURES at the bottom):
  player form     exponentially weighted averages over his recent games of
                  minutes, points, rebounds, assists, and the shot/usage
                  numbers behind them (FGA, 3PA, FTA, offensive/defensive
                  boards), per game and per minute, short and long memory
  role            how often he's started lately, games played this season
  schedule        days of rest, back-to-back, home/away
  game context    the betting spread and total for this game, and the
                  team's implied points (total and spread combined), which
                  covers pace, scoring environment and blowout risk
  opponent        what this opponent has been allowing per game in points,
                  rebounds, assists and shot attempts
  teammates out   minutes and shots normally taken by teammates who are
                  sitting tonight (injury/rest), which is where usage bumps
                  come from
"""
from pathlib import Path

import numpy as np
import pandas as pd

STATS = ["points", "rebounds", "assists"]
SHOT = ["fga", "fg3a", "fta", "oreb", "dreb", "tov"]
HL_SHORT, HL_LONG = 3, 12          # halflives in games for the recent-form averages


# ------------------------------------------------------------------ load ----
def load_history(root="history", seasons=None):
    root = Path(root)
    seasons = seasons or sorted(int(p.name) for p in root.iterdir()
                                if p.name.isdigit() and (p / "box.csv.gz").exists())
    box, games, props = [], [], []
    for s in seasons:
        box.append(pd.read_csv(root / str(s) / "box.csv.gz", dtype={"game_id": str, "player_id": str}))
        games.append(pd.read_csv(root / str(s) / "games.csv.gz", dtype={"game_id": str}))
        p = root / str(s) / "props.csv.gz"
        if p.exists():
            props.append(pd.read_csv(p, dtype={"game_id": str, "player_id": str}))
    box = pd.concat(box, ignore_index=True)
    games = pd.concat(games, ignore_index=True)
    props = pd.concat(props, ignore_index=True) if props else pd.DataFrame()
    for df in (box, games, props):
        if len(df):
            df["date"] = pd.to_datetime(df["date"])
    return box, games, props


# ------------------------------------------------------------ player form ----
def _player_form(played):
    """Rolling form per player, INCLUDING each row's game. Joined later with
    strict 'before this date' matching, so a game never sees itself."""
    p = played.sort_values(["player_id", "date"]).copy()
    g = p.groupby("player_id", sort=False)
    out = p[["player_id", "date", "season"]].copy()
    cols = ["minutes"] + STATS + SHOT
    for hl, tag in ((HL_SHORT, "s"), (HL_LONG, "l")):
        for c in cols:
            out[f"{c}_ewm_{tag}"] = g[c].transform(lambda x, hl=hl: x.ewm(halflife=hl).mean())
    for c in STATS + ["fga", "fta", "fg3a", "oreb", "dreb"]:
        out[f"{c}_pm_l"] = out[f"{c}_ewm_l"] / out["minutes_ewm_l"].clip(lower=1)
        out[f"{c}_pm_s"] = out[f"{c}_ewm_s"] / out["minutes_ewm_s"].clip(lower=1)
    out["pra_ewm_s"] = out[[f"{c}_ewm_s" for c in STATS]].sum(axis=1)
    out["pra_ewm_l"] = out[[f"{c}_ewm_l" for c in STATS]].sum(axis=1)
    out["starter_rate"] = g["starter"].transform(lambda x: x.ewm(halflife=5).mean())
    out["starter_last"] = p["starter"].values
    out["minutes_last"] = p["minutes"].values
    out["minutes_sd"] = g["minutes"].transform(lambda x: x.rolling(10, min_periods=3).std())
    out["games_played"] = g.cumcount() + 1
    out["games_played_season"] = p.groupby(["player_id", "season"]).cumcount().values + 1
    # the site's current method, kept as the baseline to beat: weighted
    # average of the last 20 games, weight 0.9^k
    for c in STATS:
        out[f"{c}_baseline"] = g[c].transform(_decay_avg)
    return out


def _decay_avg(x, n=20, r=0.9):
    v = x.to_numpy(dtype=float)
    w = r ** np.arange(n)
    res = np.full(len(v), np.nan)
    for i in range(len(v)):
        seg = v[max(0, i - n + 1): i + 1][::-1]
        ww = w[: len(seg)]
        res[i] = (seg * ww).sum() / ww.sum()
    return pd.Series(res, index=x.index)


def _asof(left, right, by, cols):
    """For each left row, the latest right row for the same key strictly
    before left.date."""
    l = left.reset_index().sort_values("date")
    r = right.sort_values("date")[[by, "date"] + cols]
    m = pd.merge_asof(l, r, on="date", by=by, allow_exact_matches=False, direction="backward")
    return m.set_index("index").sort_index()[cols]


# ---------------------------------------------------------- team context ----
def _team_games(games):
    """One row per team per game with schedule and betting context."""
    games = games.copy()
    bad = ~games["total"].between(180, 280) | (games["spread_home"].abs() > 25)
    games.loc[bad, ["total", "spread_home"]] = np.nan   # a few feeds carry half-game or junk lines
    home = games.assign(team=games.home, opp=games.away, home_flag=1,
                        spread_team=games.spread_home)
    away = games.assign(team=games.away, opp=games.home, home_flag=0,
                        spread_team=-games.spread_home)
    tg = pd.concat([home, away], ignore_index=True)[
        ["game_id", "date", "season", "team", "opp", "home_flag", "spread_team", "total"]]
    tg = tg.sort_values(["team", "date"])
    tg["rest_days"] = tg.groupby("team")["date"].diff().dt.days.clip(upper=7)
    tg["b2b"] = (tg["rest_days"] == 1).astype(int)
    tg["implied_team_pts"] = (tg["total"] - tg["spread_team"]) / 2
    tg["abs_spread"] = tg["spread_team"].abs()
    return tg


def _opp_allowed(played, games):
    """What each team has allowed per game, as a rolling average (including
    that game; joined strictly-before like everything else)."""
    agg = played.groupby(["game_id", "opp"], as_index=False)[STATS + ["fga", "fta", "fg3a", "oreb"]].sum()
    agg = agg.merge(games[["game_id", "date"]], on="game_id").rename(columns={"opp": "team"})
    agg = agg.sort_values(["team", "date"])
    g = agg.groupby("team")
    out = agg[["team", "date"]].copy()
    for c in STATS + ["fga", "fta", "fg3a", "oreb"]:
        out[f"opp_allowed_{c}"] = g[c].transform(lambda x: x.ewm(halflife=10).mean())
    return out


# ----------------------------------------------------------------- build ----
def build(box, games, upcoming=None):
    """-> DataFrame, one row per player-game in `box` (played or not), with
    pre-game features and the actual outcome columns.

    upcoming: optional rows for games not played yet (same columns as box,
    stats empty, `played` preset to 1 unless the player is ruled out). They
    get features from `box` only and are returned at the end of the frame;
    their games (with spread/total) must be included in `games`."""
    box = box.copy()
    box["played"] = ((box["dnp"] == 0) & (box["minutes"].fillna(0) > 0)).astype(int)
    played = box[box["played"] == 1].copy()
    for c in STATS + SHOT:
        played[c] = played[c].fillna(0)

    form = _player_form(played)
    fcols = [c for c in form.columns if c not in ("player_id", "date", "season")]
    if upcoming is not None and len(upcoming):
        box = pd.concat([box, upcoming], ignore_index=True)
    df = box.join(_asof(box, form, "player_id", fcols))

    tg = _team_games(games)
    df = df.merge(tg.drop(columns=["season", "date"]).rename(columns={"opp": "opp_tg"}),
                  on=["game_id", "team"], how="left")

    allowed = _opp_allowed(played, games)
    acols = [c for c in allowed.columns if c.startswith("opp_allowed_")]
    tmp = df[["opp", "date"]].rename(columns={"opp": "team"})
    df = df.join(_asof(tmp, allowed, "team", acols))

    # teammates sitting tonight: their usual minutes / shots / points
    out_rows = df[df["played"] == 0]
    miss = out_rows.groupby(["game_id", "team"]).agg(
        out_minutes=("minutes_ewm_l", lambda x: x.fillna(0).sum()),
        out_fga=("fga_ewm_l", lambda x: x.fillna(0).sum()),
        out_points=("points_ewm_l", lambda x: x.fillna(0).sum()),
        out_assists=("assists_ewm_l", lambda x: x.fillna(0).sum()),
        out_rebounds=("rebounds_ewm_l", lambda x: x.fillna(0).sum()),
    ).reset_index()
    df = df.merge(miss, on=["game_id", "team"], how="left")
    for c in ("out_minutes", "out_fga", "out_points", "out_assists", "out_rebounds"):
        df[c] = df[c].fillna(0)
    df["month"] = df["date"].dt.month
    df["pra"] = df[STATS].sum(axis=1, min_count=3)
    return df


FEATURES = (
    [f"{c}_ewm_{t}" for c in ["minutes"] + STATS + SHOT for t in ("s", "l")]
    + [f"{c}_pm_{t}" for c in STATS + ["fga", "fta", "fg3a", "oreb", "dreb"] for t in ("s", "l")]
    + ["pra_ewm_s", "pra_ewm_l", "starter_rate", "starter_last", "minutes_last", "minutes_sd",
       "games_played", "games_played_season",
       "home_flag", "rest_days", "b2b", "spread_team", "abs_spread", "total", "implied_team_pts",
       "opp_allowed_points", "opp_allowed_rebounds", "opp_allowed_assists", "opp_allowed_fga",
       "opp_allowed_fta", "opp_allowed_fg3a", "opp_allowed_oreb",
       "out_minutes", "out_fga", "out_points", "out_assists", "out_rebounds", "month"]
)
