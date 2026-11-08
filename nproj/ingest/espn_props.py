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
TRIPLE_DOUBLE = "93"        # "To Record a Triple Double": one Yes price per player
# 3-pointers made (Robin, 2026-11-08: step 1 of a 3PM model). Checked on the
# Nov 7 feed: type 4 "Total 3-Point Field Goals" (main line, over first, same
# shape as points) and type 221 "3-Point Field Goals Milestones" ("N+").
# Kept apart from the PRA stats ("threes" in espn_lines.json), so nothing
# downstream changes, and archived every run to history/<season>/threes_props.csv.gz
# (the last price before each game tips) for the 3PM model's backtests.
THREES_MAIN = {"4": "threes"}
THREES_MILESTONE = {"221": "threes"}


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


def parse_game(items, names, main=None, milestone=None, by_id=False):
    """items -> (lines, alts) keyed by norm_name (or ESPN id with by_id).
    names: {espn_id: name}. main/milestone: type maps (default: the PRA stats)."""
    from .odds import norm_name
    MAIN, MILESTONE = main or globals()["MAIN"], milestone or globals()["MILESTONE"]
    key = (lambda pid: pid) if by_id else (lambda pid: norm_name(names[pid]))
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
        lines.setdefault(key(pid), {})[stat] = {
            "line": line, "over": over, "under": under, "book": BOOK, "source": "ESPN"}
    for (pid, stat), lad in ladders.items():
        alts.setdefault(key(pid), {})[stat] = [
            {"line": ln, "over": p, "book": BOOK} for ln, p in sorted(lad.items())]
    return lines, alts


def parse_td(items, names):
    """Triple-double Yes prices -> {norm_name: {"odds", "book"}} (Jokic tab)."""
    from .odds import norm_name
    out = {}
    for it in items:
        if str((it.get("type") or {}).get("id")) != TRIPLE_DOUBLE:
            continue
        ref = (it.get("athlete") or {}).get("$ref", "")
        pid = ref.split("/athletes/")[1].split("?")[0] if "/athletes/" in ref else None
        price = _american(((it.get("odds") or {}).get("american") or {}).get("value"))
        if pid in names and price is not None:
            out[norm_name(names[pid])] = {"odds": price, "book": BOOK}
    return out


def fetch(con, date_s):
    """Pull every game's DraftKings props for date_s from ESPN (free).
    -> {"date", "fetched_at", "games": {"AWY @ HOM": n_players}, "lines", "alts"}."""
    from .odds import norm_name as norm
    names = {str(r["player_id"]): r["name"] for r in con.execute("SELECT player_id, name FROM players")}
    games = con.execute("SELECT game_id, home_team, away_team FROM games WHERE date=?", (date_s,)).fetchall()
    out = {"date": date_s, "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "source": "ESPN (DraftKings)", "games": {}, "lines": {}, "alts": {}, "td": {},
           "threes": {"lines": {}, "alts": {}}, "_threes_rows": []}
    empty = 0
    for g in games:
        items = _items(g["game_id"])
        empty = 0 if items else empty + 1
        if empty >= 2 and not out["lines"]:
            break                   # nothing posted (or ESPN down): don't wait on every game
        lines, alts = parse_game(items, names)
        out["td"].update(parse_td(items, names))
        tl, ta = parse_game(items, names, THREES_MAIN, THREES_MILESTONE, by_id=True)
        for pid, per in tl.items():
            ln = per["threes"]
            out["threes"]["lines"][norm(names[pid])] = ln
            out["_threes_rows"].append({"game_id": str(g["game_id"]), "player_id": pid, "player": names[pid],
                                        "kind": "main", "line": ln["line"], "over": ln["over"],
                                        "under": ln["under"]})
        for pid, per in ta.items():
            out["threes"]["alts"][norm(names[pid])] = per["threes"]
            for st in per["threes"]:
                out["_threes_rows"].append({"game_id": str(g["game_id"]), "player_id": pid,
                                            "player": names[pid], "kind": "rung", "line": st["line"],
                                            "over": st["over"], "under": None})
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
    data = {k: v for k, v in data.items() if not k.startswith("_")}
    with open(path(), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write("\n")


ARCHIVE_COLS = ["date", "game_id", "player_id", "player", "kind", "line", "over", "under", "fetched_at"]


def archive_threes(con, data, root=None, now=None):
    """Add this pull's 3PM prices to history/<season>/threes_props.csv.gz, one
    row per (date, game, player, main/rung, line). A newer pull replaces the
    row, until the game tips: after that the row is frozen, so the file keeps
    the last price before tip-off (close to the closing line). -> rows changed."""
    import gzip
    import csv
    import io
    from datetime import date as _date
    from .espn import season_year
    rows = data.get("_threes_rows") or []
    if not rows:
        return 0
    now = now or datetime.now(timezone.utc)
    started = set()
    for r in con.execute("SELECT game_id, tipoff_utc FROM games WHERE date=?", (data["date"],)):
        try:
            tip = datetime.fromisoformat(str(r["tipoff_utc"]).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if tip.tzinfo is None:
            tip = tip.replace(tzinfo=timezone.utc)
        if tip <= now:
            started.add(str(r["game_id"]))
    season = season_year(_date.fromisoformat(data["date"]))
    if root is None:
        import os
        from pathlib import Path
        root = Path(os.environ.get("NPROJ_HISTORY_DIR", Path(config.ROOT) / "history"))
    f = root / str(season) / "threes_props.csv.gz"
    old = []
    if f.exists():
        with gzip.open(f, "rt", encoding="utf-8", newline="") as fh:
            old = list(csv.DictReader(fh))
    key = lambda r: (r["date"], str(r["game_id"]), str(r["player_id"]), r["kind"], f"{float(r['line']):.1f}")
    table = {key(r): r for r in old}
    changed = 0
    for r in rows:
        if str(r["game_id"]) in started:
            continue                              # game under way: keep the pre-tip price
        new = {**r, "date": data["date"], "fetched_at": data["fetched_at"],
               "under": "" if r.get("under") is None else r["under"]}
        k = key(new)
        prev = table.get(k)
        if prev and str(prev.get("over")) == str(new["over"]) and str(prev.get("under") or "") == str(new["under"]):
            continue                              # same price: no change (no new commit)
        table[k] = new
        changed += 1
    if changed:
        f.parent.mkdir(parents=True, exist_ok=True)
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=ARCHIVE_COLS, extrasaction="ignore")
        w.writeheader()
        for k in sorted(table):
            w.writerow(table[k])
        with open(f, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as fh:
            fh.write(buf.getvalue().encode("utf-8"))      # mtime=0: same bytes for same rows
    return changed


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
    n = len(data["lines"]) + len(data["alts"]) + len(data["threes"]["lines"]) + len(data["threes"]["alts"])
    same = lambda d: json.dumps({k: d.get(k) for k in ("lines", "alts", "td", "threes")}, sort_keys=True)
    if n and same(data) != same(load(date_s)):        # unchanged prices: no new commit
        save(data)
    try:
        archived = archive_threes(con, data)
    except Exception as exc:  # noqa: BLE001 - the archive must never block the board
        archived = f"failed: {exc}"
    return {"date": date_s, "players_with_lines": len(data["lines"]),
            "threes_lines": len(data["threes"]["lines"]), "threes_archived": archived,
            "players_with_ladders": len(data["alts"]),
            "games_with_props": sum(1 for v in data["games"].values() if v["lines"] or v["ladders"]),
            "games": len(data["games"])}


def by_teams(data):
    """{(home, away): {"line_stats": set, "ladder_stats": set}} from a pull."""
    return {(g["home"], g["away"]): {"line_stats": set(g.get("line_stats", [])),
                                     "ladder_stats": set(g.get("ladder_stats", []))}
            for g in (data.get("games") or {}).values() if "home" in g}
