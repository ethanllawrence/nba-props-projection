"""Central configuration. Everything overridable via environment variables.

Modeled directly on the K Board's kproj/config.py — same shape, NBA specifics.
STATUS: skeleton. Values below are reasonable starting points, not tuned.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("NPROJ_DATA_DIR", ROOT / "data"))
DB_PATH = Path(os.environ.get("NPROJ_DB", DATA_DIR / "nproj.db"))
MODELS_DIR = Path(os.environ.get("NPROJ_MODELS_DIR", ROOT / "models"))
SITE_DATA_DIR = Path(os.environ.get("NPROJ_SITE_DATA", ROOT / "docs" / "data"))
LINES_CSV = Path(os.environ.get("NPROJ_LINES_CSV", ROOT / "lines" / "manual_lines.csv"))

# --- Target stats. Points was the phase-1 target; the site has since moved
#     to points+rebounds+assists (PRA) together, so the model needs all three.
TARGET_STATS = [s for s in os.environ.get(
    "NPROJ_TARGET_STATS", "points,rebounds,assists",
).split(",") if s]

# --- The Odds API (same account as the K Board, free plan, shared budget).
#     Player props are billed per game: cost = markets returned x regions,
#     and up to 10 bookmakers count as one region. The /sports and /events
#     endpoints are free. See nproj/ingest/odds.py for the rationing logic.
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")          # empty = skip odds entirely
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
ODDS_SPORT_KEY = "basketball_nba"
# off (default) | props. The daily workflow turns props on for the morning run only.
ODDS_MODE = os.environ.get("NPROJ_ODDS_MODE", "off")
# Credits never touched by this project, left for the K Board.
ODDS_BUDGET_FLOOR = int(os.environ.get("NPROJ_ODDS_FLOOR", "60"))
# Share of the remaining (above-floor) credits NBA may plan to use over the rest
# of the month; the rest is left for the K Board. Raise it once MLB is done.
ODDS_NBA_SHARE = float(os.environ.get("NPROJ_ODDS_SHARE", "0.5"))
# Day of month the plan's credits reset.
ODDS_RESET_DAY = int(os.environ.get("NPROJ_ODDS_RESET_DAY", "1"))
# Markets in priority order: when credits are tight, only the first few get
# fetched. PRA first, since it's the site's namesake and one line covers all three.
ODDS_MARKETS = [m for m in os.environ.get(
    "NPROJ_ODDS_MARKETS",
    "player_points_rebounds_assists,player_points,player_rebounds,player_assists",
).split(",") if m]
ODDS_JOKIC_TD = os.environ.get("NPROJ_ODDS_JOKIC_TD", "1") == "1"   # +1 credit on Denver game days

# --- NBA Stats (free, via nba_api / stats.nba.com — see nproj/ingest/nba_stats.py)
NBA_STATS_USER_AGENT = "nproj-personal-hobby/0.1"
REQUEST_TIMEOUT = 60

# --- Modeling constants — TODO once feature work starts (see planning doc's
#     "Model approach" section: minutes projection, opponent pace/defense,
#     usage rate, rest/back-to-back, blowout risk)
QUANTILES = [0.10, 0.25, 0.50, 0.75, 0.90]

# --- Edge scoring (same math as the K Board — de-vig, Kelly, EV — is stat-
#     agnostic, so nproj/util.py's odds-math functions are lifted verbatim)
# Robin only actually bets on FanDuel and DraftKings, occasionally bet365 —
# unlike the K Board, don't bother shopping/weighting a wide book list here.
MAJOR_BOOKS = {"draftkings", "fanduel"}
PREFERRED_BOOKS = [b for b in os.environ.get(
    "NPROJ_PREFERRED_BOOKS",
    "fanduel,draftkings",
).split(",") if b]
BOOK_WEIGHT_MAJOR = 1.0
BOOK_WEIGHT_OTHER = 0.7
KELLY_FRACTION = 0.25
MIN_EV_DISPLAY = 0.0

ET_ZONE = "America/New_York"

# --- Board clock ---------------------------------------------------------
# Robin's choice (2026-09-22): the site flips to the next day's slate at 8 PM
# Arizona time. The workflow has an 8 PM run so the flip actually shows up.
# Settling results/parlays does NOT follow this clock: a day is only graded
# once its games are over (see util.day_is_final).
BOARD_ZONE = os.environ.get("NPROJ_BOARD_ZONE", "America/Phoenix")
BOARD_ROLLOVER_HOUR = int(os.environ.get("NPROJ_BOARD_ROLLOVER_HOUR", "20"))   # 8 PM AZ
