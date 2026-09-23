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
- `docs/parlay.html` + `docs/data/parlay.json` — a **Parlay of the Day** tab: 2-4 legs picked
  automatically for high conviction (not best expected value), combined to roughly +200 to
  +300, with per-leg reasoning and a settled win/loss history. See the Parlay section below.
- `nproj/` — the Python pipeline: `ingest/espn.py` (schedule, rosters, game logs),
  `pipeline.py` (ingest into SQLite), `model/predict.py` (projections),
  `export/site_export.py` (writes `docs/data/*.json`), `cli.py`
  (`python -m nproj init|daily|backfill|export|status`).
- `.github/workflows/daily.yml` — runs `python -m nproj daily` at 8 AM and 3 PM Arizona
  time and commits the refreshed JSON. Run it by hand with a past date (e.g. 2026-03-10)
  to test without touching the live site.
- `tests/test_pipeline_offline.py` — end-to-end test on fake ESPN data; runs before every
  daily run.

## Results, safety checks, clock (since 2026-09-22)

- `nproj/results.py` + `docs/results.html`: each morning grades last night's board (MAE per
  stat, and the record of every 6%+ edge call vs. the line; 52.4% breaks even at -110).
- Name matching between ESPN and sportsbooks: exact, then `NAME_ALIASES`, then first
  initial + last name. Unmatched sportsbook names are printed as warnings in the run log.
- `docs/data/status.json` heartbeat + `docs/assets/stale.js`: every page shows a warning
  banner if the data is more than 30 hours old.
- Board flips to the next day at 8 PM Arizona (third daily run). Results and parlays are
  only graded once a night's games are final (`util.day_is_final`).

## The PRA model (live since 2026-09-22)

- `nproj/model/features.py`: pre-game features from one box-score table (form, per-minute
  rates, usage, starter rate, rest/b2b, spread/total/implied team points, opponent allowed,
  teammates out). Same code for backtests and tonight's slate.
- `nproj/model/pra_model.py`: LightGBM (poisson) per stat + minutes, negative-binomial
  spread for P(over).
- `nproj/model/live.py`: every daily run trains on `history/`, projects tonight, and writes
  projections. Site probabilities blend model and market (`calibration.json`, fitted on the
  backtest); a cell is colored when that beats the price's break-even by 2+ points.
- `nproj/ingest/box_store.py`: each morning appends last night's box scores to
  `history/<season>/` (committed by the workflow), so the model keeps learning in-season.
- `scripts/backtest.py`: monthly walk-forward backtest vs real historical lines
  (reports in `reports/`). `NPROJ_MODEL=baseline` falls back to the old recent average.

## Historical data for the model (`python -m nproj history`)

`nproj/ingest/espn_history.py` + `.github/workflows/backfill.yml` download 2023-24 through
2025-26: every game's box score (incl. starters/DNPs), the spread and total, and ESPN's
archived sportsbook prop lines (DraftKings 2023-24, ESPN BET 2024-25 and early 2025-26) into
`history/<season>/{games,box,props}.csv.gz`. That's what the real model is trained and
backtested on.

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

**Book lines (since 2026-09-22):** `nproj/ingest/odds.py` pulls FanDuel/DraftKings prop lines
once a day (the 8 AM Arizona workflow run) into `docs/data/lines.json`; the 3 PM run reuses
them for free. The account is a free plan shared with the K Board, so spending is rationed:
today's allowance = `NPROJ_ODDS_SHARE` x (credits remaining - `NPROJ_ODDS_FLOOR`) / days
until reset. Markets are bought in priority order (PRA, points, rebounds, assists) as far as
the allowance covers the whole slate, plus Jokic's triple-double Yes price on Denver game
days if there's room. `python -m nproj odds-status` checks the balance for free.

**Parlay of the Day (since 2026-09-22):** `nproj/model/parlay.py` picks it automatically in
the daily run and settles the previous day's from real box scores. Legs need a 62%+ model
chance (normal approximation over recent games, shrunk 15% toward 50%), steady minutes, no
injury tag, and a spread under 12. Leftover odds credits buy the slate's spreads (1 credit)
and then alt-line ladders for the top candidates (max 4 credits); alt legs use the highest
alt line with a 78%+ model chance. Greedy by confidence, 1 leg per player, 2 per game, until
+200 to +320; no parlay if it can't reach +150. The hand-made preview in `parlay.json` is
replaced on the first real game day. Until the 2026-27 season starts, `docs/data/today.json` keeps the hand-typed mockup,
because the daily run leaves it alone on dates with no games.

## What's still a stub

- `nproj/model/predict.py` — a recency-weighted rolling average (real, deliberately simple);
  the planned LightGBM + quantile model is still ahead, see that file's docstring

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
