"""Parlay of the Day: pick 2-4 high-conviction legs for a combined ~+200 to
+300 parlay, and settle yesterday's parlay from real box scores.

Conviction, not EV. A leg qualifies when the model gives it a high chance
of hitting, the player's minutes are steady, and the game isn't likely to
be a blowout. Legs are then added in order of model probability until the
combined price reaches the target range.

How the model probability works: each player's recent games (same recency
weighting as the Today board) give a mean and spread for the stat; the
chance of clearing a line is read off a normal curve, then pulled 15% of
the way back toward a coin flip, because a 20-game sample overstates how
sure anyone can be.

Budget (see nproj/ingest/odds.py): the main prop lines are already bought
by the morning odds run. Whatever is left of the day's allowance buys, in
order:
  1. point spreads for the whole slate (1 credit), for the blowout check;
  2. alt-line ladders for the best few over candidates (1 credit per
     game per stat, at most MAX_ALT_CREDITS). With an alt ladder, a leg
     can use a lower line the model is ~80% sure of, at a shorter price,
     which is the original alt-line design. Without one, legs use the
     main line, so fewer legs reach the target price.
Nothing is bought outside the morning run.

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

MIN_PROB_MAIN = 0.62        # model chance needed for a main-line leg
ALT_TARGET_PROB = 0.78      # take the highest alt line the model is at least this sure of
SHRINK = 0.85               # pull probabilities toward 50%; small samples overstate certainty
MIN_GAMES = 8               # need at least this many recent games to trust the spread
MIN_LEGS, MAX_LEGS = 2, 4
TARGET_LO, TARGET_HI = 200, 320
MIN_ACCEPT = 150            # below this combined price, skip the day rather than force it
MAX_PER_GAME = 2
MAX_ALT_CREDITS = 4
HISTORY_KEEP = 60

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


def candidates(con, date_s, saved):
    """Every qualifying main-line option: one dict per player/stat/side."""
    from ..export.site_export import _time_et

    spreads = saved.get("spreads", {})
    slate = _slate(con, date_s)
    matched, _ = odds.match_lines([r["name"] for r in slate], saved.get("lines", {}))
    out = []
    for r in slate:
        if (r["status"] or "active") != "active":
            continue  # out, day-to-day, questionable: minutes too uncertain
        mine = matched.get(r["name"])
        if not mine:
            continue
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
            "avg_minutes": round(avg_min, 1), "blowout_risk": risk,
            "spread": spreads.get(r["team"]),
        }
        for stat in STATS:
            ln = mine.get(stat)
            dist = distribution(con, r["player_id"], stat, date_s)
            if not ln or ln.get("line") is None or not dist:
                continue
            mean, std = dist
            p_over = prob_over(mean, std, ln["line"])
            for side, p, price in (("over", p_over, ln.get("over")),
                                   ("under", 1 - p_over, ln.get("under"))):
                if price is None or p < MIN_PROB_MAIN:
                    continue
                out.append({**base, "stat": stat, "side": side, "line": ln["line"],
                            "book_line": ln["line"], "line_type": "main", "odds": int(price),
                            "book": ln.get("book"), "prob": round(p, 3),
                            "proj": round(mean, 1), "std": round(std, 2)})
    return out


def alt_options(cands, saved):
    """For over candidates with an alt ladder: the highest alt line below the
    main line that the model is still ALT_TARGET_PROB sure of."""
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
            p = prob_over(c["proj"], c["std"], step["line"])
            if p >= ALT_TARGET_PROB and (best is None or step["line"] > best[0]["line"]):
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
    if odds.leftover_credits(saved) >= 1 and "spreads" not in saved:
        try:
            saved["spreads"] = odds.spend_extra(saved, odds.fetch_spreads, date_s)
        except Exception as exc:  # noqa: BLE001
            log["spreads_error"] = str(exc)
            saved["spreads"] = {}

    if "alts" not in saved:
        budget = min(MAX_ALT_CREDITS, odds.leftover_credits(saved))
        wanted = {}  # event_id -> [stats]
        overs = sorted((c for c in candidates(con, date_s, saved) if c["side"] == "over"),
                       key=lambda c: -c["prob"])
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
    return (f"Model projects {leg['proj']} {stat} and gives him a {round(leg['prob'] * 100)}% "
            f"chance to {word} {line_desc}. Averaging {leg['avg_minutes']} minutes over his last "
            f"10 games ({leg['minutes_confidence']} minutes confidence); {spread}.")


def _public_leg(leg):
    keep = ("player", "player_id", "team", "opp", "home", "time_et", "stat", "side", "line",
            "line_type", "book_line", "proj", "prob", "odds", "book", "blowout_risk",
            "minutes_confidence")
    return {**{k: leg[k] for k in keep}, "reasoning": reasoning(leg)}


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


def update(con, date_s, allow_spend=False):
    """Settle any finished parlay, then build (or rebuild) today's. Writes
    docs/data/parlay.json only when something changed. -> summary dict."""
    path = config.SITE_DATA_DIR / "parlay.json"
    data = _load(path)
    before = json.dumps({k: v for k, v in data.items() if k != "generated_at"}, sort_keys=True)
    log = {}

    mock = "MOCKUP" in (data.get("note") or "")
    today_json = data.get("today")

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
    if has_slate and saved.get("lines"):
        if mock:  # first real game day: drop the hand-made preview
            data = {"history": []}
        if allow_spend:
            log["extras"] = buy_extras(con, date_s, saved)
        cands = candidates(con, date_s, saved)
        legs, price = select_legs(cands + alt_options(cands, saved))
        log["candidates"] = len(cands)
        if legs:
            data["today"] = {"date": date_s, "combined_odds": price, "status": "pending",
                             "legs": [_public_leg(l) for l in legs]}
            data.pop("no_parlay_reason", None)
            log["parlay"] = {"legs": len(legs), "odds": price,
                             "alt_legs": sum(1 for l in legs if l["line_type"] == "alt")}
        else:
            data["today"] = None
            data["no_parlay_reason"] = (
                "No parlay today: not enough legs cleared the conviction bar "
                f"({len(cands)} qualifying options) to reach +{TARGET_LO} without forcing it.")
            log["parlay"] = None
        data["note"] = ("Picked automatically each morning from the Today board's projections "
                        "and FanDuel/DraftKings lines: legs the model gives a high chance to hit, "
                        "with steady minutes and no blowout risk. Settled the next morning from "
                        "the real box scores.")

    after = json.dumps({k: v for k, v in data.items() if k != "generated_at"}, sort_keys=True)
    if after != before:
        data["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        log["written"] = True
    return log
