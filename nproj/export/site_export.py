"""Regenerate docs/data/today.json from the database.

STATUS: stub. Real implementation replaces the hand-written mockup at
docs/data/today.json with a live export in the same shape (see
docs/assets/app.js for the exact fields the site reads: player, team, opp,
home, time_et, proj{point,p10,p25,p50,p75,p90,minutes_tier,
minutes_confidence}, pts_line{line,books,fetched_at}, edges[]).
"""
import json

from .. import config, util


def export_all(con, date_s: str):
    raise NotImplementedError(
        "export.site_export.export_all: query projections+prop_lines, "
        "compute edges (de-vig via util.devig_two_way + Kelly), and write "
        "docs/data/today.json in the shape docs/assets/app.js expects"
    )
