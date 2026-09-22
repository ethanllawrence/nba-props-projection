"""SQLite schema + connection helper.

STATUS: skeleton schema covering the core tables a points model needs.
Mirrors the K Board's kproj/db.py shape (session() contextmanager, schema
created on first connect). Extend as ingest/model work fills in real needs
(e.g. a separate table for minutes/role signals once that's designed).
"""
import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    game_id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    status TEXT,
    tipoff_utc TEXT
);

CREATE TABLE IF NOT EXISTS players (
    player_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    team TEXT
);

-- One row per player per game actually played (backfill + daily ingest).
CREATE TABLE IF NOT EXISTS player_game_logs (
    game_id TEXT NOT NULL,
    player_id TEXT NOT NULL,
    date TEXT NOT NULL,
    team TEXT,
    opp TEXT,
    minutes REAL,
    points INTEGER,
    rebounds INTEGER,
    assists INTEGER,
    threes INTEGER,
    PRIMARY KEY (game_id, player_id)
);

-- Today/upcoming slate: who's projected to play and roughly how much.
CREATE TABLE IF NOT EXISTS probable_players (
    game_id TEXT NOT NULL,
    player_id TEXT NOT NULL,
    date TEXT NOT NULL,
    status TEXT,               -- e.g. probable/questionable/out
    PRIMARY KEY (game_id, player_id)
);

CREATE TABLE IF NOT EXISTS projections (
    game_id TEXT NOT NULL,
    player_id TEXT NOT NULL,
    date TEXT NOT NULL,
    stat TEXT NOT NULL,        -- 'points' at launch; more stats later
    point REAL,
    p10 REAL, p25 REAL, p50 REAL, p75 REAL, p90 REAL,
    generated_at TEXT,
    PRIMARY KEY (game_id, player_id, stat)
);

CREATE TABLE IF NOT EXISTS prop_lines (
    game_id TEXT NOT NULL,
    player_id TEXT NOT NULL,
    stat TEXT NOT NULL,
    book TEXT NOT NULL,
    side TEXT NOT NULL,        -- over/under
    line REAL NOT NULL,
    odds INTEGER NOT NULL,
    fetched_at TEXT,
    PRIMARY KEY (game_id, player_id, stat, book, side)
);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


@contextmanager
def session():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def get_kv(con, key):
    row = con.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_kv(con, key, value):
    con.execute(
        "INSERT INTO kv (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
