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

# --- The Odds API (same vendor/account as the K Board; see planning doc —
#     player props are fetched PER EVENT, not in one bulk call like game
#     lines, so the credit math is much tighter than MLB's. Confirm actual
#     spend against ODDS_MONTHLY_BUDGET before turning on props for a full
#     slate of games.)
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")          # empty = skip odds ingestion
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
ODDS_SPORT_KEY = "basketball_nba"
ODDS_MONTHLY_BUDGET = 500                                   # shared free-tier plan w/ the K Board
ODDS_BUDGET_FLOOR = 60
ODDS_PROPS_MARKET = "player_points"
ODDS_MODE = os.environ.get("NPROJ_ODDS_MODE", "auto")       # auto|props|off

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
    "fanduel,draftkings,bet365",
).split(",") if b]
BOOK_WEIGHT_MAJOR = 1.0
BOOK_WEIGHT_OTHER = 0.7
KELLY_FRACTION = 0.25
MIN_EV_DISPLAY = 0.0

ET_ZONE = "America/New_York"

# --- Board clock ---------------------------------------------------------
# TODO: decide the rollover hour deliberately (see planning doc's Open
# questions — NBA West Coast night games can run past midnight ET, later
# than MLB's latest starts, so this should NOT default to copying the K
# Board's 7 PM AZ without checking against a real NBA schedule first).
BOARD_ZONE = os.environ.get("NPROJ_BOARD_ZONE", "America/Phoenix")
BOARD_ROLLOVER_HOUR = int(os.environ.get("NPROJ_BOARD_ROLLOVER_HOUR", "21"))   # placeholder: 9 PM AZ
