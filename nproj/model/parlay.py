"""Parlay of the Day: pick 3-4 overs for a combined ~+200 to +320 parlay at
real sportsbook prices, and settle yesterday's parlay from real box scores.

PRICE-FIRST (2026-10-21 on; Robin, 2026-10-20). When the PRA model ran, the
pick uses the same engine as the early-look parlay (nproj/model/early_parlay.py):
every alt or main-line over for the night's top players, priced -400 to -120,
the model projecting over the line, not 10+ points below the book; then the
3-4 leg combination at about +200 to +320 that the model likes most relative
to the book (gaps capped at +8, see early_parlay). Prices come from, oldest to
newest: the early-look ladders bought a day ahead (PRA, points), this
morning's main lines, this morning's alt ladders, and a manual top-up's
ladders; a newer price for the same line wins. The older "most likely overs"
method below is kept only for days the model didn't run.

Manual top-up (a manual run with the odds box ticked after today's lines were
bought): fresh ladders for games not started yet, then today's parlay is
re-picked from games tipping 15+ minutes later. The morning pick is kept on
the page as "replaced" and only the re-pick is graded.

Older method notes:

Conviction, not EV. A leg qualifies when the model gives it a high chance
of hitting, the player's minutes are steady, and the game isn't likely to
be a blowout. Legs are then added in order of model probability until the
combined price reaches the target range.

How the probability works: when the PRA model ran (the normal case), each
leg's chance is the model's probability blended with the sportsbook price
(nproj/model/live.py), and a leg must also be at least a fair price by that
number (no negative-value legs). If the model didn't run, it falls back to
each player's recent games on a normal curve, pulled 15% toward a coin flip.

Overs only (Robin, 2026-09-23: "something to cheer for"). PARLAY_SIDES
(env NPROJ_PARLAY_SIDES, default "over") can bring unders back. Alt overs
are the heart of it: a lower line the model is at least MODEL_ALT_PROB sure
of, at a shorter price. Main-line overs qualify only at MODEL_MIN_PROB_MAIN.
"Most likely overs", not value (Robin's choice, 2026-09-23): legs are ranked
by chance of hitting, with no fair-price rule, so expect a small long-run
loss of about the sportsbook's margin. Two guards keep it sensible: the
model's own projection must be above the line (it agrees it's an over), and
no leg priced shorter than MIN_LEG_ODDS (a -475 leg barely moves the payout).

Budget (see nproj/ingest/odds.py): the main prop lines are already bought
by the morning odds run. Whatever is left of the day's allowance buys
alt-line ladders for the most promising overs (1 credit per game per stat,
at most MAX_ALT_CREDITS), picked by the model's chance at the MAIN line
whether or not that clears the main-line bar, since a player at 50% on the
main line can be 80% on a lower alt. Spreads for the blowout check come
free from ESPN's schedule; the Odds API is only asked for them (1 credit)
when ESPN has none. Nothing is bought outside the morning run.

State lives in docs/data/parlay.json (committed): today's parlay and the
settled history. The daily run is stateless, so settling re-pulls the
legs' game logs from ESPN (free).
"""
import json
import math
from datetime import datetime, timezone
from statistics import NormalDist

from .. import config, util
from ..ingest import odds
from ..model import predict

STATS = ("points", "rebounds", "assists", "pra")
STAT_LABEL = {"points": "points", "rebounds": "rebounds", "assists": "assists", "pra": "PRA"}

MIN_PROB_MAIN = 0.62        # fallback method: chance needed for a main-line leg
ALT_TARGET_PROB = 0.78      # fallback method: highest alt line at least this likely
MODEL_MIN_PROB_MAIN = 0.60  # PRA model: blended chance needed for a main-line over
MODEL_ALT_PROB = 0.70       # PRA model: highest alt line at least this likely
MODEL_MIN_EDGE = None       # no fair-price rule ("most likely overs"); a number brings it back
MIN_LEG_ODDS = -350         # skip legs priced shorter than this
SHRINK = 0.85               # pull probabilities toward 50%; small samples overstate certainty
MIN_GAMES = 8               # need at least this many recent games to trust the spread
MIN_LEGS, MAX_LEGS = 2, 4
TARGET_LO, TARGET_HI = 200, 320
MIN_ACCEPT = 150            # below this combined price, skip the day rather than force it
MAX_PER_GAME = 2
MAX_ALT_CREDITS = 4
import os as _os
PARLAY_SIDES = tuple(x for x in _os.environ.get("NPROJ_PARLAY_SIDES", "over").split(",") if x)
HISTORY_KEEP = 60
NOTE = ("Overs only, 3-4 legs at about +200 to +320 real odds on FanDuel/DraftKings, picked each "
        "morning from the night's top players: the model must project over the line and not rate "
        "the leg well below the book, and the combination it likes most relative to the book wins "
        "(the +/- number; gaps above +8 count as +8, since bigger gaps weren't better historically). "
        "Legs stay fixed once picked, unless a manual top-up re-picks from fresh prices. Settled the "
        "next morning from the real box scores. It's for fun: expect the book's cut over time.")
REPICK_LEAD_MIN = 15        # a re-pick only uses games tipping at least this far ahead

_N = NormalDist()


# ---------------------------------------------------------------- math ----
def to_decimal(american):
    a = int(american)
    return 1 + a / 100.0 if a > 0 else 1 + 100.0 / abs(a)


def combined_odds(american_odds):
    """Combined American odds of a parlay."""
    dec = 1.0
    for o in american_odds:
        dec *= to_decimal(o)
    return round((dec - 1) * 100) if dec >= 2 else round(-100 / (dec - 1))


def prob_over(mean, std, line):
    p = 1 - _N.cdf((line - mean) / std)
    return 0.5 + SHRINK * (p - 0.5)


# ------------------------------------------------------------ player data ----
def _series(con, player_id, stat, before_date):
    if stat == "pra":
        rows = con.execute(
            """SELECT points + rebounds + assists AS v FROM player_game_logs
               WHERE player_id=? AND date < ? AND points IS NOT NULL
               ORDER BY date DESC LIMIT ?""",
            (player_id, before_date, predict.MAX_GAMES_CONSIDERED)).fetchall()
        return [r["v"] for r in rows]
    return predict._player_game_log(con, player_id, stat, before_date)


def distribution(con, player_id, stat, before_date):
    vals = _series(con, player_id, stat, before_date)
    if len(vals) < MIN_GAMES:
        return None
    mean, std = predict._weighted_stats(vals)
    return mean, max(std, 1.0)


def minutes_confidence(con, player_id, before_date):
    rows = con.execute(
        """SELECT minutes FROM player_game_logs WHERE player_id=? AND date < ?
           AND minutes IS NOT NULL ORDER BY date DESC LIMIT 10""",
        (player_id, before_date)).fetchall()
    mins = [r["minutes"] for r in rows]
    if len(mins) < 5:
        return "low", None
    avg = sum(mins) / len(mins)
    cv = math.sqrt(sum((m - avg) ** 2 for m in mins) / len(mins)) / avg if avg else 1
    if avg >= 28 and cv <= 0.20:
        return "high", avg
    if avg >= 24 and cv <= 0.30:
        return "medium", avg
    return "low", avg


def blowout_risk(spread):
    if spread is None:
        return "unknown"
    s = abs(spread)
    return "low" if s < 7 else "medium" if s < 12 else "high"


# ------------------------------------------------------------ candidates ----
def _slate(con, date_s):
    return con.execute(
        """SELECT pp.player_id, pp.game_id, pp.status, p.name, p.team,
                  g.home_team, g.away_team, g.tipoff_utc
           FROM probable_players pp
           JOIN players p ON p.player_id = pp.player_id
           JOIN games g ON g.game_id = pp.game_id
           WHERE pp.date = ?""", (date_s,)).fetchall()


def espn_spreads(con, date_s):
    """Team -> point spread from ESPN's schedule (free). Home spread is
    stored per game; the away team gets the opposite."""
    out = {}
    for g in con.execute("SELECT home_team, away_team, spread_home FROM games WHERE date=?", (date_s,)):
        if g["spread_home"] is not None:
            out[g["home_team"]], out[g["away_team"]] = g["spread_home"], -g["spread_home"]
    return out


def candidates(con, date_s, saved, qualify=True):
    """Main-line options, one dict per player/stat/side. qualify=False keeps
    every over with a line (no probability bar): the pool alt lines are
    bought for and searched in."""
    from ..export.site_export import _time_et

    spreads = saved.get("spreads") or espn_spreads(con, date_s)
    slate = _slate(con, date_s)
    matched, _ = odds.match_lines([r["name"] for r in slate], saved.get("lines", {}))
    out = []
    for r in slate:
        if (r["status"] or "active") != "active":
            continue  # out, day-to-day, questionable: minutes too uncertain
        mine = matched.get(r["name"])
        if not mine:
            continue
        from . import live
        model_on = str(r["player_id"]) in live.PROJ
        if model_on:        # the model's own minutes projection
            m = live.PROJ[str(r["player_id"])]["minutes"]
            conf, avg_min = ("high" if m >= 30 else "medium" if m >= 24 else "low"), m
        else:
            conf, avg_min = minutes_confidence(con, r["player_id"], date_s)
        if conf == "low":
            continue
        risk = blowout_risk(spreads.get(r["team"]))
        if risk == "high":
            continue
        home = r["team"] == r["home_team"]
        base = {
            "player": r["name"], "player_id": r["player_id"], "game_id": r["game_id"],
            "team": r["team"], "opp": r["away_team"] if home else r["home_team"], "home": home,
            "time_et": _time_et(r["tipoff_utc"]), "minutes_confidence": conf,
            "avg_minutes": round(avg_min, 1), "minutes_source": "model" if model_on else "recent",
            "blowout_risk": risk,
            "spread": spreads.get(r["team"]),
        }
        for stat in STATS:
            ln = mine.get(stat)
            if not ln or ln.get("line") is None:
                continue
            if model_on:
                view = live.assess(r["player_id"], stat, ln["line"], ln.get("over"), ln.get("under"))
                if not view:
                    continue
                mean, std = live.PROJ[str(r["player_id"])][stat], None
                p_over, floor = view["p_over"], MODEL_MIN_PROB_MAIN
            else:
                dist = distribution(con, r["player_id"], stat, date_s)
                if not dist:
                    continue
                mean, std = dist
                p_over, floor = prob_over(mean, std, ln["line"]), MIN_PROB_MAIN
            for side, p, price in (("over", p_over, ln.get("over")),
                                   ("under", 1 - p_over, ln.get("under"))):
                if side not in PARLAY_SIDES and qualify:
                    continue
                if not qualify and side != "over":
                    continue
                if price is None:
                    continue
                if qualify and not _leg_ok(p, floor, price, mean, ln["line"], side,
                                           model_on and MODEL_MIN_EDGE is not None):
                    continue
                out.append({**base, "stat": stat, "side": side, "line": ln["line"],
                            "book_line": ln["line"], "line_type": "main", "odds": int(price),
                            "book": ln.get("book"), "prob": round(p, 3),
                            "proj": round(mean, 1), "std": round(std, 2) if std else None,
                            "model": model_on})
    return out


def _leg_ok(p, floor, price, proj, line, side, check_edge=False):
    """A leg qualifies: likely enough, not priced too short, and the model's
    own projection on the leg's side of the line."""
    if p < floor or int(price) < MIN_LEG_ODDS:
        return False
    if (side == "over" and proj <= line) or (side == "under" and proj >= line):
        return False
    if check_edge:
        from . import live
        return p - live.implied(price) >= MODEL_MIN_EDGE
    return True


def alt_options(cands, saved):
    """For over options with an alt ladder (pass the unqualified pool, see
    candidates): the highest alt line below the main line that the model is
    still MODEL_ALT_PROB sure of (ALT_TARGET_PROB without the model)."""
    alt_by_name, _ = odds.match_lines({c["player"] for c in cands}, saved.get("alts", {}))
    out = []
    for c in cands:
        if c["side"] != "over":
            continue
        ladder = alt_by_name.get(c["player"], {}).get(c["stat"], [])
        best = None
        for step in ladder:
            if step["line"] >= c["book_line"] or step.get("over") is None:
                continue
            if c.get("model"):
                from . import live
                view = live.assess(c["player_id"], c["stat"], step["line"], step["over"], None)
                p = view["p_over"]
                ok = _leg_ok(p, MODEL_ALT_PROB, step["over"], c["proj"], step["line"], "over",
                             MODEL_MIN_EDGE is not None)
            else:
                p = prob_over(c["proj"], c["std"], step["line"])
                ok = p >= ALT_TARGET_PROB
            if ok and (best is None or step["line"] > best[0]["line"]):
                best = (step, p)
        if best:
            step, p = best
            out.append({**c, "line": step["line"], "line_type": "alt", "odds": int(step["over"]),
                        "book": step["book"], "prob": round(p, 3)})
    return out


# ------------------------------------------------------------- buying extras ----
def buy_extras(con, date_s, saved):
    """Spend leftover morning credits on spreads, then alt ladders. Returns a
    short log dict. Changes `saved` in place and re-saves lines.json."""
    log = {"leftover_before": odds.leftover_credits(saved)}
    if "spreads" not in saved and espn_spreads(con, date_s):
        saved["spreads"] = espn_spreads(con, date_s)      # free, keeps the credit for alts
    if odds.leftover_credits(saved) >= 1 and "spreads" not in saved:
        try:
            saved["spreads"] = odds.spend_extra(saved, odds.fetch_spreads, date_s)
        except Exception as exc:  # noqa: BLE001
            log["spreads_error"] = str(exc)
            saved["spreads"] = {}

    from . import live
    if "alts" not in saved and live.PROJ and saved.get("ladder_events") is not None:
        # price-first: fresh PRA + points ladders for the games the morning plan
        # picked (the ones with the top projected players), see odds.plan_morning
        alts, n = {}, 0
        for event_id in saved["ladder_events"]:
            if odds.leftover_credits(saved) < len(odds.LADDER_STATS):
                break
            try:
                got = odds.spend_extra(saved, odds.fetch_alt_props, event_id, list(odds.LADDER_STATS))
            except Exception as exc:  # noqa: BLE001
                log["alts_error"] = str(exc)
                continue
            n += 1
            for who, per_stat in got.items():
                alts.setdefault(who, {}).update(per_stat)
        saved["alts"] = alts
        saved["alts_fetched_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        log["ladder_games"] = n
    if "alts" not in saved:
        budget = min(MAX_ALT_CREDITS, odds.leftover_credits(saved))
        wanted = {}  # event_id -> [stats]
        overs = sorted(candidates(con, date_s, saved, qualify=False), key=lambda c: -c["prob"])
        for c in overs:
            if budget <= 0:
                break
            ev = next((e for e in saved.get("events", [])
                       if c["team"] in (e["home"], e["away"])), None)
            if not ev or c["stat"] in wanted.get(ev["event_id"], []):
                continue
            wanted.setdefault(ev["event_id"], []).append(c["stat"])
            budget -= 1
        alts = {}
        for event_id, stats in wanted.items():
            try:
                got = odds.spend_extra(saved, odds.fetch_alt_props, event_id, stats)
            except Exception as exc:  # noqa: BLE001
                log["alts_error"] = str(exc)
                continue
            for who, per_stat in got.items():
                alts.setdefault(who, {}).update(per_stat)
        saved["alts"] = alts
        log["alt_requests"] = sum(len(v) for v in wanted.values())
    log["extras_spent"] = saved.get("extras_spent", 0)
    odds.save_lines(saved)
    return log


# ------------------------------------------------------------- selection ----
def select_legs(options):
    """Greedy by model probability: one leg per player, at most MAX_PER_GAME
    per game, never past TARGET_HI, stop once TARGET_LO is reached."""
    legs, price = [], None
    for o in sorted(options, key=lambda o: (-o["prob"], o["line_type"] != "alt")):
        if any(l["player_id"] == o["player_id"] for l in legs):
            continue
        if sum(1 for l in legs if l["game_id"] == o["game_id"]) >= MAX_PER_GAME:
            continue
        trial = combined_odds([l["odds"] for l in legs] + [o["odds"]])
        if trial > TARGET_HI:
            continue
        legs.append(o)
        price = trial
        if (len(legs) >= MIN_LEGS and price >= TARGET_LO) or len(legs) == MAX_LEGS:
            break
    if len(legs) < MIN_LEGS or price is None or price < MIN_ACCEPT:
        return [], None
    return legs, price


def reasoning(leg):
    stat = STAT_LABEL[leg["stat"]]
    word = "clear" if leg["side"] == "over" else "stay under"
    line_desc = (f"the {leg['line']} alt line (full line {leg['book_line']})"
                 if leg["line_type"] == "alt" else f"the {leg['line']} line")
    spread = ("no spread pulled today" if leg["spread"] is None else
              f"{leg['team']} {leg['spread']:+g} spread, {leg['blowout_risk']} blowout risk")
    mins = (f"Projected for {leg['avg_minutes']} minutes" if leg.get("minutes_source") == "model"
            else f"Averaging {leg['avg_minutes']} minutes over his last 10 games")
    return (f"Model projects {leg['proj']} {stat} and gives him a {round(leg['prob'] * 100)}% "
            f"chance to {word} {line_desc}. {mins} ({leg['minutes_confidence']} minutes "
            f"confidence); {spread}.")


def _public_leg(leg):
    keep = ("player", "player_id", "team", "opp", "home", "time_et", "stat", "side", "line",
            "line_type", "book_line", "proj", "prob", "odds", "book", "blowout_risk",
            "minutes_confidence")
    return {**{k: leg[k] for k in keep}, "reasoning": reasoning(leg)}


# ------------------------------------------------------------ price-first ----
def todays_prices(date_s, saved):
    """{name: {stat: ladder}} for today, oldest prices first so newer ones
    win at the same line: early-look ladders (a day ahead), main lines, then
    alt ladders (morning, or a top-up)."""
    merged = {}

    def add(who, stat, steps, src):
        lad = merged.setdefault(who, {}).setdefault(stat, {})
        for st in steps:
            if st.get("over") is None or st.get("line") is None:
                continue
            lad[float(st["line"])] = {"line": st["line"], "over": st["over"], "book": st.get("book"),
                                      "src": src}

    for who, per in (odds.load_early_lines(date_s).get("alts") or {}).items():
        for stat, ladder in per.items():
            add(who, stat, ladder, "early")
    for who, per in (saved.get("lines") or {}).items():
        for stat, ln in per.items():
            if isinstance(ln, dict) and stat in STATS:
                add(who, stat, [ln], "main")
    for who, per in (saved.get("alts") or {}).items():
        for stat, ladder in per.items():
            add(who, stat, ladder, "alt")
    return {who: {stat: sorted(l.values(), key=lambda x: x["line"]) for stat, l in per.items()}
            for who, per in merged.items()}


def price_first(con, date_s, saved, not_before=None):
    """-> (public legs, combined price, number of candidate legs)."""
    from ..export.site_export import _time_et
    from . import early_parlay, live
    spreads = saved.get("spreads") or espn_spreads(con, date_s)
    prices = todays_prices(date_s, saved)
    cands = early_parlay.candidates(con, date_s, live.PROJ, prices, spreads, not_before)
    legs, price = early_parlay.select(cands)
    if not legs:
        return [], None, len(cands)
    mains, _ = odds.match_lines([l["player"] for l in legs], saved.get("lines") or {})
    out = []
    for l in legs:
        main = (mains.get(l["player"]) or {}).get(l["stat"]) or {}
        book_line = main.get("line")
        sp = spreads.get(l["team"])
        mins = live.PROJ.get(str(l["player_id"]), {}).get("minutes")
        leg = {"player": l["player"], "player_id": l["player_id"], "game_id": l["game_id"],
               "team": l["team"], "opp": l["opp"], "home": l["home"],
               "time_et": _time_et(l.get("tipoff_utc")), "stat": l["stat"], "side": "over",
               "line": l["line"], "line_type": "main" if book_line == l["line"] else "alt",
               "book_line": book_line, "proj": l["proj"], "prob": l["model_prob"],
               "book_prob": l["book_prob"], "gap": l["gap"], "odds": l["odds"], "book": l["book"],
               "blowout_risk": blowout_risk(sp),
               "minutes_confidence": "high" if (mins or 0) >= 30 else "medium"}
        stat = STAT_LABEL[l["stat"]]
        spread_txt = "no spread" if sp is None else f"{l['team']} {sp:+g} spread"
        leg["reasoning"] = (
            f"Model projects {l['proj']} {stat}: {round(l['model_prob'] * 100)}% to clear {l['line']} "
            f"(star-calibrated) vs the book's {round(l['book_prob'] * 100)}% at {l['odds']:+d}, "
            f"a {round(l['gap'] * 100):+d} point gap. Projected for {round(mins or 0, 1)} minutes; "
            f"{spread_txt}.")
        out.append(leg)
    return out, price, len(cands)


# ------------------------------------------------------------- settlement ----
def _actual(con, player_id, date_s, stat):
    r = con.execute(
        "SELECT points, rebounds, assists FROM player_game_logs WHERE player_id=? AND date=?",
        (player_id, date_s)).fetchone()
    if not r:
        return None
    return r["points"] + r["rebounds"] + r["assists"] if stat == "pra" else r[stat]


def settle(con, parlay):
    """Grade a finished day's parlay. A leg whose player didn't play is void
    (books drop it); the parlay is graded on the remaining legs."""
    from datetime import date as _date

    from .. import pipeline
    for leg in parlay["legs"]:
        pipeline.load_player_logs(con, leg["player_id"], _date.fromisoformat(parlay["date"]), seasons=1)
    results = []
    for leg in parlay["legs"]:
        actual = _actual(con, leg["player_id"], parlay["date"], leg["stat"])
        if actual is None:
            leg["actual"], leg["hit"], leg["void"] = None, None, True
            continue
        hit = actual > leg["line"] if leg["side"] == "over" else actual < leg["line"]
        leg["actual"], leg["hit"] = actual, hit
        results.append(hit)
    parlay["result"] = ("void" if not results else "win" if all(results) else "loss")
    parlay["status"] = parlay["result"]
    return parlay


# ------------------------------------------------------------- driver ----
def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"history": []}


def save_early(date_s, pick):
    """Store (or replace) the early-look parlay for date_s in parlay.json.
    Re-picked on every daily run until that date becomes today's board."""
    path = config.SITE_DATA_DIR / "parlay.json"
    data = _load(path)
    early = data.setdefault("early", {})
    before = json.dumps(early.get(date_s), sort_keys=True)
    early[date_s] = pick or {"date": date_s, "legs": [], "status": "none",
                             "note": "No early-look parlay: not enough stars cleared the conviction band."}
    after = json.dumps({k: v for k, v in early[date_s].items() if k != "picked_at"}, sort_keys=True)
    was = json.loads(before) if before != "null" else None
    if was and json.dumps({k: v for k, v in was.items() if k != "picked_at"}, sort_keys=True) == after:
        return {"unchanged": date_s}
    data["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return {"saved": date_s, "legs": len(early[date_s]["legs"])}


def update(con, date_s, allow_spend=False, repick=False):
    """Settle any finished parlay, then build (or rebuild) today's. Writes
    docs/data/parlay.json only when something changed. repick: a manual
    top-up run re-picks today's parlay from games not started yet.
    -> summary dict."""
    path = config.SITE_DATA_DIR / "parlay.json"
    data = _load(path)
    before = json.dumps({k: v for k, v in data.items() if k != "generated_at"}, sort_keys=True)
    log = {}

    mock = "MOCKUP" in (data.get("note") or "")
    today_json = data.get("today")

    # 0. grade finished early-look parlays (nproj/model/early_parlay.py)
    for d, ep in sorted((data.get("early") or {}).items()):
        if d < date_s and ep and ep.get("legs") and util.day_is_final(d):
            settled = settle(con, ep)
            data.setdefault("early_history", []).append(settled)
            data["early_history"] = data["early_history"][-HISTORY_KEEP:]
            del data["early"][d]
            log.setdefault("early_settled", []).append({"date": d, "result": settled["result"]})
        elif d < date_s and not (ep and ep.get("legs")):
            del data["early"][d]

    # 1. settle a finished real parlay. A parlay from an earlier date whose
    #    games may still be running (the 8 PM run flips the board to tomorrow)
    #    stays up untouched until it can be graded.
    if (not mock and today_json and today_json.get("legs")
            and today_json.get("status") == "pending" and today_json["date"] < date_s):
        if not util.day_is_final(today_json["date"]):
            log["waiting_to_settle"] = today_json["date"]
            return log
        settled = settle(con, today_json)
        data.setdefault("history", []).append(settled)
        data["history"] = data["history"][-HISTORY_KEEP:]
        data["today"] = None
        log["settled"] = {"date": settled["date"], "result": settled["result"]}

    # 2. build today's parlay, only when today has a slate with lines
    saved = odds.load_lines(date_s)
    has_slate = con.execute("SELECT 1 FROM probable_players WHERE date=?", (date_s,)).fetchone()
    today_json = data.get("today")
    locked = (not mock and ((today_json and today_json.get("date") == date_s)
                            or data.get("no_parlay_date") == date_s))
    from . import live
    if locked and repick and has_slate and saved.get("lines") and live.PROJ:
        # manual top-up: re-pick from fresh prices, only games not started yet
        now = datetime.now(timezone.utc)
        from datetime import timedelta
        legs, price, n = price_first(con, date_s, saved, now + timedelta(minutes=REPICK_LEAD_MIN))
        log["candidates"] = n
        if legs:
            old = data.get("today") if (data.get("today") or {}).get("date") == date_s else None
            data["today"] = {"date": date_s, "combined_odds": price, "status": "pending",
                             "method": "price-first", "legs": legs,
                             "picked_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "repicked": True}
            if old and old.get("legs"):
                data["today"]["replaced"] = {
                    "combined_odds": old.get("combined_odds"),
                    "picked_at": old.get("picked_at"),
                    "legs": [{k: lg.get(k) for k in ("player", "stat", "side", "line", "odds")}
                             for lg in old["legs"]]}
            data.pop("no_parlay_reason", None)
            data.pop("no_parlay_date", None)
            log["repicked"] = {"legs": len(legs), "odds": price}
        else:
            log["repick"] = f"no new parlay ({n} candidate legs); the morning pick stays"
    elif locked:
        # picked this morning: legs never change after that (you may have bet
        # them). Later runs only flag changed legs (nproj/injury_check.py).
        log["locked"] = date_s
    elif has_slate and saved.get("lines"):
        if mock:  # first real game day: drop the hand-made preview (keep early-look parlays)
            data = {"history": [], **{k: data[k] for k in ("early", "early_history") if k in data}}
        if allow_spend:
            log["extras"] = buy_extras(con, date_s, saved)
        now_s = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if live.PROJ:
            # never a game that has started (a late or manual run can pick after the first tip)
            from datetime import timedelta
            soon = datetime.now(timezone.utc) + timedelta(minutes=REPICK_LEAD_MIN)
            legs, price, n = price_first(con, date_s, saved, soon)
            method, pool, cands = "price-first", [], [None] * n
        else:
            cands = candidates(con, date_s, saved)
            pool = candidates(con, date_s, saved, qualify=False)
            legs, price = select_legs(cands + alt_options(pool, saved))
            legs, method = [_public_leg(l) for l in legs], "most-likely"
        log["candidates"] = len(cands)
        if legs:
            data["today"] = {"date": date_s, "combined_odds": price, "status": "pending",
                             "method": method, "picked_at": now_s, "legs": legs}
            data.pop("no_parlay_reason", None)
            data.pop("no_parlay_date", None)
            log["parlay"] = {"legs": len(legs), "odds": price,
                             "alt_legs": sum(1 for l in legs if l["line_type"] == "alt")}
        else:
            data["today"] = None
            data["no_parlay_date"] = date_s
            data["no_parlay_reason"] = (
                f"No parlay today: {len(cands)} overs passed the price range and the model's veto, "
                f"but no 3-4 leg combination reaches +{MIN_ACCEPT}." if method == "price-first" else
                "No parlay today: not enough overs cleared the conviction bar "
                f"({len(cands)} main-line overs, {len(alt_options(pool, saved))} alt overs "
                f"qualified) to reach +{TARGET_LO} without forcing it.")
            log["parlay"] = None
        data["note"] = NOTE

    if data.get("today") and data["today"].get("date") == date_s and data["today"].get("method") == "price-first":
        data["note"] = NOTE
    after = json.dumps({k: v for k, v in data.items() if k != "generated_at"}, sort_keys=True)
    if after != before:
        data["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        log["written"] = True
    return log
