"""Command-line entry points. Run as: python -m nproj <command> [options]

Commands
  init      create the database schema
  daily     full game-day cycle: ingest, project, score, export (STUB)
  export    regenerate site JSON only (STUB)
  status    quick database status

STATUS: skeleton mirroring the K Board's kproj/cli.py shape. `daily` and
`export` raise NotImplementedError until the ingest/model/export modules
they call are built out — see the planning doc's Build phases.
"""
import argparse
import sys

from . import config, db


def cmd_init(_args) -> None:
    with db.session() as con:
        n = con.execute("SELECT COUNT(*) c FROM sqlite_master WHERE type='table'").fetchone()["c"]
    print(f"[init] schema ready at {config.DB_PATH} ({n} tables)")


def cmd_daily(_args) -> None:
    from . import util
    from .export.site_export import export_all
    from .ingest.nba_stats import fetch_box_scores, fetch_schedule  # noqa: F401
    from .ingest.odds import fetch_points_props  # noqa: F401
    from .model import predict

    date_s = util.iso(util.board_date())
    with db.session() as con:
        # TODO once nba_stats/odds ingest can actually reach the network
        # (blocked from this sandbox today — see nproj/ingest/nba_stats.py):
        # fetch_schedule -> fetch_box_scores for finished games ->
        # fetch_points_props (budget-gated), THEN the two calls below.
        predict.train(con)
        predict.project_date(con, date_s)
        result = export_all(con, date_s)
    print(f"[daily] projected/exported for {date_s}: {result}")


def cmd_export(_args) -> None:
    from . import util
    from .export.site_export import export_all

    date_s = util.iso(util.board_date())
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
    sub.add_parser("daily")
    sub.add_parser("export")
    sub.add_parser("status")
    args = p.parse_args(argv)
    {"init": cmd_init, "daily": cmd_daily, "export": cmd_export, "status": cmd_status}[args.cmd](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
