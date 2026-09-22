"""The Odds API: NBA player prop lines, rationed for a shared free plan.

Cost model (from the v4 docs): GET /sports and GET /events are free.
GET /events/{id}/odds costs (markets returned) x (regions), and up to 10
bookmakers count as one region. We ask for FanDuel + DraftKings only, so a
game costs 1 credit per market that's actually posted.

Rationing, run once a day (the morning workflow run):
  1. Free call to /sports to read x-requests-remaining.
  2. allowance today = SHARE x (remaining - FLOOR) / days left until reset.
     FLOOR is never touched (kept for the K Board); SHARE leaves the rest of
     the pool for the K Board too. Recomputed daily, so it self-corrects.
  3. Pick how many markets (in ODDS_MARKETS priority order) fit across the
     whole slate. If not even one market fits every game, fetch the first
     market for as many games as the allowance covers.
  4. Jokic's triple-double Yes/No price costs 1 more credit on Denver game
     days, if the allowance still has room.

Results are written to docs/data/lines.json (committed with the site), so
the afternoon run and the page itself reuse the morning's lines without
spending anything.
"""
import json
import re
import unicodedata
from datetime import date, datetime, timezone

import requests

from .. import config

MARKET_TO_STAT = {
    "player_points": "points",
    "player_rebounds": "rebounds",
    "player_assists": "assists",
    "player_points_rebounds_assists": "pra",
}
TD_MARKET = "player_triple_double"

# Odds API uses full team names; the rest of the pipeline uses abbreviations.
TEAM_ABBR = {
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA", "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN", "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "Los Angeles Clippers": "LAC", "LA Clippers": "LAC", "Los Angeles Lakers": "LAL",
    "Memphis Grizzlies": "MEM", "Miami Heat": "MIA", "Milwaukee Bucks": "MIL",
    "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NOP", "New York Knicks": "NYK",
    "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHX", "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC",
    "San Antonio Spurs": "SAS", "Toronto Raptors": "TOR", "Utah Jazz": "UTA",
    "Washington Wizards": "WAS",
}

_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def norm_name(name):
    """'Nikola Jokić' / 'Jaren Jackson Jr.' -> 'nikola jokic' / 'jaren jackson'."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    s = _SUFFIX.sub(" ", s)
    return " ".join(s.split())


class Quota:
    """Latest usage numbers from response headers."""
    remaining = None
    used = None

    @classmethod
    def update(cls, resp):
        try:
            cls.remaining = int(float(resp.headers["x-requests-remaining"]))
            cls.used = int(float(resp.headers["x-requests-used"]))
        except (KeyError, ValueError):
            pass


def _get(path, **params):
    params["apiKey"] = config.ODDS_API_KEY
    r = requests.get(f"{config.ODDS_API_BASE}{path}", params=params, timeout=30)
    Quota.update(r)
    if r.status_code != 200:
        raise RuntimeError(f"Odds API {path}: HTTP {r.status_code} {r.text[:200]}")
    return r.json()


def check_quota():
    """Free call. Returns (remaining, used) or raises if the key is bad."""
    _get("/sports")
    return Quota.remaining, Quota.used


def days_until_reset(today: date):
    reset = config.ODDS_RESET_DAY
    if today.day < reset:
        nxt = today.replace(day=reset)
    else:
        y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        nxt = date(y, m, min(reset, 28))
    return max(1, (nxt - today).days)


def daily_allowance(remaining, today: date):
    spare = max(0, remaining - config.ODDS_BUDGET_FLOOR)
    return int(config.ODDS_NBA_SHARE * spare / days_until_reset(today))


def plan(n_games, allowance, markets):
    """-> (markets to fetch for every game, max games for a partial fetch).
    Partial = only markets[0], for the first `partial` games."""
    if n_games == 0:
        return [], 0
    k = min(len(markets), allowance // n_games)
    if k >= 1:
        return markets[:k], n_games
    return markets[:1], min(n_games, allowance)


def fetch_events(date_s):
    """Free. Odds API events whose tipoff falls on date_s (US Eastern)."""
    from .espn import et_date
    out = []
    for e in _get(f"/sports/{config.ODDS_SPORT_KEY}/events"):
        if et_date(e["commence_time"]).strftime("%Y-%m-%d") != date_s:
            continue
        out.append({
            "event_id": e["id"],
            "home": TEAM_ABBR.get(e["home_team"], e["home_team"]),
            "away": TEAM_ABBR.get(e["away_team"], e["away_team"]),
            "commence_time": e["commence_time"],
        })
    return sorted(out, key=lambda e: e["commence_time"])


def fetch_event_props(event_id, markets):
    return _get(
        f"/sports/{config.ODDS_SPORT_KEY}/events/{event_id}/odds",
        regions="us",
        bookmakers=",".join(config.PREFERRED_BOOKS),
        markets=",".join(markets),
        oddsFormat="american",
    )


def parse_props(event_json):
    """-> {norm_name: {stat: {line, over, under, book}}} plus
          {norm_name: {"td_yes": price, "book": book}} for the triple-double market.
    One book per player/stat: the first in PREFERRED_BOOKS that posts it."""
    rank = {b: i for i, b in enumerate(config.PREFERRED_BOOKS)}
    books = sorted(event_json.get("bookmakers", []), key=lambda b: rank.get(b.get("key"), 99))
    lines, td = {}, {}
    for bk in books:
        title = bk.get("title") or bk.get("key")
        for m in bk.get("markets", []):
            key = m.get("key")
            if key == TD_MARKET:
                for o in m.get("outcomes", []):
                    who = norm_name(o.get("description"))
                    if o.get("name") == "Yes" and who and who not in td:
                        td[who] = {"odds": o.get("price"), "book": title}
                continue
            stat = MARKET_TO_STAT.get(key)
            if not stat:
                continue
            by_player = {}
            for o in m.get("outcomes", []):
                who = norm_name(o.get("description"))
                if not who or o.get("point") is None:
                    continue
                side = by_player.setdefault(who, {"line": o["point"], "book": title})
                if o.get("name") == "Over":
                    side["over"] = o.get("price")
                    side["line"] = o["point"]
                elif o.get("name") == "Under":
                    side["under"] = o.get("price")
            for who, v in by_player.items():
                lines.setdefault(who, {}).setdefault(stat, v)
    return lines, td


def refresh_lines(date_s, jokic_name="Nikola Jokic", jokic_team="DEN"):
    """Morning run: spend today's allowance and write lines.json.
    Returns a summary dict for the run log. Never raises for budget reasons."""
    today = date.fromisoformat(date_s)
    remaining, used = check_quota()
    allowance = daily_allowance(remaining, today)
    events = fetch_events(date_s)
    markets, n_full = plan(len(events), allowance, config.ODDS_MARKETS)

    all_lines, td_price = {}, None
    spent = 0
    jokic_key = norm_name(jokic_name)
    room_for_td = config.ODDS_JOKIC_TD and len(markets) * n_full + 1 <= allowance
    for ev in events[:n_full]:
        mk = list(markets)
        if room_for_td and jokic_team in (ev["home"], ev["away"]):
            mk.append(TD_MARKET)
        before = Quota.remaining
        lines, td = parse_props(fetch_event_props(ev["event_id"], mk))
        if before is not None and Quota.remaining is not None:
            spent += before - Quota.remaining
        for who, stats in lines.items():
            all_lines.setdefault(who, {}).update(stats)
        if jokic_key in td:
            td_price = td[jokic_key]

    out = {
        "date": date_s,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "games_on_slate": len(events),
        "games_fetched": min(n_full, len(events)),
        "markets": markets,
        "credits_spent": spent,
        "credits_remaining": Quota.remaining,
        "lines": all_lines,
        "jokic_td": td_price,
    }
    save_lines(out)
    return {k: out[k] for k in ("games_on_slate", "games_fetched", "markets",
                                "credits_spent", "credits_remaining")} | {
        "allowance": allowance, "remaining_before": remaining, "players_with_lines": len(all_lines)}


def lines_path():
    return config.SITE_DATA_DIR / "lines.json"


def save_lines(data):
    with open(lines_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def load_lines(date_s):
    """Lines saved for date_s, or {} (wrong date, missing file)."""
    try:
        with open(lines_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if data.get("date") == date_s else {}

