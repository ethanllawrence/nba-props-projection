#!/usr/bin/env python3
"""RETIRED (2026-09-22). This used to hand-load Jokic's game log because
nba_api couldn't reach stats.nba.com. The daily pipeline now pulls every
player's real game log from ESPN (see nproj/ingest/espn.py), including
Jokic's, so this script is no longer needed. It also used nba_api's player
ID (203999), which would create a duplicate Jokic next to ESPN's (3112335).

Use instead:  python -m nproj daily
"""
import sys

if __name__ == "__main__":
    sys.exit(__doc__)
