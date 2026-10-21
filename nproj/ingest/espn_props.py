"""Free check: does ESPN publish NBA player props again?

ESPN's core API served sportsbook player props (propBets) for free: DraftKings
for 2023-24 and ESPN BET for 2024-25 (our history/ prop archive came from
there). They stopped when ESPN BET shut down (checked 2026-09-22). ESPN now
partners with DraftKings, and an open-source project reported DraftKings
milestone props ("8+ points" style, one-way prices) back under provider 100 in
October 2026. If that holds for the NBA, it is a free alt ladder for every
game, which would free Odds API credits for main lines.

This only LOOKS: each morning odds run saves docs/data/espn_props_probe.json
(what providers ESPN lists, how many prop items, their types, a few raw
samples, and how many parse as priced over lines). Nothing in the board or
the parlays uses it until the samples show the format.
"""
import json
from datetime import datetime, timezone

from .. import config
from .espn_history import CORE, PROP_TYPES, _get

PROBE_GAMES = 3
TRY_PROVIDERS = ["100", "40", "58"]


def _summ(it):
    t = it.get("type") or {}
    return f"{t.get('id')}:{t.get('name') or t.get('displayName') or ''}"


def _parsed(it):
    cur = it.get("current") or {}
    tgt = (cur.get("target") or {}).get("value")
    over = (cur.get("over") or {}).get("american")
    return tgt is not None and over not in (None, "")


def probe(con, date_s):
    games = [r["game_id"] for r in con.execute(
        "SELECT game_id FROM games WHERE date=? ORDER BY tipoff_utc", (date_s,)).fetchall()][:PROBE_GAMES]
    out = {"checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "date": date_s,
           "games": []}
    found = 0
    for gid in games:
        g = {"game_id": gid}
        odds = _get(f"{CORE}/events/{gid}/competitions/{gid}/odds") or {}
        provs = [str(i["provider"]["id"]) for i in odds.get("items", []) if i.get("provider")]
        g["providers"] = {str(i["provider"]["id"]): i["provider"].get("name")
                          for i in odds.get("items", []) if i.get("provider")}
        g["props"] = {}
        for p in dict.fromkeys(TRY_PROVIDERS + provs):
            data = _get(f"{CORE}/events/{gid}/competitions/{gid}/odds/{p}/propBets", {"limit": 1000})
            if not data:
                g["props"][p] = {"items": 0}
                continue
            items = data.get("items", [])
            types = {}
            for it in items:
                types[_summ(it)] = types.get(_summ(it), 0) + 1
            usable = [it for it in items if str((it.get("type") or {}).get("id")) in PROP_TYPES and _parsed(it)]
            g["props"][p] = {"items": len(items), "pages": data.get("pageCount"), "types": types,
                             "priced_pts_reb_ast_pra": len(usable),
                             "sample": items[:3]}
            found += len(items)
        out["games"].append(g)
    path = config.SITE_DATA_DIR / "espn_props_probe.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
        f.write("\n")
    return {"games": len(games), "prop_items": found}
