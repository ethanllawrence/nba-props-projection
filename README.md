# PRAjections — NBA Points/Rebounds/Assists Projections

Sibling project to [mlb-k-projection](https://github.com/akillam1/mlb-k-projection) (the K
Board), built the same way: free, self-hosted on GitHub Pages, SQLite + Python pipeline,
no server to maintain. See the planning doc for the full design — this README just tracks
what exists in this repo right now vs. what's still a stub.

## What's actually built

- `docs/` — the site itself. **Real, working front end**, table-only (no card view — a
  full NBA slate runs 20+ meaningfully-projected players a night, so a sortable table beats
  scrolling cards): `docs/data/today.json` covers ten real NBA players with a condensed
  points/rebounds/assists/PRA table — one column per stat, each cell colored green/gold/red
  by how far the projection sits from that stat's book line, sortable by tapping any stat
  column header. PRA (points+rebounds+assists) is currently just the sum of the three
  individual lines, not a separately-priced combo line yet.
- `docs/jokic.html` + `docs/data/jokic.json` — a separate **Jokic triple-double tracker**
  tab, built around a single headline call — bet the triple-double yes or no tonight — with
  the model probability and market price underneath as supporting detail, then season summary
  tiles and a real recent-games log below that.
- `docs/parlay.html` + `docs/data/parlay.json` — a **Parlay of the Day** tab: 3-4 alt-line
  legs picked for high conviction (projection clears the alt line by a wide margin, low
  blowout/minutes risk) rather than best expected value, combined to roughly +200 to +300
  odds, with per-leg reasoning and a win/loss history log. Leg selection is manually curated
  today — see `nproj/model/parlay.py` for the intended automated criteria, not yet wired up.
- `nproj/` — the Python pipeline: `ingest/espn.py` (schedule, rosters, game logs),
  `pipeline.py` (ingest into SQLite), `model/predict.py` (projections),
  `export/site_export.py` (writes `docs/data/*.json`), `cli.py`
  (`python -m nproj init|daily|backfill|export|status`).
- `.github/workflows/daily.yml` — runs `python -m nproj daily` at 8 AM and 3 PM Arizona
  time and commits the refreshed JSON. Run it by hand with a past date (e.g. 2026-03-10)
  to test without touching the live site.
- `tests/test_pipeline_offline.py` — end-to-end test on fake ESPN data; runs before every
  daily run.

## Data source: ESPN (since 2026-09-22)

GitHub Actions runners can't reach NBA's own feeds. Tested with
`.github/workflows/nba-api-check.yml`: stats.nba.com hangs until timeout, cdn.nba.com and
site.api.espn.com return 403, and **site.web.api.espn.com returns 200**. So all ingest uses
that one host (see the docstring in `nproj/ingest/espn.py`). It's free and needs no key,
but it's unofficial and undocumented, so parsers skip bad rows instead of crashing.

`nproj/ingest/nba_stats.py` (nba_api) and `scripts/seed_jokic.py` are retired and kept only
for reference.

## Real vs. mock data

**Real once the daily workflow runs on a game day:** every Today board projection
(recency-weighted average of each player's game log, this season plus last; players listed
as out or averaging under 20 minutes are left off), and the whole Jokic tab except the
market price.

**Still mock:** all sportsbook lines (none until The Odds API is wired in, so the board shows
"no line" and no edge colors), the Jokic tab's market price, and the whole Parlay of the Day
tab. Until the 2026-27 season starts, `docs/data/today.json` keeps the hand-typed mockup,
because the daily run leaves it alone on dates with no games.

## What's still a stub

- `nproj/ingest/odds.py` — The Odds API player props, budget-gated
- `nproj/model/predict.py` — a recency-weighted rolling average (real, deliberately simple);
  the planned LightGBM + quantile model is still ahead, see that file's docstring
- `nproj/model/parlay.py` — Parlay of the Day selection criteria, not automated

## Build phases

See the planning doc (shared with Robin) for the full phased roadmap and open
decisions — full-slate vs. shortlist coverage, Odds API budget sharing with the K Board,
rollover-hour choice. Short version:

0. Setup (repo skeleton — done)
1. Historical player game logs — **done via ESPN** (`python -m nproj backfill --season 2026`)
2. First model (points/rebounds/assists point estimate + quantiles) — **baseline version
   done** (recency-weighted average), LightGBM upgrade still ahead
3. Live board (no odds yet, just projections) — **built**, goes live with the first game day
4. Odds & edges (wire in The Odds API)
5. Scheduling hardened (real rollover hour, freshness gate, verified budget)
6. Scoreboard & polish (settlement, performance page)
7 (stretch). Signals-style capper tracking
