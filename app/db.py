"""
db.py — SQLite persistence layer for Mediastarr v4
All search history is stored here instead of JSON.

Schema includes: service, item_type, item_id, title, release_year,
searched_at, result, search_count, last_changed_at
"""
import sqlite3
import threading
from typing import Optional
from datetime import datetime, timedelta
from pathlib import Path

_lock = threading.RLock()
_conn = None
_db_path = None


def _require_init():
    if _conn is None:
        raise RuntimeError("Database is not initialized. Call init(db_path) first.")


def _get_conn():
    _require_init()
    return _conn

def init(db_path: Path):
    global _conn, _db_path
    with _lock:
        if _conn is not None:
            _conn.close()
        _db_path = db_path
        _conn = _connect()
        _migrate()


def _connect():
    conn = sqlite3.connect(str(_db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # safe concurrent reads
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _migrate():
    """Create schema and run non-destructive migrations on existing DBs."""
    _require_init()
    with _lock:
        # Step 1: base table (release_year NOT included here so ALTER works on old DBs)
        _conn.execute("""
            CREATE TABLE IF NOT EXISTS search_history (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                service         TEXT    NOT NULL,
                item_type       TEXT    NOT NULL,
                item_id         INTEGER NOT NULL,
                title           TEXT    NOT NULL DEFAULT '',
                searched_at     TEXT    NOT NULL,
                result          TEXT    NOT NULL DEFAULT 'triggered',
                search_count    INTEGER NOT NULL DEFAULT 1,
                last_changed_at TEXT,
                UNIQUE(service, item_type, item_id)
            )
        """)
        _conn.commit()

        # Step 2: add columns introduced in v4 (idempotent ALTER TABLE)
        existing_cols = {row[1] for row in
                         _conn.execute("PRAGMA table_info(search_history)")}
        if "release_year" not in existing_cols:
            _conn.execute(
                "ALTER TABLE search_history ADD COLUMN release_year INTEGER")
            _conn.commit()

        # Step 3: indexes — individual execute() calls (not executescript) to avoid
        # the implicit COMMIT that executescript() performs, which can confuse
        # schema caches after an ALTER TABLE in the same connection.
        for stmt in [
            "CREATE INDEX IF NOT EXISTS idx_service     ON search_history(service)",
            "CREATE INDEX IF NOT EXISTS idx_searched_at ON search_history(searched_at)",
            "CREATE INDEX IF NOT EXISTS idx_item        ON search_history(service, item_type, item_id)",
            "CREATE INDEX IF NOT EXISTS idx_year        ON search_history(release_year)",
        ]:
            _conn.execute(stmt)
        _conn.commit()


# ─── Write ────────────────────────────────────────────────────────────────────

def upsert_search(service: str, item_type: str, item_id: int,
                  title: str, result: str = "triggered",
                  last_changed_at: str = None,
                  release_year: int = None):
    """Insert or update a search record. Increments search_count on conflict."""
    _require_init()
    now = datetime.utcnow().isoformat()
    with _lock:
        _conn.execute("""
            INSERT INTO search_history
                (service, item_type, item_id, title, release_year,
                 searched_at, result, search_count, last_changed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(service, item_type, item_id) DO UPDATE SET
                searched_at     = excluded.searched_at,
                title           = excluded.title,
                release_year    = COALESCE(excluded.release_year, release_year),
                result          = excluded.result,
                search_count    = search_count + 1,
                last_changed_at = COALESCE(excluded.last_changed_at, last_changed_at)
        """, (service, item_type, item_id, title, release_year,
              now, result, last_changed_at))
        _conn.commit()


# ─── Read ─────────────────────────────────────────────────────────────────────

def is_on_cooldown(service: str, item_type: str, item_id: int,
                   cooldown_days: int) -> bool:
    """True if this item was searched within the cooldown window."""
    _require_init()
    cutoff = (datetime.utcnow() - timedelta(days=cooldown_days)).isoformat()
    with _lock:
        row = _conn.execute("""
            SELECT searched_at FROM search_history
            WHERE service=? AND item_type=? AND item_id=? AND searched_at > ?
        """, (service, item_type, item_id, cutoff)).fetchone()
    return row is not None


def get_history(limit: int = 300, service: str = "",
                only_cooldown: bool = False,
                cooldown_days: int = 7) -> list:
    """Return recent history rows as plain dicts, newest first."""
    _require_init()
    cutoff = (datetime.utcnow() - timedelta(days=cooldown_days)).isoformat()
    wheres, params = [], []

    if service:
        wheres.append("service = ?"); params.append(service)
    if only_cooldown:
        wheres.append("searched_at > ?"); params.append(cutoff)

    where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""
    params.append(limit)

    with _lock:
        rows = _conn.execute(f"""
            SELECT *,
                   (searched_at > ?) AS on_cooldown
            FROM search_history
            {where_sql}
            ORDER BY searched_at DESC
            LIMIT ?
        """, [cutoff, *params]).fetchall()
    return [dict(r) for r in rows]


def count_today() -> int:
    """Number of real searches triggered today (UTC date)."""
    _require_init()
    today = datetime.utcnow().strftime("%Y-%m-%d")
    with _lock:
        row = _conn.execute("""
            SELECT COUNT(*) AS n FROM search_history
            WHERE searched_at LIKE ? AND result IN ('triggered', 'dry_run', 'downloaded')
        """, (today + "%",)).fetchone()
    return row["n"] if row else 0


def total_count() -> int:
    _require_init()
    with _lock:
        row = _conn.execute(
            "SELECT COUNT(*) AS n FROM search_history").fetchone()
    return row["n"] if row else 0


def stats_by_service() -> dict:
    """Per-service summary totals."""
    _require_init()
    with _lock:
        rows = _conn.execute("""
            SELECT service,
                   COUNT(*)             AS total,
                   SUM(search_count)    AS total_attempts,
                   MAX(searched_at)     AS last_search
            FROM search_history
            GROUP BY service
        """).fetchall()
    return {r["service"]: dict(r) for r in rows}


def year_stats() -> list:
    """Count of searched items grouped by release_year (for charts)."""
    _require_init()
    with _lock:
        rows = _conn.execute("""
            SELECT release_year, COUNT(*) AS count
            FROM search_history
            WHERE release_year IS NOT NULL
            GROUP BY release_year
            ORDER BY release_year DESC
        """).fetchall()
    return [dict(r) for r in rows]


# ─── Purge / Clear ────────────────────────────────────────────────────────────

def purge_expired(cooldown_days: int) -> int:
    """Remove rows older than cooldown so they can be re-searched next time."""
    _require_init()
    cutoff = (datetime.utcnow() - timedelta(days=cooldown_days)).isoformat()
    with _lock:
        cur = _conn.execute(
            "DELETE FROM search_history WHERE searched_at < ?", (cutoff,))
        _conn.commit()
    return cur.rowcount


def clear_service(service: str) -> int:
    _require_init()
    with _lock:
        cur = _conn.execute(
            "DELETE FROM search_history WHERE service=?", (service,))
        _conn.commit()
    return cur.rowcount


def clear_all() -> int:
    _require_init()
    with _lock:
        cur = _conn.execute("DELETE FROM search_history")
        _conn.commit()
    return cur.rowcount
