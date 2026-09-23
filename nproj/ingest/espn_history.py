"""Historical backfill for model training and backtesting: every game of a
season with full box scores, the closing spread/total, and, where ESPN has
them, the sportsbook's player prop lines for that game.

Sources (all free, no key):
  site.web.api.espn.com   scoreboard (game ids per date) and summary (box
                          score, starters, DNPs, and sometimes spread/total)
  sports.core.api.espn.com  odds (spread/total by provider) and propBets
                          (player prop lines with prices; checked 2026-09-22:
                          DraftKings for 2023-24, ESPN BET for 2024-25 and the
                          start of 2025-26, none after ESPN BET shut down)

Output, one folder per ESPN season year (2026 = 2025-26), gzipped CSV:
  games.csv.gz  one row per game: date, teams, score, spread (home), total
  box.csv.gz    one row per player per game, including DNPs
  props.csv.gz  one row per prop offer: player, stat, line, side, price.
                Alt lines are kept (only one side priced); the main line is
                derived later (both sides priced, price closest to even).

Run: python -m nproj history --season 2025 --out history
Meant for GitHub Actions (see .github/workflows/backfill.yml): the core API
host may or may not be reachable from there; the run log reports counts so
a blocked host shows up as zero props/odds rather than a crash.
"""
import csv
import gzip
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import requests

from .espn import UA, abbr, et_date

SITE = "https://site.web.api.espn.com/apis/site/v2/sports/basketball/nba"
CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"
PROP_TYPES = {"1": "points", "2": "assists", "3": "rebounds", "90": "pra"}
PROVIDER_PRIORITY = ["40", "58", "100", "45", "47"]   # DraftKings, ESPN BET, DK (new id), Caesars, MGM
WORKERS = 6

BOX_COLS = ["game_id", "date", "season", "playoff", "team", "opp", "home", "player_id", "player",
            "starter", "dnp", "dnp_reason", "minutes", "points", "rebounds", "assists", "oreb", "dreb",
            "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "tov", "stl", "blk", "pf", "plus_minus"]
GAME_COLS = ["game_id", "date", "season", "playoff", "home", "away", "home_score", "away_score",
             "spread_home", "total", "odds_source"]
PROP_COLS = ["game_id", "date", "player_id", "stat", "line", "side", "price", "open_line",
             "open_price", "provider"]

_s = requests.Session()
_s.headers.update({"User-Agent": UA, "Accept": "application/json"})


def _get(url, params=None, tries=4):
    for i in range(tries):
        try:
            r = _s.get(url, params=params, timeout=25)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
        except (requests.RequestException, ValueError):
            pass
        time.sleep(1.5 * (i + 1))
    return None


# ---------------------------------------------------------------- games ----
def season_game_ids(season):
    """[(game_id, date_s, playoff)] for every regular-season and playoff game."""
    d = date(season - 1, 10, 1)
    end = date(season, 6, 30)
    dates = []
    while d <= end:
        dates.append(d)
        d += timedelta(days=1)

    def one(day):
        data = _get(f"{SITE}/scoreboard", {"dates": day.strftime("%Y%m%d")}) or {}
        out = []
        for e in data.get("events", []):
            t = (e.get("season") or {}).get("type")
            done = ((e.get("status") or {}).get("type") or {}).get("completed")
            if t in (2, 3) and done:
                out.append((str(e["id"]), et_date(e["date"]).strftime("%Y-%m-%d"), t == 3))
        return out

    with ThreadPoolExecutor(WORKERS) as ex:
        ids = [g for day in ex.map(one, dates) for g in day]
    seen, out = set(), []
    for g in ids:
        if g[0] not in seen:
            seen.add(g[0])
            out.append(g)
    return out


def _int(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


def _made_att(x):
    try:
        m, a = str(x).split("-")
        return int(m), int(a)
    except ValueError:
        return None, None


def parse_summary(summary, game_id, date_s, season, playoff):
    """-> (game row, [box rows]) from a site-API summary."""
    comp = summary["header"]["competitions"][0]
    side = {c["homeAway"]: c for c in comp["competitors"]}
    home, away = abbr(side["home"]["team"]["abbreviation"]), abbr(side["away"]["team"]["abbreviation"])
    pc = (summary.get("pickcenter") or [{}])[0]
    game = {"game_id": game_id, "date": date_s, "season": season, "playoff": int(playoff),
            "home": home, "away": away,
            "home_score": _int(side["home"].get("score")), "away_score": _int(side["away"].get("score")),
            "spread_home": pc.get("spread"), "total": pc.get("overUnder"),
            "odds_source": (pc.get("provider") or {}).get("name") if pc else None}
    rows = []
    for team_block in summary.get("boxscore", {}).get("players", []):
        team = abbr(team_block["team"]["abbreviation"])
        opp = away if team == home else home
        st = team_block["statistics"][0]
        idx = {k: i for i, k in enumerate(st.get("keys", []))}
        for a in st.get("athletes", []):
            s = a.get("stats") or []
            dnp = bool(a.get("didNotPlay")) or not s

            def g(key):
                return s[idx[key]] if (not dnp and key in idx and idx[key] < len(s)) else None
            fgm, fga = _made_att(g("fieldGoalsMade-fieldGoalsAttempted"))
            f3m, f3a = _made_att(g("threePointFieldGoalsMade-threePointFieldGoalsAttempted"))
            ftm, fta = _made_att(g("freeThrowsMade-freeThrowsAttempted"))
            rows.append({
                "game_id": game_id, "date": date_s, "season": season, "playoff": int(playoff),
                "team": team, "opp": opp, "home": int(team == home),
                "player_id": str(a["athlete"]["id"]), "player": a["athlete"].get("displayName"),
                "starter": int(bool(a.get("starter"))), "dnp": int(dnp),
                "dnp_reason": a.get("reason") if dnp else None,
                "minutes": _int(g("minutes")), "points": _int(g("points")),
                "rebounds": _int(g("rebounds")), "assists": _int(g("assists")),
                "oreb": _int(g("offensiveRebounds")), "dreb": _int(g("defensiveRebounds")),
                "fgm": fgm, "fga": fga, "fg3m": f3m, "fg3a": f3a, "ftm": ftm, "fta": fta,
                "tov": _int(g("turnovers")), "stl": _int(g("steals")), "blk": _int(g("blocks")),
                "pf": _int(g("fouls")), "plus_minus": _int(str(g("plusMinus") or "").replace("+", "")),
            })
    return game, rows


# ----------------------------------------------------------------- odds ----
def _core_url(u):
    return u.replace("http://", "https://")


def core_odds(game_id):
    """-> (spread_home, total, provider_name, [provider ids]) from the core API."""
    data = _get(f"{CORE}/events/{game_id}/competitions/{game_id}/odds")
    if not data:
        return None, None, None, []
    items = data.get("items", [])
    ids = [str(i["provider"]["id"]) for i in items if i.get("provider")]
    for it in items:
        if it.get("spread") is not None:
            # core 'spread' is the home line (DAL -4 away fav -> spread 4)
            return it.get("spread"), it.get("overUnder"), it["provider"].get("name"), ids
    return None, None, None, ids


def parse_prop_item(it, game_id, date_s, provider):
    stat = PROP_TYPES.get(str((it.get("type") or {}).get("id")))
    if not stat:
        return None
    cur = it.get("current") or {}
    side = "over" if "over" in cur else "under" if "under" in cur else None
    target = (cur.get("target") or {}).get("value")
    if side is None or target is None:
        return None
    price = _int(str(cur[side].get("american", "")).replace("+", ""))
    op = it.get("open") or {}
    ath = (it.get("athlete") or {}).get("$ref", "")
    pid = ath.split("/athletes/")[1].split("?")[0] if "/athletes/" in ath else None
    if not pid or price is None:
        return None
    return {"game_id": game_id, "date": date_s, "player_id": pid, "stat": stat, "line": target,
            "side": side, "price": price,
            "open_line": (op.get("target") or {}).get("value"),
            "open_price": _int(str((op.get(side) or {}).get("american", "")).replace("+", "")),
            "provider": provider}


def game_props(game_id, date_s, provider_ids):
    """Prop rows from the first provider (in priority order) that has any."""
    order = [p for p in PROVIDER_PRIORITY if p in provider_ids] + \
            [p for p in provider_ids if p not in PROVIDER_PRIORITY]
    for p in order:
        rows, page, pages = [], 1, 1
        while page <= pages:
            data = _get(f"{CORE}/events/{game_id}/competitions/{game_id}/odds/{p}/propBets",
                        {"limit": 1000, "page": page})
            if not data:
                break
            pages = data.get("pageCount", 1)
            for it in data.get("items", []):
                r = parse_prop_item(it, game_id, date_s, p)
                if r:
                    rows.append(r)
            page += 1
        if rows:
            return rows
    return []


# --------------------------------------------------------------- driver ----
def backfill_season(season, out_dir, with_props=True, limit=None):
    out = Path(out_dir) / str(season)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    ids = season_game_ids(season)
    if limit:
        ids = ids[:limit]
    print(f"[history] season {season}: {len(ids)} games found in {time.time() - t0:.0f}s", flush=True)

    def one(g):
        gid, d, po = g
        summ = _get(f"{SITE}/summary", {"event": gid})
        if not summ:
            return None, [], []
        try:
            game, box = parse_summary(summ, gid, d, season, po)
        except (KeyError, IndexError, TypeError):
            return None, [], []
        props = []
        if with_props:
            sp, tot, src, provs = core_odds(gid)
            if game["spread_home"] is None and sp is not None:
                game.update(spread_home=sp, total=tot, odds_source=src)
            props = game_props(gid, d, provs) if provs else []
        return game, box, props

    games, box, props = [], [], []
    with ThreadPoolExecutor(WORKERS) as ex:
        for i, (g, b, p) in enumerate(ex.map(one, ids), 1):
            if g:
                games.append(g)
                box += b
                props += p
            if i % 200 == 0:
                print(f"[history]   {i}/{len(ids)} games, {len(props)} prop rows, "
                      f"{time.time() - t0:.0f}s", flush=True)

    for name, cols, rows in (("games", GAME_COLS, games), ("box", BOX_COLS, box),
                             ("props", PROP_COLS, props)):
        with gzip.open(out / f"{name}.csv.gz", "wt", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
    summary = {"season": season, "games": len(games), "box_rows": len(box), "prop_rows": len(props),
               "games_with_spread": sum(1 for g in games if g["spread_home"] is not None),
               "games_with_props": len({p["game_id"] for p in props}),
               "seconds": round(time.time() - t0)}
    print(f"[history] done: {summary}", flush=True)
    return summary
