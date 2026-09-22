"""Points/rebounds/assists projection model.

STATUS: real phase-1 baseline, not the planned LightGBM model yet. This is
deliberately simple — a recency-weighted rolling average with a normal-
approximation quantile band — so the pipeline (db -> model -> site export)
is real and working end to end before investing in the fuller model
described below. It only produces a projection for a player once there are
real game logs for them in player_game_logs; see scripts/seed_jokic.py for
how those get seeded today (nba_api ingest is blocked from this sandbox —
see nproj/ingest/nba_stats.py's docstring).

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


def train(con, quick: bool = False):
    """Compute and cache each tracked player's per-stat (mean, std) from
    their game log so far, stored in kv as model:<player_id>:<stat>. Cheap
    enough to just recompute from scratch each run rather than persisting a
    real trained model artifact — this is descriptive stats, not ML."""
    from .. import db

    player_ids = [r["player_id"] for r in con.execute("SELECT DISTINCT player_id FROM player_game_logs")]
    trained = 0
    for pid in player_ids:
        for stat in config.TARGET_STATS:
            values = _player_game_log(con, pid, stat)
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


def project_triple_double_prob(con, player_id, season_hit_rate=None):
    """Rough triple-double probability for the Jokic tracker: blends the
    season-long empirical hit rate (stable, but slow to react) with the
    empirical hit rate over the player's last games actually in the db
    (reactive, but noisy over a small sample) — NOT a real joint model.

    A real version needs the joint distribution across points/rebounds/
    assists (they're correlated — a big-assist game often means a lower-
    usage, lower-scoring game for a ball-dominant player), not three
    independent marginals multiplied together, let alone this blend. See
    the planning doc and docs/methodology.html for why that's a separate,
    harder modeling problem than the points/rebounds/assists baseline above.
    """
    rows = con.execute(
        """SELECT points, rebounds, assists FROM player_game_logs
           WHERE player_id = ? ORDER BY date DESC LIMIT ?""",
        (player_id, MAX_GAMES_CONSIDERED),
    ).fetchall()
    if not rows:
        return season_hit_rate

    recent_hits = sum(1 for r in rows if r["points"] >= 10 and r["rebounds"] >= 10 and r["assists"] >= 10)
    recent_rate = recent_hits / len(rows)

    if season_hit_rate is None:
        return round(recent_rate, 3)

    # Season rate is the larger, more stable sample — weight it higher.
    blended = 0.6 * season_hit_rate + 0.4 * recent_rate
    return round(blended, 3)
