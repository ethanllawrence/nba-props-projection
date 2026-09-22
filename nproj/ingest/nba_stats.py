"""Ingest from stats.nba.com via the nba_api package (free, MIT-licensed).

STATUS: stub. Real work here is:
  1. Schedule pull (nba_api.stats.endpoints.scoreboardv2 or leaguegamefinder)
  2. Box scores / player game logs (boxscoretraditionalv2, playergamelogs)
  3. Rosters + injury/role status (commonteamroster, and likely a separate
     free source for injury reports — nba_api doesn't cleanly expose one)

stats.nba.com is known to rate-limit and occasionally block scripted
traffic more aggressively than MLB's Stats API did — expect to need longer
backoff (util.http_get already retries 3x with growing delay) and a
believable browser-like User-Agent/header set beyond what util.http_get
sends today; nba_api's own client sets several NBA-specific headers that
may need to be replicated here rather than relying on requests defaults.

pip install nba_api  (not yet in requirements.txt — add once this is wired up)
"""
from .. import util


def fetch_schedule(date_from, date_to):
    """Placeholder. Real implementation: nba_api's scoreboardv2 or
    leaguegamefinder endpoint, filtered to the date range, normalized into
    the shape nproj.db.games expects."""
    raise NotImplementedError("nba_stats.fetch_schedule: wire up nba_api here")


def fetch_box_scores(game_id):
    """Placeholder. Real implementation: boxscoretraditionalv2 for a
    finished game, returning per-player minutes/points/rebounds/assists."""
    raise NotImplementedError("nba_stats.fetch_box_scores: wire up nba_api here")


def fetch_rosters(team_id=None):
    """Placeholder. Real implementation: commonteamroster (all 30 teams or
    one), for player_id/name/team resolution."""
    raise NotImplementedError("nba_stats.fetch_rosters: wire up nba_api here")
