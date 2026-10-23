"""SQLite schema and small helpers. WAL mode, foreign keys ON, idempotent init."""
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from backend.config import DATA_DIR, DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS notebooks(id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT);
CREATE TABLE IF NOT EXISTS sources(
  id TEXT PRIMARY KEY, notebook_id TEXT NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
  type TEXT, title TEXT, origin TEXT, content_hash TEXT, language TEXT, full_text TEXT,
  page_offsets_json TEXT, meta_json TEXT, status TEXT, error TEXT, warning TEXT, created_at TEXT,
  UNIQUE(notebook_id, content_hash));
CREATE TABLE IF NOT EXISTS chunks(
  id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  chunk_config TEXT, idx INTEGER, text TEXT, start_offset INTEGER, end_offset INTEGER,
  page INTEGER, start_seconds REAL, language TEXT);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_id, chunk_config);
CREATE TABLE IF NOT EXISTS chat_messages(
  id TEXT PRIMARY KEY, notebook_id TEXT NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
  role TEXT, text TEXT, citations_json TEXT, retrieval_json TEXT,
  tokens_in INTEGER, tokens_out INTEGER, latency_ms INTEGER, created_at TEXT);
CREATE TABLE IF NOT EXISTS test_questions(
  id TEXT PRIMARY KEY, notebook_id TEXT NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
  question TEXT, language TEXT, expected_answer TEXT, gold_source_id TEXT,
  gold_start INTEGER, gold_end INTEGER, verified INTEGER DEFAULT 0, origin TEXT, pair_id TEXT);
CREATE TABLE IF NOT EXISTS experiments(
  id TEXT PRIMARY KEY, notebook_id TEXT NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
  name TEXT, config_json TEXT, status TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS experiment_results(
  id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
  question_id TEXT NOT NULL REFERENCES test_questions(id) ON DELETE CASCADE,
  retrieved_json TEXT, answer TEXT, hit INTEGER, rr REAL, correct INTEGER, faithful INTEGER,
  judge_notes TEXT, latency_ms INTEGER, tokens INTEGER, language TEXT, extra_json TEXT);
CREATE TABLE IF NOT EXISTS feedback(
  id TEXT PRIMARY KEY, message_id TEXT NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
  rating TEXT, comment TEXT, created_at TEXT);
"""


def new_id() -> str:
    """Short unique id."""
    return uuid.uuid4().hex[:12]


def now() -> str:
    """UTC time as text."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Open a connection (one per call, so threads never share it). Commits on success."""
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create folders and tables if missing."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(experiment_results)")}
        if "extra_json" not in cols:  # small migration for databases made by Part 1
            c.execute("ALTER TABLE experiment_results ADD COLUMN extra_json TEXT")


def rows(sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    """Run a SELECT, return list of dicts."""
    with connect() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def one(sql: str, args: tuple = ()) -> dict[str, Any] | None:
    """Run a SELECT, return first row or None."""
    r = rows(sql, args)
    return r[0] if r else None


def run(sql: str, args: tuple = ()) -> None:
    """Run INSERT/UPDATE/DELETE."""
    with connect() as c:
        c.execute(sql, args)
