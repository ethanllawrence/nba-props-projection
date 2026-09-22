"""Ingest from ESPN's public JSON feeds (free, no key).

Why ESPN and not stats.nba.com: GitHub-hosted runners can't reach NBA's own
feeds. Tested 2026-09-22 with .github/workflows/nba-api-check.yml:
  stats.nba.com                 -> hangs until timeout
  cdn.nba.com                   -> 403
  site.api.espn.com             -> 403
  site.web.api.espn.com         -> 200  <- everything here uses this host
So every URL below deliberately uses site.web.api.espn.com, even for
endpoints that are more commonly documented on site.api.espn.com.

This is an unofficial, undocumented API. ESPN can change it without notice,
so every parser below is defensive: a missing field skips that row rather
than crashing the whole daily run.

IDs: ESPN has its own athlete/team/event IDs, different from nba_api's.
Jokic is 3112335 on ESPN (203999 on nba_api).
"""
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from .. import config

BASE = "https://site.web.api.espn.com/apis"
ET = ZoneInfo(config.ET_ZONE)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# ESPN uses a few non-standard team abbreviations; map to the ones the site
# (and sportsbooks) use.
ABBR_FIX = {"GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS"}

_session = requests.Session()
_session.headers.update({"User-Agent": UA, "Accept": "application/json"})


def abbr(a):
    return ABBR_FIX.get(a, a) if a else a


def _get(path, params=None, retries=3):
    last = None
    for attempt in range(retries):
        try:
            r = _session.get(f"{BASE}{path}", params=params, timeout=20)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}"
            if r.status_code in (400, 401, 403, 404):
                break
        except requests.RequestException as exc:
            last = str(exc)
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"ESPN GET {path} failed: {last}")


def season_year(d):
    """ESPN labels a season by the year it ENDS: 2025-26 is 2026. A new
    season's games start in October, so August onward counts as next year."""
    return d.year + 1 if d.month >= 8 else d.year


def et_date(iso_utc):
    """ESPN timestamps are UTC ('2025-10-24T02:17:00.000+00:00' or
    '2026-03-10T23:00Z'). Games are dated by their US Eastern calendar day."""
    s = iso_utc.replace("Z", "+00:00")
    return datetime.fromisoformat(s).astimezone(ET)


# ---------- Teams / rosters ----------
def fetch_teams():
    data = _get("/site/v2/sports/basketball/nba/teams")
    teams = []
    for entry in data["sports"][0]["leagues"][0]["teams"]:
        t = entry["team"]
        teams.append({"team_id": str(t["id"]), "abbr": abbr(t["abbreviation"]), "name": t["displayName"]})
    return teams


def fetch_roster(team_id):
    """Players on one team, with ESPN's injury status when it has one.
    status is 'out' / 'day-to-day' / 'active' (lower-cased ESPN wording)."""
    data = _get(f"/site/v2/sports/basketball/nba/teams/{team_id}/roster")
    team_abbr = abbr((data.get("team") or {}).get("abbreviation"))
    players = []
    for a in data.get("athletes", []):
        inj = (a.get("injuries") or [{}])[0]
        status = (inj.get("status") or (a.get("status") or {}).get("type") or "active").lower()
        players.append({
            "player_id": str(a["id"]),
            "name": a.get("displayName") or a.get("fullName"),
            "team": team_abbr,
            "position": (a.get("position") or {}).get("abbreviation"),
            "status": status,
        })
    return players


# ---------- Schedule ----------
def fetch_schedule(date_s):
    """Games on one date (YYYY-MM-DD, US Eastern). Regular season and
    playoffs only; preseason and All-Star games are skipped."""
    data = _get("/site/v2/sports/basketball/nba/scoreboard", {"dates": date_s.replace("-", "")})
    games = []
    for e in data.get("events", []):
        if (e.get("season") or {}).get("type") not in (2, 3):  # 2 = regular, 3 = postseason
            continue
        comp = e["competitions"][0]
        side = {c["homeAway"]: c for c in comp["competitors"]}
        if "home" not in side or "away" not in side:
            continue
        tip = et_date(e["date"])
        games.append({
            "game_id": str(e["id"]),
            "date": tip.strftime("%Y-%m-%d"),
            "home_team": abbr(side["home"]["team"]["abbreviation"]),
            "away_team": abbr(side["away"]["team"]["abbreviation"]),
            "home_team_id": str(side["home"]["team"]["id"]),
            "away_team_id": str(side["away"]["team"]["id"]),
            "status": ((e.get("status") or {}).get("type") or {}).get("description"),
            "tipoff_utc": e["date"],
            "time_et": tip.strftime("%I:%M %p").lstrip("0"),
        })
    return games


# ---------- Game logs ----------
def fetch_player_game_log(player_id, season):
    """Every regular-season and playoff game a player appeared in for one
    ESPN season year (e.g. 2026 for 2025-26), newest first.

    The feed splits stats (seasonTypes[].categories[].events[].stats, by
    position matching `names`) from game info (events{id: ...}). Preseason
    is dropped, and so is the All-Star game, which ESPN files under the
    regular season with a fake team ('WORLD', 'STARS', ...)."""
    data = _get(f"/common/v3/sports/basketball/nba/athletes/{player_id}/gamelog", {"season": season})
    names = data.get("names") or []
    idx = {n: i for i, n in enumerate(names)}
    need = ("minutes", "points", "totalRebounds", "assists")
    if any(n not in idx for n in need):
        return []
    info = data.get("events") or {}

    rows = []
    for st in data.get("seasonTypes", []):
        label = (st.get("displayName") or "").lower()
        if "preseason" in label:
            continue
        is_playoff = "postseason" in label
        for cat in st.get("categories", []):
            if cat.get("type") != "event":
                continue
            for ev in cat.get("events", []):
                meta = info.get(str(ev.get("eventId")))
                stats = ev.get("stats") or []
                if not meta or len(stats) < len(names):
                    continue
                if "all-star" in (meta.get("eventNote") or "").lower():
                    continue
                team = abbr((meta.get("team") or {}).get("abbreviation"))
                opp = abbr((meta.get("opponent") or {}).get("abbreviation"))
                try:
                    row = {
                        "game_id": str(meta["id"]),
                        "player_id": str(player_id),
                        "date": et_date(meta["gameDate"]).strftime("%Y-%m-%d"),
                        "team": team,
                        "opp": f"{'@' if meta.get('atVs') == '@' else 'vs'} {opp}",
                        "minutes": float(stats[idx["minutes"]]),
                        "points": int(float(stats[idx["points"]])),
                        "rebounds": int(float(stats[idx["totalRebounds"]])),
                        "assists": int(float(stats[idx["assists"]])),
                        "threes": _made(stats, idx.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted")),
                        "playoff": is_playoff,
                    }
                except (KeyError, ValueError, TypeError):
                    continue
                rows.append(row)
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows


def _made(stats, i):
    if i is None:
        return None
    try:
        return int(str(stats[i]).split("-")[0])
    except (ValueError, IndexError):
        return None
