"""Free DraftKings player props from ESPN's core API (provider 100).

Checked 2026-10-21 (Robin's request, from her computer's browser): every NBA
game that night had DraftKings props on ESPN, about 1,100-1,400 items a game:
  main lines  type 1 points, 3 rebounds, 2 assists, 90 PRA: two items per
              player at the same target, OVER FIRST then under (280 of 290
              pairs matched the over price on the matching milestone; the
              rest were within a few cents). We still check against the
              milestone when there is one.
  milestones  type 218 points, 220 rebounds, 219 assists, 225 PRA: "N+" with
              one over price each (= an alt over at N - 0.5). That is the
              same thing as The Odds API's alt ladders we pay credits for.
Some players' rebounds milestones mix in a second, shorter ladder under the
same type (e.g. 2+ to 7+ alongside 6+ to 20+, with 6+ at -3800 in one and
+454 in the other). Only the full-game ladder is kept: the longest set whose
chance falls as the line rises (clean_ladder).
Athletes come as ESPN ids, the same ids as the rest of the pipeline.
Timing: the night before (tonight's props were posted about 10 PM Eastern the
evening before); two days out only team totals were up.

docs/data/espn_lines.json holds the latest pull for the board date. It is a
separate file from lines.json (the paid lines), so a free pull never looks
like "today's lines were already bought".
"""
import json
from datetime import datetime, timezone

from .. import config
from .espn_history import CORE, _get

PROVIDER = "100"            # DraftKings on ESPN
MAIN = {"1": "points", "3": "rebounds", "2": "assists", "90": "pra"}
MILESTONE = {"218": "points", "220": "rebounds", "219": "assists", "225": "pra"}
BOOK = "DraftKings"


def _american(v):
    try:
        return int(str(v).replace("+", ""))
    except (TypeError, ValueError):
        return None


def _items(game_id):
    out, page, pages = [], 1, 1
    while page <= pages:
        data = _get(f"{CORE}/events/{game_id}/competitions/{game_id}/odds/{PROVIDER}/propBets",
                    {"limit": 1000, "page": page})
        if not data:
            break
        pages = data.get("pageCount") or 1
        out += data.get("items", [])
        page += 1
    return out


def _chance(american):
    return -american / (-american + 100) if american < 0 else 100 / (american + 100)


def clean_ladder(entries):
    """[(target, price), ...] in feed order -> {line: price} for the single
    consistent ladder: one price per line, the chance strictly falling as the
    line rises, keeping the most rungs (longest decreasing chain)."""
    pts = sorted(set(entries), key=lambda e: (e[0], -_chance(e[1])))
    n = len(pts)
    best, prev = [1] * n, [-1] * n
    for i in range(n):
        for j in range(i):
            if pts[j][0] < pts[i][0] and _chance(pts[j][1]) > _chance(pts[i][1]) and best[j] + 1 > best[i]:
                best[i], prev[i] = best[j] + 1, j
    if not n:
        return {}
    i = max(range(n), key=lambda k: best[k])
    out = {}
    while i >= 0:
        out[pts[i][0] - 0.5] = pts[i][1]
        i = prev[i]
    return out


def parse_game(items, names):
    """items -> (lines, alts) keyed by norm_name. names: {espn_id: name}."""
    from .odds import norm_name
    mains, ladders = {}, {}
    for it in items:
        t = str((it.get("type") or {}).get("id"))
        if t not in MAIN and t not in MILESTONE:
            continue
        ref = (it.get("athlete") or {}).get("$ref", "")
        pid = ref.split("/athletes/")[1].split("?")[0] if "/athletes/" in ref else None
        tgt = ((it.get("current") or {}).get("target") or {}).get("value")
        price = _american(((it.get("odds") or {}).get("american") or {}).get("value"))
        if not pid or pid not in names or tgt is None or price is None:
            continue
        if t in MAIN:
            mains.setdefault((pid, MAIN[t]), []).append((float(tgt), price))
        else:
            ladders.setdefault((pid, MILESTONE[t]), []).append((float(tgt), price))
    ladders = {k: clean_ladder(v) for k, v in ladders.items()}
    lines, alts = {}, {}
    for (pid, stat), pair in mains.items():
        if len(pair) != 2 or pair[0][0] != pair[1][0]:
            continue
        line, (a, b) = pair[0][0], (pair[0][1], pair[1][1])
        over, under = a, b
        ms = ladders.get((pid, stat), {}).get(line)
        if ms is not None and abs(ms - b) < abs(ms - a):
            over, under = b, a
        lines.setdefault(norm_name(names[pid]), {})[stat] = {
            "line": line, "over": over, "under": under, "book": BOOK, "source": "ESPN"}
    for (pid, stat), lad in ladders.items():
        alts.setdefault(norm_name(names[pid]), {})[stat] = [
            {"line": ln, "over": p, "book": BOOK} for ln, p in sorted(lad.items())]
    return lines, alts


def fetch(con, date_s):
    """Pull every game's DraftKings props for date_s from ESPN (free).
    -> {"date", "fetched_at", "games": {"AWY @ HOM": n_players}, "lines", "alts"}."""
    names = {str(r["player_id"]): r["name"] for r in con.execute("SELECT player_id, name FROM players")}
    games = con.execute("SELECT game_id, home_team, away_team FROM games WHERE date=?", (date_s,)).fetchall()
    out = {"date": date_s, "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "source": "ESPN (DraftKings)", "games": {}, "lines": {}, "alts": {}}
    empty = 0
    for g in games:
        items = _items(g["game_id"])
        empty = 0 if items else empty + 1
        if empty >= 2 and not out["lines"]:
            break                   # nothing posted (or ESPN down): don't wait on every game
        lines, alts = parse_game(items, names)
        count = lambda d: {st: sum(1 for per in d.values() if st in per) for st in MAIN.values()}
        out["games"][f"{g['away_team']} @ {g['home_team']}"] = {
            "home": g["home_team"], "away": g["away_team"], "lines": len(lines), "ladders": len(alts),
            "line_stats": sorted(k for k, v in count(lines).items() if v >= 3),
            "ladder_stats": sorted(k for k, v in count(alts).items() if v >= 3)}
        for who, per in lines.items():
            out["lines"].setdefault(who, {}).update(per)
        for who, per in alts.items():
            out["alts"].setdefault(who, {}).update(per)
    return out


def path():
    return config.SITE_DATA_DIR / "espn_lines.json"


def save(data):
    with open(path(), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write("\n")


def load(date_s):
    try:
        with open(path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if data.get("date") == date_s else {}


def refresh(con, date_s):
    """Fetch and save if ESPN has anything for date_s (an empty pull never
    replaces a good one). -> log dict."""
    data = fetch(con, date_s)
    n = len(data["lines"]) + len(data["alts"])
    same = lambda d: json.dumps({k: d.get(k) for k in ("lines", "alts")}, sort_keys=True)
    if n and same(data) != same(load(date_s)):        # unchanged prices: no new commit
        save(data)
    return {"date": date_s, "players_with_lines": len(data["lines"]),
            "players_with_ladders": len(data["alts"]),
            "games_with_props": sum(1 for v in data["games"].values() if v["lines"] or v["ladders"]),
            "games": len(data["games"])}


def by_teams(data):
    """{(home, away): {"line_stats": set, "ladder_stats": set}} from a pull."""
    return {(g["home"], g["away"]): {"line_stats": set(g.get("line_stats", [])),
                                     "ladder_stats": set(g.get("ladder_stats", []))}
            for g in (data.get("games") or {}).values() if "home" in g}
