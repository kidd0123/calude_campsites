"""Shared SQLite helpers and config loading."""
import os
import sqlite3
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "camps.sqlite"
CONFIG_PATH = ROOT / "config.yaml"


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS facilities (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  name TEXT NOT NULL,
  aliases TEXT,
  lat REAL, lon REAL,
  state TEXT,
  facility_type TEXT,
  car_accessible INTEGER DEFAULT 1,
  amenities_json TEXT,
  url TEXT,
  description TEXT,
  miles_from_home REAL,
  drive_hours_est REAL,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_fac_state ON facilities(state);
CREATE INDEX IF NOT EXISTS idx_fac_drive ON facilities(drive_hours_est);

CREATE TABLE IF NOT EXISTS reddit_mentions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  facility_id TEXT,
  subreddit TEXT,
  post_id TEXT,
  comment_id TEXT,
  score INTEGER,
  permalink TEXT,
  snippet TEXT,
  themes TEXT,
  created_utc INTEGER,
  UNIQUE(facility_id, comment_id)
);
CREATE INDEX IF NOT EXISTS idx_rm_fac ON reddit_mentions(facility_id);

CREATE TABLE IF NOT EXISTS curated_mentions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  facility_id TEXT,
  source TEXT,
  url TEXT,
  rank INTEGER,
  snippet TEXT,
  UNIQUE(facility_id, source)
);

CREATE TABLE IF NOT EXISTS quality_score (
  facility_id TEXT PRIMARY KEY,
  reddit_mention_count INTEGER DEFAULT 0,
  reddit_score_sum INTEGER DEFAULT 0,
  curated_count INTEGER DEFAULT 0,
  uniqueness REAL DEFAULT 0,
  themes TEXT,
  composite REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS availability_cache (
  facility_id TEXT,
  date TEXT,
  sites_available INTEGER,
  fetched_at TEXT,
  PRIMARY KEY (facility_id, date)
);
"""


def init_schema(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def haversine_miles(lat1, lon1, lat2, lon2):
    from math import radians, sin, cos, asin, sqrt
    if None in (lat1, lon1, lat2, lon2):
        return None
    R = 3958.8
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * R * asin(sqrt(a))
