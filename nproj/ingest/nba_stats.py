"""Ingest from stats.nba.com via the nba_api package (free, MIT-licensed).

STATUS: real implementation, but UNTESTED FROM THIS SANDBOX. stats.nba.com
is not on this cloud sandbox's outbound network allowlist — a live call from
here fails at the proxy with a 403 before it ever reaches NBA's servers
(confirmed directly: `playergamelog.PlayerGameLog(...)` raises a ProxyError,
not an nba_api/NBA-side error). That's a policy block on THIS environment,
not a problem with the code below or with nba_api itself.

Two ways forward, neither blocking this file from being written correctly
now:
  1. Run it from `.github/workflows/daily.yml` instead — GitHub-hosted
     runners have normal internet access, and that was always the intended
     home for the live pipeline (see the planning doc). This sandbox was
     only ever meant for building/testing the site and framework, not for
     running the real ingest.
  2. If Robin's org allows it, a session's network egress can sometimes be
     widened in Admin settings to add stats.nba.com — worth asking if quick
     local iteration against real data (rather than via GitHub Actions) is
     wanted later.

pip install nba_api  (added to requirements.txt)
"""
import time

from .. import config

try:
    from nba_api.stats.endpoints import (
        boxscoretraditionalv2,
        commonteamroster,
        leaguegamefinder,
        scoreboardv2,
    )
    from nba_api.stats.static import teams as static_teams
except ImportError:  # nba_api not installed in this environment
    boxscoretraditionalv2 = commonteamroster = leaguegamefinder = scoreboardv2 = None
    static_teams = None

_RETRY_DELAYS = (2, 5, 12)  # seconds; stats.nba.com rate-limits more aggressively than MLB's API


def _call_with_retry(fn, *args, **kwargs):
    """nba_api endpoints raise on timeout/429/etc. Retry with growing backoff,
    same pattern as util.http_get's 3x retry, since a single nba_api call is
    itself one or more HTTP requests under the hood."""
    kwargs.setdefault("timeout", config.REQUEST_TIMEOUT)
    last_exc = None
    for delay in (0, *_RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - nba_api raises several exception types
            last_exc = exc
    raise last_exc


def fetch_schedule(date_from, date_to):
    """Games between two YYYY-MM-DD dates (inclusive), normalized into the
    shape nproj.db.games expects: game_id, date, home_team, away_team,
    status, tipoff_utc.

    scoreboardv2 is per-day only, so this loops one call per date rather
    than a single range query — there's no bulk "schedule for date range"
    endpoint in nba_api that also returns tipoff time and status cleanly.
    """
    from datetime import datetime, timedelta

    if boxscoretraditionalv2 is None:
        raise RuntimeError("nba_api is not installed — run: pip install nba_api")

    d0 = datetime.strptime(date_from, "%Y-%m-%d")
    d1 = datetime.strptime(date_to, "%Y-%m-%d")
    games = []
    d = d0
    while d <= d1:
        date_str = d.strftime("%m/%d/%Y")
        sb = _call_with_retry(scoreboardv2.ScoreboardV2, game_date=date_str, day_offset=0)
        header = sb.game_header.get_data_frame()
        for _, row in header.iterrows():
            games.append({
                "game_id": row["GAME_ID"],
                "date": d.strftime("%Y-%m-%d"),
                "home_team": str(row["HOME_TEAM_ID"]),
                "away_team": str(row["VISITOR_TEAM_ID"]),
                "status": row.get("GAME_STATUS_TEXT"),
                "tipoff_utc": row.get("GAME_STATUS_TEXT"),  # scoreboardv2 doesn't give a clean UTC
                                                              # tipoff; derive from GAME_DATE_EST +
                                                              # known ET slate times if needed later
            })
        d += timedelta(days=1)
    return games


def fetch_box_scores(game_id):
    """Per-player minutes/points/rebounds/assists for one finished game,
    normalized into the shape nproj.db.player_game_logs expects."""
    if boxscoretraditionalv2 is None:
        raise RuntimeError("nba_api is not installed — run: pip install nba_api")

    box = _call_with_retry(boxscoretraditionalv2.BoxScoreTraditionalV2, game_id=game_id)
    df = box.player_stats.get_data_frame()
    rows = []
    for _, r in df.iterrows():
        if r.get("MIN") in (None, "", "0:00"):
            continue  # DNP
        rows.append({
            "game_id": game_id,
            "player_id": str(r["PLAYER_ID"]),
            "team": r.get("TEAM_ABBREVIATION"),
            "minutes": _parse_minutes(r.get("MIN")),
            "points": r.get("PTS"),
            "rebounds": r.get("REB"),
            "assists": r.get("AST"),
            "threes": r.get("FG3M"),
        })
    return rows


def _parse_minutes(min_str):
    """nba_api returns MIN as 'MM:SS' (or sometimes just 'MM') — convert to
    a plain float for the db's REAL column."""
    if not min_str:
        return None
    s = str(min_str)
    if ":" in s:
        m, sec = s.split(":")
        return round(int(m) + int(sec) / 60, 1)
    try:
        return float(s)
    except ValueError:
        return None


def fetch_player_game_log(player_id, season):
    """Full season game log for one player — the simplest reliable way to
    backfill player_game_logs without walking every game's box score.
    season format: '2025-26'."""
    from nba_api.stats.endpoints import playergamelog

    if boxscoretraditionalv2 is None:
        raise RuntimeError("nba_api is not installed — run: pip install nba_api")

    gl = _call_with_retry(playergamelog.PlayerGameLog, player_id=player_id, season=season)
    df = gl.get_data_frames()[0]
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "game_id": r["Game_ID"],
            "player_id": str(player_id),
            "date": r["GAME_DATE"],
            "team": r.get("MATCHUP", "").split(" ")[0] if r.get("MATCHUP") else None,
            "opp": r.get("MATCHUP"),
            "minutes": _parse_minutes(r.get("MIN")),
            "points": r.get("PTS"),
            "rebounds": r.get("REB"),
            "assists": r.get("AST"),
            "threes": r.get("FG3M"),
        })
    return rows


def fetch_rosters(team_id=None):
    """All 30 teams' rosters (or one), for player_id/name/team resolution."""
    if commonteamroster is None:
        raise RuntimeError("nba_api is not installed — run: pip install nba_api")

    team_ids = [team_id] if team_id else [t["id"] for t in static_teams.get_teams()]
    players = []
    for tid in team_ids:
        roster = _call_with_retry(commonteamroster.CommonTeamRoster, team_id=tid)
        df = roster.common_team_roster.get_data_frame()
        for _, r in df.iterrows():
            players.append({
                "player_id": str(r["PLAYER_ID"]),
                "name": r["PLAYER"],
                "team": str(tid),
            })
        time.sleep(0.6)  # be polite across 30 sequential calls
    return players
