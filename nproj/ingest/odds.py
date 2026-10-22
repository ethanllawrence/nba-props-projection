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
ALT_MARKET = {
    "points": "player_points_alternate",
    "rebounds": "player_rebounds_alternate",
    "assists": "player_assists_alternate",
    "pra": "player_points_rebounds_assists_alternate",
}

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


# Known spelling differences between ESPN and the sportsbooks, both sides
# already run through norm_name. Add pairs here when the run log's
# "unmatched lines" warning names one.
NAME_ALIASES = {
    "nic claxton": "nicolas claxton",
    "herb jones": "herbert jones",
    "cam johnson": "cameron johnson",
    "og anunoby": "o g anunoby",
    "alex sarr": "alexandre sarr",
}


def _short_key(n):
    parts = n.split()
    return (parts[0][0], parts[-1]) if len(parts) >= 2 else None


def match_lines(espn_names, lines):
    """Pair each ESPN player name with his entry in `lines` (keyed by
    norm_name of the sportsbook's spelling). Exact match first, then known
    aliases, then first initial + last name when that's unique on both
    sides. -> ({espn_name: line_entry}, [sportsbook names left unmatched])."""
    by_short = {}
    for k in lines:
        sk = _short_key(k)
        if sk:
            by_short.setdefault(sk, []).append(k)
    rev_alias = {v: k for k, v in NAME_ALIASES.items()}
    espn_short = {}
    for name in espn_names:
        sk = _short_key(norm_name(name))
        if sk:
            espn_short[sk] = espn_short.get(sk, 0) + 1

    matched, used = {}, set()
    for name in espn_names:
        n = norm_name(name)
        for cand in (n, NAME_ALIASES.get(n), rev_alias.get(n)):
            if cand and cand in lines:
                matched[name] = lines[cand]
                used.add(cand)
                break
        else:
            sk = _short_key(n)
            hits = by_short.get(sk, []) if sk else []
            if len(hits) == 1 and espn_short.get(sk) == 1 and hits[0] not in used:
                matched[name] = lines[hits[0]]
                used.add(hits[0])
    return matched, sorted(k for k in lines if k not in used)


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
                if o.get("line_type") not in (None, "main"):
                    continue            # alternate/milestone rows (PropLine marks them)
                side = by_player.setdefault(who, {"line": o["point"], "book": title})
                if o.get("name") == "Over":
                    side["over"] = o.get("price")
                    side["line"] = o["point"]
                elif o.get("name") == "Under":
                    side["under"] = o.get("price")
            for who, v in by_player.items():
                lines.setdefault(who, {}).setdefault(stat, v)
    return lines, td


LADDER_STATS = ("pra", "points")   # morning alt ladders for the parlay (overs on stars)
MAX_LADDER_GAMES = 4               # at most this many games get fresh morning ladders


def plan_morning(events, allowance, covered=None, markets=None, ladders_on=True, free_ladders=()):
    """How the morning spends today's allowance, games already in priority
    order (the games with the top projected players first). Levels:
      1. PRA main line, every game (the board's namesake column);
      2. fresh PRA + points alt ladders for the top games (the Parlay of the
         Day prices its legs from these; Robin 2026-10-21: the morning parlay
         should use morning prices for the top players, not only the
         day-old early-look ones), up to MAX_LADDER_GAMES;
      3. points main lines, top games first, then rebounds, then assists.
    A share of the allowance (early_reserve) is held back for the next game
    day's early-look ladders. covered: {event_id: set(stats)} main lines a
    free source already has, which cost nothing here and are skipped at
    every level. free_ladders: event_ids whose alt ladders are free.
    -> (markets per event_id, ladder event_ids, reserve)."""
    markets = markets or config.ODDS_MARKETS
    covered = covered or {}
    reserve = early_reserve(allowance)
    budget = allowance - reserve
    per = {e["event_id"]: [] for e in events}

    def level(market):
        nonlocal budget
        stat = MARKET_TO_STAT.get(market)
        for e in events:
            if budget < 1:
                return
            if stat in covered.get(e["event_id"], set()):
                continue
            per[e["event_id"]].append(market)
            budget -= 1

    if markets:
        level(markets[0])
    ladders = []
    for e in (events if ladders_on else []):
        if e["event_id"] in free_ladders:
            continue
        if len(ladders) >= MAX_LADDER_GAMES or budget < len(LADDER_STATS):
            break
        ladders.append(e["event_id"])
        budget -= len(LADDER_STATS)
    for m in markets[1:]:
        level(m)
    return per, ladders, reserve


def refresh_lines(date_s, jokic_name="Nikola Jokic", jokic_team="DEN", team_order=(), ladders_on=True,
                  espn=None):
    """Morning run: spend today's allowance and write lines.json. team_order:
    teams with the top projected players first (their games get points lines
    and fresh alt ladders first when credits are short). Main lines from a
    free source (PropLine, if PROPLINE_API_KEY is set) are used first and
    cost no credits. Returns a summary dict. Never raises for budget reasons."""
    today = date.fromisoformat(date_s)
    remaining, used = check_quota()
    allowance = daily_allowance(remaining, today)
    events = fetch_events(date_s)
    rank = {t: i for i, t in enumerate(team_order)}
    events.sort(key=lambda e: (min(rank.get(e["home"], 99), rank.get(e["away"], 99)), e["commence_time"]))

    all_lines, covered, free_log = {}, {}, None
    if config.PROPLINE_API_KEY:
        try:
            all_lines, covered, free_log = propline_mains(events)
        except Exception as exc:  # noqa: BLE001 - the free source is a bonus
            free_log = {"error": str(exc)[:200]}
    # ESPN's free DraftKings props (ingest/espn_props.py) first: any game/stat
    # ESPN has (main lines or alt ladders) is never bought. Credits only fill
    # what's missing; the rest carries over (the allowance is recomputed daily).
    # No book preference (Robin, 2026-10-21: boosts on both, best model data).
    free_ladders = set()
    if espn:
        from .espn_props import by_teams
        cov = by_teams(espn)
        for e in events:
            c = cov.get((e["home"], e["away"]))
            if not c:
                continue
            covered.setdefault(e["event_id"], set()).update(c["line_stats"])
            if {"pra", "points"} <= c["ladder_stats"]:
                free_ladders.add(e["event_id"])
    per, ladders, reserve = plan_morning(events, allowance, covered, ladders_on=ladders_on,
                                         free_ladders=free_ladders)
    # today's lines.json holds the free lines too, so it is complete on its own
    # (and "today's lines were bought" stays true when ESPN covered everything)
    for who, per_stat in ((espn or {}).get("lines") or {}).items():
        for stat, v in per_stat.items():
            all_lines.setdefault(who, {}).setdefault(stat, v)

    td_price, spent = None, 0
    jokic_key = norm_name(jokic_name)
    planned = sum(len(v) for v in per.values()) + len(ladders) * len(LADDER_STATS)
    room_for_td = config.ODDS_JOKIC_TD and planned + 1 <= allowance - reserve
    for ev in events:
        mk = list(per[ev["event_id"]])
        if room_for_td and jokic_team in (ev["home"], ev["away"]):
            mk.append(TD_MARKET)
        if not mk:
            continue
        before = Quota.remaining
        lines, td = parse_props(fetch_event_props(ev["event_id"], mk))
        if before is not None and Quota.remaining is not None:
            spent += before - Quota.remaining
        for who, stats in lines.items():
            for stat, v in stats.items():
                all_lines.setdefault(who, {}).setdefault(stat, v)
        if jokic_key in td:
            td_price = td[jokic_key]

    used_markets = [m for m in config.ODDS_MARKETS
                    if any(m in v for v in per.values())
                    or any(MARKET_TO_STAT[m] in c for c in covered.values())]
    out = {
        "date": date_s,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "allowance": allowance,
        "early_reserve": reserve,
        "events": events,
        "games_on_slate": len(events),
        "games_fetched": sum(1 for e in events if per[e["event_id"]] or covered.get(e["event_id"])),
        "markets": used_markets,
        "markets_by_game": {f"{e['away']} @ {e['home']}": sorted(
            set(MARKET_TO_STAT[m] for m in per[e["event_id"]]) | covered.get(e["event_id"], set()))
            for e in events},
        "ladder_events": ladders,
        "free_ladder_events": sorted(free_ladders),
        "credits_spent": spent,
        "credits_remaining": Quota.remaining,
        "lines": all_lines,
        "jokic_td": td_price,
    }
    if free_log is not None:
        out["free_source"] = free_log
    save_lines(out)
    return {k: out[k] for k in ("games_on_slate", "games_fetched", "markets", "markets_by_game",
                                "credits_spent", "credits_remaining")} | {
        "allowance": allowance, "remaining_before": remaining, "players_with_lines": len(all_lines),
        "ladder_games": len(ladders), "free_source": free_log}


# ------------------------------------------------------------ PropLine ----
# PropLine (prop-line.com) has a free key (1,000 requests a day, no card) with
# FanDuel/DraftKings player props in The Odds API's format. When the repo has
# a PROPLINE_API_KEY secret, the morning run takes main lines from it first, so
# Odds API credits go to alt ladders instead. Checked 2026-10-21 from its docs;
# its NBA market keys are assumed to match The Odds API's (the run log shows
# what came back per market, and anything missing is bought as before).
PROPLINE_BASE = "https://api.prop-line.com/v1"


def _pl_get(path, **params):
    params["apiKey"] = config.PROPLINE_API_KEY
    r = requests.get(f"{PROPLINE_BASE}{path}", params=params, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"PropLine {path}: HTTP {r.status_code} {r.text[:200]}")
    return r.json()


def propline_mains(events):
    """Main lines for today's games from PropLine (free). -> (lines,
    {odds_api_event_id: set(stats covered)}, log)."""
    pl_events = _pl_get(f"/sports/{config.ODDS_SPORT_KEY}/events")
    by_teams = {}
    for e in pl_events:
        key = (TEAM_ABBR.get(e.get("home_team"), e.get("home_team")),
               TEAM_ABBR.get(e.get("away_team"), e.get("away_team")))
        by_teams[key] = e.get("id")
    lines, covered, per_stat = {}, {}, {}
    for ev in events:
        pid = by_teams.get((ev["home"], ev["away"]))
        if not pid:
            continue
        data = _pl_get(f"/sports/{config.ODDS_SPORT_KEY}/events/{pid}/odds",
                       markets=",".join(config.ODDS_MARKETS),
                       bookmakers=",".join(config.PREFERRED_BOOKS), oddsFormat="american")
        got, _ = parse_props(data)
        stats = set()
        for who, per in got.items():
            for stat, v in per.items():
                if v.get("over") is not None and v.get("under") is not None:
                    lines.setdefault(who, {})[stat] = {**v, "source": "PropLine"}
                    stats.add(stat)
                    per_stat[stat] = per_stat.get(stat, 0) + 1
        covered[ev["event_id"]] = stats
    return lines, covered, {"games": sum(1 for v in covered.values() if v), "lines_per_stat": per_stat}


def early_reserve(allowance):
    """Credits held back each morning for the next game day's early-look alt
    ladders: a third of the allowance, at most EARLY_MAX_CREDITS, in whole
    games (2 credits each)."""
    from ..model import early_parlay
    if not early_parlay.ENABLED:          # retired: nothing to hold back
        return 0
    return min(EARLY_MAX_CREDITS, allowance // 3) // len(EARLY_STATS) * len(EARLY_STATS)


def leftover_credits(saved, include_reserve=False):
    """Credits from today's allowance not used by the main pull (or by
    extras already bought). Parlay extras (spreads, alt lines) spend these;
    the early-look reserve is only for the early-look purchase."""
    left = (saved.get("allowance", 0) - saved.get("credits_spent", 0)
            - saved.get("extras_spent", 0))
    if not include_reserve:
        left -= saved.get("early_reserve", 0)
    return max(0, left)


def spend_extra(saved, fn, *args):
    """Run one paid call, charge its actual cost to extras_spent."""
    before = Quota.remaining
    result = fn(*args)
    if before is not None and Quota.remaining is not None:
        saved["extras_spent"] = saved.get("extras_spent", 0) + before - Quota.remaining
        saved["credits_remaining"] = Quota.remaining
    return result


def fetch_spreads(date_s):
    """One bulk call for every game's point spread: 1 market x 1 region = 1
    credit for the whole slate. -> {team_abbr: spread}, e.g. {"DEN": -4.5}."""
    from .espn import et_date
    data = _get(f"/sports/{config.ODDS_SPORT_KEY}/odds", regions="us", markets="spreads",
                bookmakers=",".join(config.PREFERRED_BOOKS), oddsFormat="american")
    rank = {b: i for i, b in enumerate(config.PREFERRED_BOOKS)}
    out = {}
    for ev in data:
        if et_date(ev["commence_time"]).strftime("%Y-%m-%d") != date_s:
            continue
        for bk in sorted(ev.get("bookmakers", []), key=lambda b: rank.get(b.get("key"), 99)):
            m = next((m for m in bk.get("markets", []) if m.get("key") == "spreads"), None)
            if not m:
                continue
            for o in m.get("outcomes", []):
                team = TEAM_ABBR.get(o.get("name"), o.get("name"))
                if o.get("point") is not None:
                    out.setdefault(team, o["point"])
            break
    return out


def fetch_alt_props(event_id, stats):
    """Alt-line Over prices for the given stats in one game. Costs 1 credit
    per stat. -> {norm_name: {stat: [{"line", "over", "book"}, ...]}}"""
    data = fetch_event_props(event_id, [ALT_MARKET[s] for s in stats])
    rank = {b: i for i, b in enumerate(config.PREFERRED_BOOKS)}
    rev = {v: k for k, v in ALT_MARKET.items()}
    out = {}
    for bk in sorted(data.get("bookmakers", []), key=lambda b: rank.get(b.get("key"), 99)):
        title = bk.get("title") or bk.get("key")
        for m in bk.get("markets", []):
            stat = rev.get(m.get("key"))
            if not stat:
                continue
            per_player = {}
            for o in m.get("outcomes", []):
                who = norm_name(o.get("description"))
                if o.get("name") != "Over" or not who or o.get("point") is None:
                    continue
                per_player.setdefault(who, []).append(
                    {"line": o["point"], "over": o.get("price"), "book": title})
            for who, ladder in per_player.items():
                out.setdefault(who, {}).setdefault(stat, sorted(ladder, key=lambda x: x["line"]))
    return out


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



# ------------------------------------------------------- early-look alt lines ----
# The early-look parlay (nproj/model/early_parlay.py) is built from real alt-line
# prices for the NEXT game day. They're bought once per game day, on the morning
# odds run, once that day is within EARLY_DAYS_AHEAD days (books post star
# props a day or two ahead), from what's left of today's allowance after
# today's own lines and parlay extras, at most EARLY_MAX_CREDITS. Each game
# costs one credit per stat (PRA and points alt ladders = 2 credits), so 10
# credits covers the 5 games with the most top players.
# A manual run (Robin asked for prices now) uses a fresh day's allowance from
# the remaining credits instead of this morning's leftovers, so a budget-starved
# morning can be topped up mid-day without re-buying today's lines.
EARLY_DAYS_AHEAD = 2
EARLY_MAX_CREDITS = 10
EARLY_STATS = ("pra", "points")


def early_lines_path():
    return config.SITE_DATA_DIR / "early_lines.json"


def load_early_lines(date_s):
    """Early alt ladders saved for date_s, or {}."""
    try:
        with open(early_lines_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if data.get("date") == date_s else {}


def refresh_early_alts(board_date_s, game_date_s, team_order=(), force=False, budget=None,
                       skip_games=()):
    """Buy alt ladders for game_date_s if due and affordable. team_order:
    team abbreviations, most important first (games with the top projected
    players are bought first). force: a manual odds run (Robin asked for
    prices now) skips the days-ahead wait; the budget still applies.
    budget: credits to use (a manual top-up passes what it has left).
    -> log dict."""
    if load_early_lines(game_date_s).get("alts"):
        return {"skipped": f"already bought for {game_date_s}"}
    ahead = (date.fromisoformat(game_date_s) - date.fromisoformat(board_date_s)).days
    if ahead > EARLY_DAYS_AHEAD and not force:
        return {"skipped": f"{game_date_s} is {ahead} days out (buying starts {EARLY_DAYS_AHEAD} days ahead)"}
    saved = load_lines(board_date_s)
    if budget is not None:
        pass
    elif saved and not force:
        budget = leftover_credits(saved, include_reserve=True)
    else:                                  # no slate today (offseason, off day): today's allowance
        budget = topup_allowance(board_date_s)
    budget = min(budget, EARLY_MAX_CREDITS)
    per_game = len(EARLY_STATS)
    if budget < per_game:
        return {"skipped": f"budget {budget} credits, need {per_game} per game"}
    events = [e for e in fetch_events(game_date_s) if (e["home"], e["away"]) not in set(skip_games)]
    rank = {t: i for i, t in enumerate(team_order)}
    events.sort(key=lambda e: min(rank.get(e["home"], 99), rank.get(e["away"], 99)))
    alts, spent, bought = {}, 0, []
    for ev in events:
        if budget - spent < per_game:
            break
        before = Quota.remaining
        got = fetch_alt_props(ev["event_id"], list(EARLY_STATS))
        cost = (before - Quota.remaining) if before is not None and Quota.remaining is not None else per_game
        spent += cost
        bought.append(f"{ev['away']} @ {ev['home']}")
        for who, per_stat in got.items():
            alts.setdefault(who, {}).update(per_stat)
    if saved:                              # charge today's budget so nothing double-spends
        saved["extras_spent"] = saved.get("extras_spent", 0) + spent
        saved["credits_remaining"] = Quota.remaining
        save_lines(saved)
    data = {"date": game_date_s, "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "credits_spent": spent, "games": bought, "alts": alts}
    with open(early_lines_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return {"bought": bought, "credits": spent, "players": len(alts)}


# ------------------------------------------------------------ manual top-up ----
# A manual "Daily board" run with the odds box ticked, after today's lines were
# already bought, is a TOP-UP: fresh alt ladders (all four stats) for today's
# games that haven't started, so today's parlay can be re-picked from current
# prices, then the next game day's early-look ladders. It spends a fresh day's
# allowance (the same share formula), never today's main lines again.
TOPUP_STATS = ("pra", "points", "rebounds", "assists")


def topup_allowance(date_s):
    remaining, _ = check_quota()
    return daily_allowance(remaining, date.fromisoformat(date_s))


def refresh_today_alts(date_s, budget, team_order=(), not_before=None):
    """Buy all-stat alt ladders for today's games tipping after not_before
    (UTC datetime), games with the top players first, within budget. Merges
    them into lines.json "alts" (newer prices win). -> log dict with "spent"."""
    saved = load_lines(date_s)
    if not saved.get("lines"):
        return {"skipped": "no lines for today yet", "spent": 0}
    per_game = len(TOPUP_STATS)
    events = fetch_events(date_s)
    if not_before is not None:
        events = [e for e in events
                  if datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00")) > not_before]
    rank = {t: i for i, t in enumerate(team_order)}
    events.sort(key=lambda e: min(rank.get(e["home"], 99), rank.get(e["away"], 99)))
    alts = saved.get("alts") or {}
    spent, bought = 0, []
    for ev in events:
        if budget - spent < per_game:
            break
        before = Quota.remaining
        got = fetch_alt_props(ev["event_id"], list(TOPUP_STATS))
        cost = (before - Quota.remaining) if before is not None and Quota.remaining is not None else per_game
        spent += cost
        bought.append(f"{ev['away']} @ {ev['home']}")
        for who, per_stat in got.items():
            alts.setdefault(who, {}).update(per_stat)
    if not events:
        return {"skipped": "every game has started", "spent": 0}
    saved["alts"] = alts
    saved["alts_fetched_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    saved["topup"] = {"at": saved["alts_fetched_at"], "games": bought, "credits": spent}
    saved["extras_spent"] = saved.get("extras_spent", 0) + spent
    saved["credits_remaining"] = Quota.remaining
    save_lines(saved)
    return {"bought": bought, "spent": spent, "players": len(alts)}


def board_lines(date_s):
    """Lines for the board: the paid lines (FanDuel first), with ESPN's free
    DraftKings lines filling any player/stat the paid pull doesn't have."""
    from . import espn_props
    paid = load_lines(date_s)
    lines = {who: dict(per) for who, per in (paid.get("lines") or {}).items()}
    free = espn_props.load(date_s)
    for who, per in (free.get("lines") or {}).items():
        for stat, v in per.items():
            lines.setdefault(who, {}).setdefault(stat, v)
    return {**paid, "lines": lines, "free_fetched_at": free.get("fetched_at")}
