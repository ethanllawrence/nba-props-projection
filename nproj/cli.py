"""Command-line entry points. Run as: python -m nproj <command> [options]

Commands
  init                  create the database schema
  daily [--date D]      full game-day cycle: pull tonight's slate and game
                        logs from ESPN, project, export site JSON, settle
                        yesterday's Parlay of the Day and pick today's,
                        grade last night's board (results.json)
  backfill --season S   every rostered player's game log for one ESPN
                        season year (2026 = 2025-26); not needed daily
  export [--date D]     regenerate site JSON only, from what's in the db
  odds-status           check the Odds API key and credits left (free call)
  history --season S    historical box scores, spreads and prop lines for one
    [--out DIR]         season (for model training/backtests), gzipped CSV
    [--limit N]         in DIR/S/ (default history/); --limit for a quick test
  status                quick database status

Odds: `daily` pulls prop lines only when NPROJ_ODDS_MODE=props and
ODDS_API_KEY is set (the workflow does this for the morning run only).

--date defaults to the board date (see util.board_date). Passing a past
date is the way to test the pipeline in the offseason: projections only use
games played before that date.
"""
import argparse
import sys

from . import config, db


def _date(args):
    from . import util
    return args.date or util.iso(util.board_date())


def cmd_init(_args) -> None:
    with db.session() as con:
        n = con.execute("SELECT COUNT(*) c FROM sqlite_master WHERE type='table'").fetchone()["c"]
    print(f"[init] schema ready at {config.DB_PATH} ({n} tables)")


def cmd_daily(args) -> None:
    from . import pipeline
    from .export.site_export import export_all
    from .model import predict

    date_s = _date(args)
    with db.session() as con:
        slate = pipeline.refresh_slate(con, date_s)
        print(f"[daily] {date_s}: {slate['games']} games, {slate['players']} rostered players, "
              f"{slate['logs']} game-log rows")
        pipeline.refresh_jokic(con, date_s)
        _maybe_odds(date_s, slate["games"])
        trained = predict.train(con, before_date=date_s)
        projected = predict.project_date(con, date_s)
        result = export_all(con, date_s)
        try:
            from .model import parlay
            spend = config.ODDS_MODE == "props" and bool(config.ODDS_API_KEY)
            print(f"[parlay] {parlay.update(con, date_s, allow_spend=spend)}")
        except Exception as exc:  # noqa: BLE001 - a parlay problem must never block the board
            print(f"[parlay] FAILED: {exc}")
        try:
            from . import results
            print(f"[results] {results.update(con, date_s)}")
        except Exception as exc:  # noqa: BLE001
            print(f"[results] FAILED: {exc}")
    from .export.status import write_status
    print(f"[status] {write_status(date_s, slate['games'])}")
    print(f"[daily] trained {trained} player-stats, wrote {projected} projections, exported {result}")
    if slate["games"] == 0:
        print("[daily] no games on this date, so today.json was left as it was")


def _maybe_odds(date_s, n_games):
    from .ingest import odds

    if config.ODDS_MODE != "props":
        print("[odds] skipped (odds mode is off for this run; reusing any saved lines)")
        return
    if not config.ODDS_API_KEY:
        print("[odds] skipped (no ODDS_API_KEY)")
        return
    if n_games == 0:
        print("[odds] skipped (no games, no credits spent)")
        return
    try:
        print(f"[odds] {odds.refresh_lines(date_s)}")
    except Exception as exc:  # noqa: BLE001 - odds trouble must never block the board
        print(f"[odds] FAILED, board will show no lines: {exc}")


def cmd_odds_status(_args) -> None:
    from datetime import date

    from .ingest import odds

    if not config.ODDS_API_KEY:
        print("[odds-status] no ODDS_API_KEY set")
        return
    remaining, used = odds.check_quota()
    today = date.today()
    print(f"[odds-status] key OK: {remaining} credits remaining, {used} used this period; "
          f"{odds.days_until_reset(today)} days to reset; NBA allowance today = "
          f"{odds.daily_allowance(remaining, today)} credits "
          f"(floor {config.ODDS_BUDGET_FLOOR}, share {config.ODDS_NBA_SHARE})")


def cmd_history(args) -> None:
    from .ingest.espn_history import backfill_season
    backfill_season(args.season, args.out, limit=args.limit)


def cmd_backfill(args) -> None:
    from . import pipeline

    with db.session() as con:
        result = pipeline.backfill(con, args.season)
    print(f"[backfill] season {args.season}: {result}")


def cmd_export(args) -> None:
    from .export.site_export import export_all

    date_s = _date(args)
    with db.session() as con:
        result = export_all(con, date_s)
    print(f"[export] site JSON refreshed for {date_s}: {result}")


def cmd_status(_args) -> None:
    with db.session() as con:
        for label, q in [
            ("games", "SELECT COUNT(*) c, MIN(date) lo, MAX(date) hi FROM games"),
            ("game logs", "SELECT COUNT(*) c, MIN(date) lo, MAX(date) hi FROM player_game_logs"),
            ("projections", "SELECT COUNT(*) c, MIN(generated_at) lo, MAX(generated_at) hi FROM projections"),
        ]:
            r = con.execute(q).fetchone()
            print(f"{label:14} {r['c']:>8}   {r['lo'] or '-'} → {r['hi'] or '-'}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="nproj", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    d = sub.add_parser("daily")
    d.add_argument("--date", help="YYYY-MM-DD (default: board date)")
    b = sub.add_parser("backfill")
    b.add_argument("--season", type=int, required=True, help="ESPN season year, e.g. 2026 for 2025-26")
    e = sub.add_parser("export")
    e.add_argument("--date", help="YYYY-MM-DD (default: board date)")
    sub.add_parser("odds-status")
    h = sub.add_parser("history")
    h.add_argument("--season", type=int, required=True)
    h.add_argument("--out", default="history")
    h.add_argument("--limit", type=int, default=None)
    sub.add_parser("status")
    args = p.parse_args(argv)
    {"init": cmd_init, "daily": cmd_daily, "backfill": cmd_backfill,
     "export": cmd_export, "odds-status": cmd_odds_status, "history": cmd_history, "status": cmd_status}[args.cmd](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
