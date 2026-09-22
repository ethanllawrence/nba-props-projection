"""The Odds API ingest — NBA player points props.

STATUS: stub, but the budget-gating shape is intentionally sketched out now
because it's the tightest constraint on this project (see the planning
doc's Data sources section): player props are billed PER EVENT
(markets x regions x games), not in one bulk call the way MLB game lines
are, so an un-gated daily pull across a full 8-13 game slate can burn
through a meaningful chunk of the shared 500-credit/month budget fast.

Reuses the same once-per-day-per-window KV-flag gating pattern as the K
Board's kproj/cli.py cmd_daily (_due() there) — copy that pattern into
nproj/cli.py once this is wired up, rather than re-deriving it.
"""
from .. import config, util


def fetch_points_props(game_ids):
    """Placeholder. Real implementation: GET
    {ODDS_API_BASE}/sports/{ODDS_SPORT_KEY}/events/{event_id}/odds
    per game_id, market=player_points, store into nproj.db.prop_lines.

    Before wiring this up for real: confirm actual per-slate credit cost
    against a live NBA schedule (game count varies 3-13+ a night) and set
    ODDS_MONTHLY_BUDGET / ODDS_BUDGET_FLOOR accordingly — the K Board's
    500-credit budget is currently unshared with any other project, but
    per the planning doc, MLB season ending means it should have headroom
    once NBA takes over as the only active consumer.
    """
    raise NotImplementedError("odds.fetch_points_props: wire up The Odds API here")
