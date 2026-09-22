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
- `nproj/` — the Python pipeline. `config.py`, `util.py` (odds math + board-day clock,
  lifted from the K Board's `kproj/util.py`), `db.py` (SQLite schema), `cli.py`
  (`python -m nproj init|daily|export|status`), and now a working, if very simple,
  `model/predict.py` + `export/site_export.py` — see **Real vs. mock data** below for
  exactly what that does and doesn't cover today.
- `.github/workflows/daily.yml` — a workflow skeleton with the K Board's *dense* backstop
  cron pattern already in place. Still disabled (`if: false`) until real ingest can run
  (see **The network blocker**).

## Real vs. mock data (as of the last pipeline run)

Nikola Jokic's row on the Today board is now genuine model output: his points/rebounds/
assists projections come from a recency-weighted rolling average over his real 2025-26
game log, computed by `nproj/model/predict.py` and written by `nproj/export/site_export.py`.
The Jokic tab's "Bet TD" call and probability are also computed for real, from a blend of
his season-long hit rate and his recent-game hit rate (see that file's docstring — it's an
explicitly-labeled placeholder method, not a real joint probability model yet).

Everyone else on the Today board, and every book line/edge for every player including
Jokic, is still the original hand-typed mockup — there's no real game log for them in the
database yet, and no live odds feed. Exporting real data never overwrites a player's mock
row with an invented one; it only touches players who actually have a model behind them.

To reproduce the real Jokic numbers from scratch: `python -m nproj init`, then
`python -m scripts.seed_jokic` (loads his real recent-games log, already sourced into
`docs/data/jokic.json`, into the database — a stopgap for `nba_api` backfill, see below),
then `python -m nproj daily`.

## The network blocker (read this before trying to backfill the rest of the league)

`nproj/ingest/nba_stats.py` is written for real — correct `nba_api` calls for schedule,
box scores, full game logs, and rosters — but **stats.nba.com is not reachable from this
dev sandbox**. A live call fails at the sandbox's outbound proxy with a 403 before it ever
reaches NBA's servers; confirmed directly, it's a network policy block on this environment,
not a bug in the code or in `nba_api` itself. Two ways forward:

1. **Run the real ingest from `.github/workflows/daily.yml` instead of this sandbox** —
   GitHub-hosted runners have normal internet access, and that was always the intended home
   for the live pipeline anyway (see the planning doc). This sandbox was only ever meant for
   building and testing the site/framework.
2. If Robin's org allows it, ask about widening this session's network egress
   (Admin settings → Capabilities) to include `stats.nba.com`, if faster local iteration
   against real data is worth it before wiring up Actions.

Nothing about this blocks writing correct code now — it just means the code above is
untested against a live response and needs a first real run from wherever it actually gets
network access.

## What's still a stub

- `nproj/ingest/nba_stats.py` — code is real, but see **The network blocker** above; needs
  its first live run from somewhere with real internet access
- `nproj/ingest/odds.py` — The Odds API player-points/rebounds/assists props, budget-gated
- `nproj/model/predict.py` — currently a recency-weighted rolling average (real, working,
  deliberately simple); the planned LightGBM + quantile model is still ahead, see that
  file's docstring for the feature list
- Rebounds/assists/PRA all use the same baseline model as points now (the code is
  stat-agnostic), but none of them have real game-log data behind them except Jokic

## Build phases

See the planning doc (shared with Robin) for the full phased roadmap and open
decisions — full-slate vs. shortlist coverage, Odds API budget sharing with the K Board,
rollover-hour choice. Short version:

0. Setup (repo skeleton — done)
1. Backfill historical player game logs — **blocked on network access, see above**;
   proven for one player (Jokic) via a manual seed as a stopgap
2. First model (points/rebounds/assists point estimate + quantiles) — **baseline version
   done** (recency-weighted average), LightGBM upgrade still ahead
3. Live board (no odds yet, just projections) — **done for Jokic**, pending real backfill
   for everyone else
4. Odds & edges (wire in The Odds API)
5. Scheduling hardened (real rollover hour, freshness gate, verified budget)
6. Scoreboard & polish (settlement, performance page)
7 (stretch). Signals-style capper tracking
