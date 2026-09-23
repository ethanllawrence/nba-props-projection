#!/usr/bin/env python3
"""Walk-forward backtest of the PRA model against real sportsbook lines.

For each month from November 2024 on, train on every game BEFORE that month
(2023-24 onward), then project that month's games blind. Score the
projections three ways:
  1. accuracy: average miss (MAE) of the model vs. the site's old recent-
     average method vs. the sportsbook line itself;
  2. calibration: when the model says 60% over, does the over hit ~60%?
  3. betting: bet whichever side the model likes when its probability beats
     the price's break-even by a margin, graded at the real prices. Also the
     old site's rule (projection 6%+ off the line) for comparison.

Main line per player/stat/game: both sides priced, and of those, the line
whose two prices are closest to each other (the book's "fair" line).

Run: python scripts/backtest.py [history_dir] [out_dir]
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nproj.model import features as F  # noqa: E402
from nproj.model.pra_model import PRAModel, trainable  # noqa: E402

STATS = ["points", "rebounds", "assists", "pra"]


def implied(price):
    price = np.asarray(price, dtype=float)
    return np.where(price < 0, -price / (-price + 100), 100 / (price + 100))


def payout(price):
    price = np.asarray(price, dtype=float)
    return np.where(price < 0, 100 / -price, price / 100)


def main_lines(props):
    p = props.pivot_table(index=["game_id", "player_id", "stat", "line"], columns="side",
                          values="price", aggfunc="first").dropna().reset_index()
    p["gap"] = (implied(p["over"]) - implied(p["under"])).__abs__()
    p = p.sort_values("gap").drop_duplicates(["game_id", "player_id", "stat"])
    p["hold"] = implied(p["over"]) + implied(p["under"]) - 1
    p = p[p["hold"].between(0, 0.15)]          # drop obviously broken pairs
    return p.drop(columns="gap")


def run(hist, out_dir):
    box, games, props = F.load_history(hist)
    df = F.build(box, games)
    lines = main_lines(props)
    months = pd.period_range("2024-11", df["date"].max().to_period("M"), freq="M")
    preds = []
    for m in months:
        start, end = m.start_time, m.end_time
        test = trainable(df[(df["date"] >= start) & (df["date"] <= end)])
        if test.empty:
            continue
        model = PRAModel().fit(df[df["date"] < start])
        pr = model.predict(test)
        pr = pr.join(test[["game_id", "player_id", "player", "date", "season", "minutes"] + STATS
                          + [f"{c}_baseline" for c in F.STATS]])
        for s in STATS:
            pr[f"{s}_a"], pr[f"{s}_b"] = model.var[s]
        preds.append(pr)
        print(f"{m}: trained on {len(trainable(df[df['date'] < start])):,} games, "
              f"projected {len(test):,}", flush=True)
    P = pd.concat(preds)
    P["pra_baseline"] = P[[f"{c}_baseline" for c in F.STATS]].sum(axis=1)

    # long format: one row per player-game-stat with the line
    rows = []
    for s in STATS:
        x = P[["game_id", "player_id", "player", "date", "season", s, f"{s}_proj", f"{s}_baseline",
               f"{s}_a", f"{s}_b"]].rename(columns={s: "actual", f"{s}_proj": "proj",
                                                    f"{s}_baseline": "baseline",
                                                    f"{s}_a": "va", f"{s}_b": "vb"})
        x["stat"] = s
        rows.append(x)
    L = pd.concat(rows).merge(lines, on=["game_id", "player_id", "stat"], how="inner")
    from nproj.model.pra_model import prob_over
    L["p_over"] = prob_over(L["proj"], L["line"], L["va"].to_numpy(), L["vb"].to_numpy())
    L.to_pickle(Path(out_dir) / "backtest_rows.pkl")
    report = evaluate(L, P)
    (Path(out_dir) / "backtest_report.json").write_text(json.dumps(report, indent=2, default=float))
    return report, L


def _bets(L, side_over_mask, price_col_over="over", price_col_under="under"):
    """Grade bets: side_over_mask True=bet over, False=bet under, NaN=no bet."""
    b = L[side_over_mask.notna()].copy()
    over = side_over_mask[side_over_mask.notna()].astype(bool)
    price = np.where(over, b[price_col_over], b[price_col_under])
    push = b["actual"] == b["line"]
    win = np.where(over, b["actual"] > b["line"], b["actual"] < b["line"])
    profit = np.where(push, 0.0, np.where(win, payout(price), -1.0))
    n = int((~push).sum())
    return {"bets": n, "wins": int((win & ~push).sum()),
            "hit_rate": round(float((win & ~push).sum() / n), 4) if n else None,
            "roi": round(float(profit.sum() / max(len(b), 1)), 4),
            "units": round(float(profit.sum()), 1)}


def evaluate(L, P):
    rep = {"rows_with_lines": int(len(L)), "by_stat": {}, "betting": {}, "calibration": {}}
    for s, g in L.groupby("stat"):
        rep["by_stat"][s] = {
            "n": int(len(g)),
            "mae_model": round(float((g.proj - g.actual).abs().mean()), 3),
            "mae_old_method": round(float((g.baseline - g.actual).abs().mean()), 3),
            "mae_book_line": round(float((g.line - g.actual).abs().mean()), 3),
            "bias_model": round(float((g.proj - g.actual).mean()), 3),
        }
    # all player-games (not just those with lines)
    rep["all_games_mae"] = {s: {"model": round(float((P[f"{s}_proj"] - P[s]).abs().mean()), 3),
                                "old_method": round(float((P[f"{s}_baseline"] - P[s]).abs().mean()), 3)}
                            for s in STATS}
    # model betting at several margins
    be_over, be_under = implied(L["over"]), implied(L["under"])
    for margin in (0.0, 0.02, 0.04, 0.06, 0.08, 0.10):
        side = pd.Series(np.nan, index=L.index, dtype=object)
        eo = L["p_over"] - be_over
        eu = (1 - L["p_over"]) - be_under
        side[(eo > margin) & (eo >= eu)] = True
        side[(eu > margin) & (eu > eo)] = False
        rep["betting"][f"model_margin_{margin:.2f}"] = _bets(L, side)
        by = {}
        for s in STATS:
            msk = L["stat"] == s
            by[s] = _bets(L[msk], side[msk])
        rep["betting"][f"model_margin_{margin:.2f}"]["by_stat"] = by
    # the old site rule: baseline 6%+ off the line
    edge = (L["baseline"] - L["line"]) / L["line"]
    side = pd.Series(np.nan, index=L.index, dtype=object)
    side[edge >= 0.06] = True
    side[edge <= -0.06] = False
    rep["betting"]["old_site_rule_6pct"] = _bets(L, side)
    # same 6% rule but with the model's projection
    edge = (L["proj"] - L["line"]) / L["line"]
    side = pd.Series(np.nan, index=L.index, dtype=object)
    side[edge >= 0.06] = True
    side[edge <= -0.06] = False
    rep["betting"]["model_6pct_rule"] = _bets(L, side)
    # calibration of p_over
    c = L[L["actual"] != L["line"]].copy()
    c["bucket"] = pd.cut(c["p_over"], [0, .3, .4, .45, .5, .55, .6, .7, 1])
    cal = c.groupby("bucket", observed=True).agg(n=("p_over", "size"), predicted=("p_over", "mean"),
                                                 actual=("actual", lambda a: 0))
    cal["actual"] = c.groupby("bucket", observed=True).apply(
        lambda g: (g["actual"] > g["line"]).mean(), include_groups=False)
    rep["calibration"] = {str(k): {"n": int(v.n), "predicted": round(float(v.predicted), 3),
                                   "actual": round(float(v.actual), 3)} for k, v in cal.iterrows()}
    # by season
    rep["by_season_model_margin_0.04"] = {}
    for season, g in L.groupby("season"):
        eo = g["p_over"] - implied(g["over"])
        eu = (1 - g["p_over"]) - implied(g["under"])
        side = pd.Series(np.nan, index=g.index, dtype=object)
        side[(eo > .04) & (eo >= eu)] = True
        side[(eu > .04) & (eu > eo)] = False
        rep["by_season_model_margin_0.04"][int(season)] = _bets(g, side)
    return rep


if __name__ == "__main__":
    hist = sys.argv[1] if len(sys.argv) > 1 else "history"
    out = sys.argv[2] if len(sys.argv) > 2 else "."
    rep, _ = run(hist, out)
    print(json.dumps(rep, indent=2, default=float))
