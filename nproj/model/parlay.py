"""Parlay of the Day — pick 3-4 high-conviction alt-line legs for a combined
~+200 to +300 parlay, and track whether each day's parlay hit.

STATUS: stub. docs/data/parlay.json is hand-curated today (see its "note"
field) — this module is where that selection becomes automatic once there's
real projection data (and real alt lines) to select from.

This is NOT an expected-value optimizer like the Today board's edge ranking.
The goal is conviction, not EV: legs where the model clears not just the
full book line but a shaded alt line by a wide margin, with low variance
risk around minutes. Planned selection criteria, roughly in priority order:

  1. Alt-line cushion: projection must clear the alt line by a meaningfully
     larger margin than it clears the full book line (the same relative
     threshold idea as the Today board's edge coloring, just a stricter bar
     — this is "high conviction", not "positive EV").
  2. Minutes stability: exclude anyone on a minutes restriction, a
     questionable/probable injury tag, or in a role that's changed
     recently (a new starter with an unstable sample, a bench player whose
     minutes swing with matchup).
  3. Blowout risk: a big projected point spread raises the odds a starter
     sits the 4th quarter, capping their counting stats regardless of how
     well the model liked the matchup. Prefer competitive games, or
     players whose minutes are historically blowout-resistant (Jokic-style
     workhorses) when the game does look lopsided.
  4. Combine 3-4 legs' American odds (see combined_odds() below) and only
     surface the day's parlay if it lands close to the ~+200 to +300 target
     range — a stricter conviction bar naturally produces shorter odds, so
     this is as much a sanity check on step 1's threshold as a hard filter.
  5. Diversify legs across players/games where possible, so one bad
     matchup read doesn't sink every leg at once.

None of this is wired up yet — it needs real per-player projections (not
just Jokic) and real alt-line odds (The Odds API, not yet integrated for
alt lines specifically) before there's anything real to select from.
"""


def combined_odds(american_odds: list[int]) -> int:
    """Multiply decimal odds across legs, return the combined American odds.
    Pure math, already usable today (docs/assets/parlay.js does the same
    calculation client-side for display) — the missing piece is picking
    which legs to feed it, not this function."""
    decimal = 1.0
    for o in american_odds:
        decimal *= 1 + (o / 100.0) if o > 0 else 1 + (100.0 / abs(o))
    return round((decimal - 1) * 100) if decimal >= 2 else round(-100 / (decimal - 1))


def select_legs(con, date_s: str, target_legs: int = 3):
    raise NotImplementedError(
        "model.parlay.select_legs: needs real per-player projections beyond "
        "Jokic, plus real alt-line odds, before there's anything to screen — "
        "see this module's docstring for the intended criteria"
    )


def record_result(con, date_s: str, results: dict):
    """Once a night's games are final, write each leg's actual stat line and
    hit/miss, and the overall parlay result, back into a parlay_history
    table (not yet in nproj/db.py's schema — add it alongside this)."""
    raise NotImplementedError("model.parlay.record_result: settle the day's parlay and store it")
