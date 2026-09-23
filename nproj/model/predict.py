"""Points/rebounds/assists projection model.

STATUS: real phase-1 baseline, not the planned LightGBM model yet. This is
deliberately simple — a recency-weighted rolling average with a normal-
approximation quantile band — so the pipeline (db -> model -> site export)
is real and working end to end before investing in the fuller model
described below. It only produces a projection for a player once there are
real game logs for them in player_game_logs, which nproj/pipeline.py loads
from ESPN (see nproj/ingest/espn.py).

Planned upgrade path (see the planning doc's Model approach section): a
LightGBM point estimate + quantile models per stat, same family as the K
Board's kproj/model/, trained on a full backfilled game-log history. Feature
candidates to add, roughly in order of expected importance:
  - projected minutes (biggest NBA-specific variance driver — no MLB
    analogue; may need to be its own small model rather than one feature)
  - season-long baseline rate, to regress small samples toward
  - opponent defensive rating / pace
  - usage rate and role stability
  - home/away, rest days, back-to-back
  - blowout risk (spread magnitude)
The baseline below covers none of that — it's a placeholder good enough to
prove the plumbing, not a model to trust for real edges yet.
"""
import json
import math
from statistics import NormalDist

from .. import config

# Recency weighting: most recent game weighted highest, decaying by this
# factor per game further back. 0.9 means the 10th-most-recent game still
# carries ~35% as much weight as the most recent one.
RECENCY_DECAY = 0.9
MIN_GAMES_FOR_MODEL = 3
MAX_GAMES_CONSIDERED = 20

_Z = {q: NormalDist().inv_cdf(q) for q in config.QUANTILES}


def _weighted_stats(values):
    """values: newest-first list of numbers. Returns (weighted_mean, std)."""
    values = values[:MAX_GAMES_CONSIDERED]
    weights = [RECENCY_DECAY ** i for i in range(len(values))]
    wsum = sum(weights)
    mean = sum(v * w for v, w in zip(values, weights)) / wsum
    if len(values) < 2:
        # Not enough games for a real spread estimate — use a conservative
        # placeholder relative to the mean rather than claiming zero variance.
        std = max(mean * 0.25, 1.0)
    else:
        var = sum(w * (v - mean) ** 2 for v, w in zip(values, weights)) / wsum
        std = math.sqrt(var) if var > 0 else max(mean * 0.1, 0.5)
    return mean, std


def _player_game_log(con, player_id, stat, before_date=None):
    q = f"""SELECT {stat} AS v FROM player_game_logs
            WHERE player_id = ? AND {stat} IS NOT NULL
            {"AND date < ?" if before_date else ""}
            ORDER BY date DESC LIMIT ?"""
    params = [player_id] + ([before_date] if before_date else []) + [MAX_GAMES_CONSIDERED]
    return [r["v"] for r in con.execute(q, params).fetchall()]


def train(con, before_date: str | None = None):
    """Compute and cache each tracked player's per-stat (mean, std) from
    their game log so far, stored in kv as model:<player_id>:<stat>. Cheap
    enough to just recompute from scratch each run rather than persisting a
    real trained model artifact — this is descriptive stats, not ML.

    before_date (YYYY-MM-DD) ignores games on or after that date, so a test
    run for a past slate doesn't peek at the games it's projecting."""
    from .. import db

    player_ids = [r["player_id"] for r in con.execute("SELECT DISTINCT player_id FROM player_game_logs")]
    trained = 0
    for pid in player_ids:
        for stat in config.TARGET_STATS:
            values = _player_game_log(con, pid, stat, before_date)
            if len(values) < MIN_GAMES_FOR_MODEL:
                continue
            mean, std = _weighted_stats(values)
            db.set_kv(con, f"model:{pid}:{stat}", json.dumps({"mean": mean, "std": std, "n": len(values)}))
            trained += 1
    return trained


def project_player_stat(con, player_id, stat):
    """Point estimate + p10-p90 band for one player/stat, or None if there's
    no cached model for them (not enough game log yet)."""
    from .. import db

    raw = db.get_kv(con, f"model:{player_id}:{stat}")
    if not raw:
        return None
    m = json.loads(raw)
    mean, std = m["mean"], m["std"]
    return {
        "point": round(mean, 1),
        **{f"p{int(q * 100)}": round(max(0.0, mean + _Z[q] * std), 1) for q in config.QUANTILES},
    }


def project_date(con, date_s: str):
    """Write projections for every probable player on date_s into the
    projections table, for whichever stats have a cached model."""
    from .. import db

    rows = con.execute(
        "SELECT player_id, game_id FROM probable_players WHERE date = ?", (date_s,)
    ).fetchall()
    written = 0
    for r in rows:
        for stat in config.TARGET_STATS:
            proj = project_player_stat(con, r["player_id"], stat)
            if not proj:
                continue
            con.execute(
                """INSERT INTO projections
                   (game_id, player_id, date, stat, point, p10, p25, p50, p75, p90, generated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?, datetime('now'))
                   ON CONFLICT(game_id, player_id, stat) DO UPDATE SET
                     point=excluded.point, p10=excluded.p10, p25=excluded.p25,
                     p50=excluded.p50, p75=excluded.p75, p90=excluded.p90,
                     generated_at=excluded.generated_at""",
                (r["game_id"], r["player_id"], date_s, stat,
                 proj["point"], proj["p10"], proj["p25"], proj["p50"], proj["p75"], proj["p90"]),
            )
            written += 1
    return written


TD_WINDOW = 80   # games in the triple-double hit rate


def project_triple_double_prob(con, player_id, season_hit_rate=None, before_date=None):
    """Triple-double chance for the Jokic tracker: halfway between his hit
    rate over his last TD_WINDOW games (regular season and playoffs) and 50%.

    Tested 2026-09-23 on 2024-25 and 2025-26 (scripts/td_backtest.py): no
    method predicted his nightly triple-doubles better than a coin flip. The
    PRA model's rebounds/assists projections drawn together (a copula,
    nproj/model/live.td_probability) came out about 10 points too low, and
    the old 60/40 blend of season rate and last-20 rate chased hot and cold
    streaks and called only 41% of 2025-26 games right. His raw 80-game rate
    ran about 5 points low both seasons (his rate kept rising); pulled halfway
    to 50% it scored like a coin flip (Brier 0.2514 both seasons) with its
    average within a few points of the truth. So that's the number shown, and
    the yes/no call is made against the sportsbook price (site_export).
    """
    rows = con.execute(
        """SELECT points, rebounds, assists FROM player_game_logs
           WHERE player_id = ? AND (? IS NULL OR date < ?) ORDER BY date DESC LIMIT ?""",
        (player_id, before_date, before_date, TD_WINDOW),
    ).fetchall()
    if len(rows) < 20:
        return season_hit_rate
    hits = sum(1 for r in rows if r["points"] >= 10 and r["rebounds"] >= 10 and r["assists"] >= 10)
    return round((hits / len(rows) + 0.5) / 2, 3)
