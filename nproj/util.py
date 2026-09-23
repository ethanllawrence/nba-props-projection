"""Shared helpers: odds math, dates, names, HTTP.

Lifted directly from the K Board's kproj/util.py where the logic is stat-
agnostic (odds math, board-day clock, HTTP retry). Ingest-specific helpers
(name matching, innings-pitched parsing) are dropped or will be replaced
with NBA equivalents as ingest work starts.
"""
import time
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from zoneinfo import ZoneInfo

import requests

from . import config

BOARD_TZ = ZoneInfo(config.BOARD_ZONE)


# ---------- HTTP ----------
def http_get(url, params=None, retries=3, timeout=None, as_json=True):
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.get(
                url,
                params=params,
                timeout=timeout or config.REQUEST_TIMEOUT,
                headers={"User-Agent": config.NBA_STATS_USER_AGENT},
            )
            if r.status_code == 200:
                return r.json() if as_json else r
            last_err = f"HTTP {r.status_code}"
            if r.status_code in (400, 401, 403, 404, 422):
                break  # not retryable
        except requests.RequestException as e:  # noqa: PERF203
            last_err = str(e)
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed: {last_err}")


# ---------- Dates ----------
def board_date(now: datetime | None = None) -> date:
    """The slate the site should be showing right now.

    Same explicit-rollover pattern as the K Board's board_date() — see the
    TODO in config.py: BOARD_ROLLOVER_HOUR needs a deliberate NBA-specific
    value before this is trustworthy for real games (West Coast night games
    can run later than any MLB start).
    """
    n = (now or datetime.now(BOARD_TZ)).astimezone(BOARD_TZ)
    d = n.date()
    return d + timedelta(days=1) if n.hour >= config.BOARD_ROLLOVER_HOUR else d


def day_is_final(date_s: str, now: datetime | None = None) -> bool:
    """True once every game dated date_s (US Eastern) is surely over: 4 AM
    Eastern the next morning. Used before grading results or a parlay, so the
    8 PM Arizona run (which flips the board to tomorrow) never grades a night
    whose late games are still being played."""
    et = ZoneInfo(config.ET_ZONE)
    n = (now or datetime.now(timezone.utc)).astimezone(et)
    cutoff = datetime.combine(date.fromisoformat(date_s) + timedelta(days=1), dtime(4, 0), et)
    return n >= cutoff


def board_prev_date(now: datetime | None = None) -> date:
    return board_date(now) - timedelta(days=1)


def iso(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


# ---------- Odds math (identical to kproj/util.py — stat-agnostic) ----------
def american_to_decimal(odds: int) -> float:
    o = int(odds)
    return 1 + (o / 100.0) if o > 0 else 1 + (100.0 / abs(o))


def american_to_prob(odds: int) -> float:
    o = int(odds)
    return 100.0 / (o + 100.0) if o > 0 else abs(o) / (abs(o) + 100.0)


def devig_two_way(p_a_raw: float, p_b_raw: float) -> tuple[float, float]:
    """Multiplicative de-vig: normalize the two raw implied probabilities."""
    s = p_a_raw + p_b_raw
    if s <= 0:
        return 0.5, 0.5
    return p_a_raw / s, p_b_raw / s
