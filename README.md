# PTS Board — NBA Points Projections (skeleton)

Sibling project to [mlb-k-projection](https://github.com/akillam1/mlb-k-projection) (the K
Board), built the same way: free, self-hosted on GitHub Pages, SQLite + Python pipeline,
no server to maintain. See the planning doc for the full design — this README just tracks
what exists in this repo right now vs. what's still a stub.

## What's actually built

- `docs/` — the site itself. **Real, working front end** (cards/table toggle, sorting,
  filtering, the same visual design as the K Board) served from **mock data**:
  `docs/data/today.json` uses ten real NBA players' actual 2024-25 season scoring
  averages as placeholder projections, with made-up sportsbook lines/edges layered on
  top purely to preview the layout. No live pipeline produces this file yet.
- `nproj/` — the Python package skeleton: `config.py`, `util.py` (odds math + board-day
  clock, lifted directly from the K Board's `kproj/util.py`), `db.py` (SQLite schema),
  and `cli.py` (`python -m nproj init|daily|export|status`). `init` and `status` work.
  `daily` and `export` raise `NotImplementedError` — see below.
- `.github/workflows/daily.yml` — a workflow skeleton with the K Board's *dense* backstop
  cron pattern already in place (not its original sparse one — no reason to relearn that
  publish-lag lesson on a second project). Disabled (`if: false`) until the pipeline
  underneath it actually works.

## What's still a stub

Everything that touches real data or a real model:

- `nproj/ingest/nba_stats.py` — schedule, box scores, rosters via `nba_api`
  (`pip install nba_api`, not yet in `requirements.txt`)
- `nproj/ingest/odds.py` — The Odds API player-points props, budget-gated
- `nproj/model/predict.py` — the LightGBM + quantile model
- `nproj/export/site_export.py` — writes the real `docs/data/today.json`,
  replacing the hand-written mockup

Each of those files has a docstring describing the intended shape and what to reuse
from the K Board's equivalent module.

## Build phases

See the planning doc (shared with Robin) for the full phased roadmap and open
decisions — repo location, full-slate vs. shortlist coverage, Odds API budget sharing
with the K Board, rollover-hour choice, and visual identity. Short version:

0. Setup (this repo skeleton — done)
1. Backfill historical player game logs
2. First model (points-only point estimate + quantiles)
3. Live board (no odds yet, just projections)
4. Odds & edges (wire in The Odds API)
5. Scheduling hardened (real rollover hour, freshness gate, verified budget)
6. Scoreboard & polish (settlement, performance page)
7–8 (stretch). Rebounds/assists, Signals-style capper tracking

## Repo status

This currently lives only in a sandbox working directory — it has **not** been pushed
to GitHub. Creating `ethanllawrence/nba-props-projection` (or whatever name Robin
prefers) and pushing this skeleton needs to happen from a session with GitHub write
access, same constraint as the K Board's pending pushes.
