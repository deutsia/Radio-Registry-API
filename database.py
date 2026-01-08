"""
SQLite database operations for the radio directory
"""
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional
from contextlib import contextmanager

from config import (
    DATABASE_PATH,
    RECHECK_INTERVALS_MINUTES,
    DEAD_THRESHOLD_HOURS,
    HEALTH_CHECK_INTERVAL_HOURS
)


def get_db_path() -> Path:
    """Get database path"""
    return DATABASE_PATH


@contextmanager
def get_connection():
    """Context manager for database connections"""
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate_health_fields(conn):
    """Add new health tracking columns to existing databases"""
    cursor = conn.execute("PRAGMA table_info(stations)")
    existing_columns = {row[1] for row in cursor.fetchall()}

    migrations = [
        ("consecutive_failures", "INTEGER DEFAULT 0"),
        ("last_online_time", "INTEGER"),
        ("next_check_time", "INTEGER"),
        ("health_status", "TEXT DEFAULT 'unknown'"),
    ]

    for col_name, col_type in migrations:
        if col_name not in existing_columns:
            conn.execute(f"ALTER TABLE stations ADD COLUMN {col_name} {col_type}")

    # Initialize health_status for existing stations based on current data
    conn.execute("""
        UPDATE stations SET health_status = 'online'
        WHERE last_check_ok = 1 AND health_status = 'unknown'
    """)
    conn.execute("""
        UPDATE stations SET health_status = 'offline'
        WHERE last_check_ok = 0 AND health_status = 'unknown' AND last_check_time IS NOT NULL
    """)


def init_db():
    """Initialize the database schema"""
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS stations (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                stream_url TEXT NOT NULL UNIQUE,
                homepage TEXT,
                favicon_url TEXT,
                genre TEXT DEFAULT 'Other',
                codec TEXT,
                bitrate INTEGER,
                language TEXT DEFAULT 'Unknown',
                network TEXT NOT NULL,
                country TEXT,
                country_code TEXT,

                -- Health tracking
                last_check_time INTEGER,
                last_check_ok INTEGER DEFAULT 0,
                check_count INTEGER DEFAULT 0,
                check_ok_count INTEGER DEFAULT 0,
                consecutive_failures INTEGER DEFAULT 0,
                last_online_time INTEGER,
                next_check_time INTEGER,
                health_status TEXT DEFAULT 'unknown',
                
                -- Moderation
                status TEXT DEFAULT 'pending',
                submitted_at INTEGER,
                approved_at INTEGER,
                rejection_reason TEXT,
                
                created_at INTEGER,
                updated_at INTEGER
            );
            
            CREATE INDEX IF NOT EXISTS idx_network ON stations(network);
            CREATE INDEX IF NOT EXISTS idx_status ON stations(status);
            CREATE INDEX IF NOT EXISTS idx_genre ON stations(genre);
            CREATE INDEX IF NOT EXISTS idx_last_check ON stations(last_check_ok, status);

            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            -- Cover approval queue for human-in-the-loop review
            CREATE TABLE IF NOT EXISTS cover_approvals (
                id TEXT PRIMARY KEY,
                station_id TEXT NOT NULL,
                cover_url TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                submitted_at INTEGER NOT NULL,
                reviewed_at INTEGER,
                FOREIGN KEY (station_id) REFERENCES stations(id)
            );

            CREATE INDEX IF NOT EXISTS idx_cover_approvals_status ON cover_approvals(status);
            CREATE INDEX IF NOT EXISTS idx_cover_approvals_station ON cover_approvals(station_id);
        """)
        # Run migrations for existing databases
        _migrate_health_fields(conn)

        # Create indices for new columns (after migration ensures columns exist)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_next_check ON stations(next_check_time, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_health_status ON stations(health_status, status)")


def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a sqlite Row to a dictionary"""
    return dict(row)


def _timestamp_to_iso(ts: Optional[int]) -> Optional[str]:
    """Convert Unix timestamp to ISO format string"""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts).isoformat() + "Z"


def _now() -> int:
    """Current Unix timestamp"""
    return int(datetime.now().timestamp())


def _escape_like_pattern(search: str) -> str:
    """Escape special characters for SQL LIKE pattern matching.

    Characters % and _ have special meaning in LIKE:
    - % matches any sequence of characters
    - _ matches any single character

    We escape them with backslash so they are matched literally.
    """
    return search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _compute_health_status(d: dict) -> str:
    """Compute health status based on station data"""
    # If we have a stored health_status, use it
    stored = d.get("health_status")
    if stored and stored != "unknown":
        return stored

    # Fallback computation for legacy data
    if d.get("last_check_ok"):
        return "online"
    if d.get("last_online_time"):
        now = _now()
        hours_since_online = (now - d["last_online_time"]) / 3600
        if hours_since_online >= DEAD_THRESHOLD_HOURS:
            return "dead"
    return "offline"


def station_to_response(row: sqlite3.Row) -> dict:
    """Convert database row to API response format"""
    d = _row_to_dict(row)
    check_count = d.get("check_count") or 0
    check_ok_count = d.get("check_ok_count") or 0
    uptime_pct = round((check_ok_count / check_count * 100) if check_count > 0 else 0)
    return {
        "id": d["id"],
        "name": d["name"],
        "streamUrl": d["stream_url"],
        "homepage": d["homepage"],
        "faviconUrl": d.get("favicon_url"),
        "genre": d["genre"] or "",
        "codec": d["codec"],
        "bitrate": d["bitrate"],
        "language": d.get("language") or "",
        "network": d["network"],
        "lastCheckOk": bool(d["last_check_ok"]),
        "lastCheckTime": _timestamp_to_iso(d["last_check_time"]),
        "healthStatus": _compute_health_status(d),
        "consecutiveFailures": d.get("consecutive_failures") or 0,
        "checkCount": check_count,
        "checkOkCount": check_ok_count,
        "uptimePercent": uptime_pct,
    }


def station_to_detail_response(row: sqlite3.Row) -> dict:
    """Convert database row to detailed API response format"""
    base = station_to_response(row)
    d = _row_to_dict(row)
    base.update({
        "status": d["status"],
        "checkCount": d["check_count"] or 0,
        "checkOkCount": d["check_ok_count"] or 0,
        "consecutiveFailures": d.get("consecutive_failures") or 0,
        "lastOnlineTime": _timestamp_to_iso(d.get("last_online_time")),
        "submittedAt": _timestamp_to_iso(d["submitted_at"]),
        "approvedAt": _timestamp_to_iso(d["approved_at"]),
        "createdAt": _timestamp_to_iso(d["created_at"]),
        "updatedAt": _timestamp_to_iso(d["updated_at"]),
    })
    return base


# ============== Station CRUD ==============

def get_stations(
    network: Optional[str] = None,
    genre: Optional[str] = None,
    status: str = "approved",
    limit: int = 50,
    offset: int = 0,
    online_only: bool = False,
    search: Optional[str] = None,
    sort: str = "newest"
) -> list[dict]:
    """Get stations with optional filters and sorting.

    Args:
        sort: Sorting method - "newest" (default), "health", or "alphabetical"
    """
    query = "SELECT * FROM stations WHERE status = ?"
    params: list = [status]

    if online_only:
        query += " AND last_check_ok = 1"

    if network:
        query += " AND network = ?"
        params.append(network.lower())

    if genre:
        # Support multi-genre: match if the genre field contains this genre
        # This works for both single genre and comma-separated genres
        query += " AND (',' || genre || ',' LIKE ? ESCAPE '\\' OR genre = ?)"
        genre_pattern = f"%,{_escape_like_pattern(genre)},%"
        params.extend([genre_pattern, genre])

    if search:
        query += " AND (name LIKE ? ESCAPE '\\' OR genre LIKE ? ESCAPE '\\')"
        search_pattern = f"%{_escape_like_pattern(search)}%"
        params.extend([search_pattern, search_pattern])

    # Apply sorting based on sort parameter
    if sort == "health":
        # Best health first (online first, then by uptime percentage)
        query += """ ORDER BY
            CASE health_status
                WHEN 'online' THEN 0
                WHEN 'offline' THEN 1
                WHEN 'dead' THEN 2
                ELSE 3
            END ASC,
            CASE WHEN check_count > 0 THEN (check_ok_count * 100.0 / check_count) ELSE 0 END DESC,
            name ASC"""
    elif sort == "alphabetical":
        # Alphabetical by name
        query += " ORDER BY name COLLATE NOCASE ASC"
    else:
        # Default: newest first
        query += " ORDER BY created_at DESC, name ASC"

    query += " LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
        return [station_to_response(row) for row in rows]


def get_station_by_id(station_id: str) -> Optional[dict]:
    """Get a single station by ID"""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM stations WHERE id = ?",
            (station_id,)
        ).fetchone()
        if row:
            return station_to_detail_response(row)
        return None


def get_station_by_url(stream_url: str) -> Optional[dict]:
    """Get a station by stream URL"""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM stations WHERE stream_url = ?",
            (stream_url,)
        ).fetchone()
        if row:
            return station_to_detail_response(row)
        return None


def create_station(
    name: str,
    stream_url: str,
    network: str,
    homepage: Optional[str] = None,
    genre: str = "",
    codec: Optional[str] = None,
    bitrate: Optional[int] = None,
    language: str = "",
    status: str = "pending"
) -> str:
    """Create a new station, returns the ID"""
    station_id = str(uuid.uuid4())
    now = _now()

    with get_connection() as conn:
        conn.execute("""
            INSERT INTO stations (
                id, name, stream_url, network, homepage, genre,
                codec, bitrate, language,
                status, submitted_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            station_id, name, stream_url, network.lower(), homepage, genre,
            codec, bitrate, language,
            status, now, now, now
        ))

    return station_id


def update_station(station_id: str, **kwargs) -> bool:
    """Update station fields"""
    if not kwargs:
        return False
    
    # Map API field names to database columns
    field_map = {
        "name": "name",
        "stream_url": "stream_url",
        "homepage": "homepage",
        "favicon_url": "favicon_url",
        "genre": "genre",
        "codec": "codec",
        "bitrate": "bitrate",
        "language": "language",
        "status": "status",
    }
    
    updates = []
    params = []
    for key, value in kwargs.items():
        if key in field_map:
            updates.append(f"{field_map[key]} = ?")
            params.append(value)
    
    if not updates:
        return False
    
    updates.append("updated_at = ?")
    params.append(_now())
    params.append(station_id)
    
    query = f"UPDATE stations SET {', '.join(updates)} WHERE id = ?"
    
    with get_connection() as conn:
        cursor = conn.execute(query, params)
        return cursor.rowcount > 0


def delete_station(station_id: str) -> bool:
    """Delete a station"""
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM stations WHERE id = ?", (station_id,))
        return cursor.rowcount > 0


# ============== Health Check Operations ==============

def get_stations_due_for_check(status: str = "approved") -> list[dict]:
    """Get stations that are due for a health check based on next_check_time"""
    now = _now()
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, stream_url, network, consecutive_failures, health_status
               FROM stations
               WHERE status = ? AND (next_check_time IS NULL OR next_check_time <= ?)
               ORDER BY next_check_time ASC NULLS FIRST""",
            (status, now)
        ).fetchall()
        return [_row_to_dict(row) for row in rows]


def get_stations_for_health_check(status: str = "approved") -> list[dict]:
    """Get all stations that need health checking (legacy function for compatibility)"""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, stream_url, network FROM stations WHERE status = ?",
            (status,)
        ).fetchall()
        return [_row_to_dict(row) for row in rows]


def _calculate_next_check_time(is_online: bool, consecutive_failures: int) -> int:
    """Calculate when the next check should happen based on current status"""
    now = _now()

    if is_online:
        # Online stations: check again in 4 hours
        return now + (HEALTH_CHECK_INTERVAL_HOURS * 3600)

    # Failed check: use escalating recheck intervals
    # consecutive_failures: 1 -> 5 min, 2 -> 15 min, 3 -> 60 min, 4+ -> 4 hours
    if consecutive_failures <= len(RECHECK_INTERVALS_MINUTES):
        minutes = RECHECK_INTERVALS_MINUTES[consecutive_failures - 1]
        return now + (minutes * 60)
    else:
        # After exhausting recheck intervals, fall back to regular interval
        return now + (HEALTH_CHECK_INTERVAL_HOURS * 3600)


def _calculate_health_status(is_online: bool, last_online_time: Optional[int], consecutive_failures: int) -> str:
    """Determine health status: online, offline, or dead"""
    if is_online:
        return "online"

    if last_online_time is None:
        # Never been online - could be new station or always failed
        if consecutive_failures >= len(RECHECK_INTERVALS_MINUTES) + 1:
            return "dead"
        return "offline"

    # Check if it's been too long since last online
    now = _now()
    hours_since_online = (now - last_online_time) / 3600
    if hours_since_online >= DEAD_THRESHOLD_HOURS:
        return "dead"

    return "offline"


def update_health_status(station_id: str, is_online: bool):
    """Update station health status after a check with smart recheck scheduling"""
    now = _now()
    with get_connection() as conn:
        # Get current state
        row = conn.execute(
            """SELECT check_count, check_ok_count, consecutive_failures,
                      last_online_time, health_status
               FROM stations WHERE id = ?""",
            (station_id,)
        ).fetchone()

        if not row:
            return

        check_count = (row["check_count"] or 0) + 1
        check_ok_count = (row["check_ok_count"] or 0) + (1 if is_online else 0)

        if is_online:
            # Success: reset consecutive failures, update last online time
            consecutive_failures = 0
            last_online_time = now
        else:
            # Failure: increment consecutive failures, keep last online time
            consecutive_failures = (row["consecutive_failures"] or 0) + 1
            last_online_time = row["last_online_time"]

        # Calculate next check time and health status
        next_check_time = _calculate_next_check_time(is_online, consecutive_failures)
        health_status = _calculate_health_status(is_online, last_online_time, consecutive_failures)

        conn.execute("""
            UPDATE stations SET
                last_check_time = ?,
                last_check_ok = ?,
                check_count = ?,
                check_ok_count = ?,
                consecutive_failures = ?,
                last_online_time = ?,
                next_check_time = ?,
                health_status = ?,
                updated_at = ?
            WHERE id = ?
        """, (
            now,
            1 if is_online else 0,
            check_count,
            check_ok_count,
            consecutive_failures,
            last_online_time,
            next_check_time,
            health_status,
            now,
            station_id
        ))


def set_last_health_check_time():
    """Record when the last full health check ran"""
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            ("last_health_check", str(_now()))
        )


def get_last_health_check_time() -> Optional[str]:
    """Get the time of last health check"""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = ?",
            ("last_health_check",)
        ).fetchone()
        if row:
            return _timestamp_to_iso(int(row["value"]))
        return None


# ============== Moderation Operations ==============

def get_pending_stations() -> list[dict]:
    """Get all pending stations"""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM stations WHERE status = 'pending' ORDER BY submitted_at ASC"
        ).fetchall()
        return [station_to_detail_response(row) for row in rows]


def approve_station(station_id: str) -> bool:
    """Approve a pending station"""
    now = _now()
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE stations SET status = 'approved', approved_at = ?, updated_at = ? WHERE id = ? AND status = 'pending'",
            (now, now, station_id)
        )
        return cursor.rowcount > 0


def reject_station(station_id: str, reason: Optional[str] = None) -> bool:
    """Reject a pending station"""
    now = _now()
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE stations SET status = 'rejected', rejection_reason = ?, updated_at = ? WHERE id = ? AND status = 'pending'",
            (reason, now, station_id)
        )
        return cursor.rowcount > 0


# ============== Stats Operations ==============

def get_stats() -> dict:
    """Get directory statistics"""
    with get_connection() as conn:
        total = conn.execute("SELECT COUNT(*) as c FROM stations WHERE status = 'approved'").fetchone()["c"]
        online = conn.execute("SELECT COUNT(*) as c FROM stations WHERE status = 'approved' AND health_status = 'online'").fetchone()["c"]
        offline = conn.execute("SELECT COUNT(*) as c FROM stations WHERE status = 'approved' AND health_status = 'offline'").fetchone()["c"]
        dead = conn.execute("SELECT COUNT(*) as c FROM stations WHERE status = 'approved' AND health_status = 'dead'").fetchone()["c"]
        tor = conn.execute("SELECT COUNT(*) as c FROM stations WHERE status = 'approved' AND network = 'tor'").fetchone()["c"]
        i2p = conn.execute("SELECT COUNT(*) as c FROM stations WHERE status = 'approved' AND network = 'i2p'").fetchone()["c"]
        tor_online = conn.execute("SELECT COUNT(*) as c FROM stations WHERE status = 'approved' AND network = 'tor' AND health_status = 'online'").fetchone()["c"]
        i2p_online = conn.execute("SELECT COUNT(*) as c FROM stations WHERE status = 'approved' AND network = 'i2p' AND health_status = 'online'").fetchone()["c"]
        pending = conn.execute("SELECT COUNT(*) as c FROM stations WHERE status = 'pending'").fetchone()["c"]

        return {
            "total_stations": total,
            "online_stations": online,
            "offline_stations": offline,
            "dead_stations": dead,
            "tor_stations": tor,
            "i2p_stations": i2p,
            "tor_online": tor_online,
            "i2p_online": i2p_online,
            "pending_submissions": pending,
            "last_health_check": get_last_health_check_time()
        }


def get_genres() -> list[str]:
    """Get list of genres with stations (supports multi-genre comma-separated values)"""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT genre FROM stations WHERE status = 'approved' AND genre IS NOT NULL AND genre != ''"
        ).fetchall()
        # Extract individual genres from comma-separated values
        genres_set = set()
        for row in rows:
            genre_value = row["genre"]
            if genre_value:
                # Split by comma and strip whitespace
                for g in genre_value.split(","):
                    g = g.strip()
                    if g:
                        genres_set.add(g)
        return sorted(genres_set)


def count_stations(
    network: Optional[str] = None,
    genre: Optional[str] = None,
    status: str = "approved",
    online_only: bool = False,
    search: Optional[str] = None
) -> int:
    """Count stations with optional filters."""
    query = "SELECT COUNT(*) as c FROM stations WHERE status = ?"
    params: list = [status]

    if online_only:
        query += " AND last_check_ok = 1"

    if network:
        query += " AND network = ?"
        params.append(network.lower())

    if genre:
        # Support multi-genre: match if the genre field contains this genre
        query += " AND (',' || genre || ',' LIKE ? ESCAPE '\\' OR genre = ?)"
        genre_pattern = f"%,{_escape_like_pattern(genre)},%"
        params.extend([genre_pattern, genre])

    if search:
        query += " AND (name LIKE ? ESCAPE '\\' OR genre LIKE ? ESCAPE '\\')"
        search_pattern = f"%{_escape_like_pattern(search)}%"
        params.extend([search_pattern, search_pattern])

    with get_connection() as conn:
        return conn.execute(query, params).fetchone()["c"]


# ============== Import/Export ==============

def import_stations_from_json(stations: list[dict], network: str, auto_approve: bool = True):
    """Import stations from JSON (for initial migration)"""
    status = "approved" if auto_approve else "pending"
    imported = 0
    
    for s in stations:
        # Check if already exists
        existing = get_station_by_url(s.get("streamUrl") or s.get("stream_url", ""))
        if existing:
            continue
        
        try:
            create_station(
                name=s.get("name", "Unknown"),
                stream_url=s.get("streamUrl") or s.get("stream_url"),
                network=network,
                homepage=s.get("homepage"),
                genre=s.get("genre", "Other"),
                codec=s.get("codec"),
                bitrate=s.get("bitrate"),
                language=s.get("language", "Unknown"),
                status=status
            )
            imported += 1
        except Exception as e:
            print(f"Failed to import {s.get('name')}: {e}")
    
    return imported


def export_stations_to_json(network: Optional[str] = None) -> list[dict]:
    """Export stations to JSON format"""
    return get_stations(network=network, online_only=False, limit=10000)


def get_stations_for_download(include_dead: bool = True, online_only: bool = False) -> list[dict]:
    """Get stations for user download with various filters"""
    query = "SELECT * FROM stations WHERE status = 'approved'"
    params: list = []

    if online_only:
        query += " AND health_status = 'online'"
    elif not include_dead:
        query += " AND health_status != 'dead'"

    query += " ORDER BY last_check_ok DESC, name ASC"

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
        return [station_to_response(row) for row in rows]


# ============== Cover Approval Operations ==============

def create_cover_approval(station_id: str, cover_url: str) -> str:
    """Create a new cover approval request, returns the ID"""
    approval_id = str(uuid.uuid4())
    now = _now()

    with get_connection() as conn:
        conn.execute("""
            INSERT INTO cover_approvals (id, station_id, cover_url, status, submitted_at)
            VALUES (?, ?, ?, 'pending', ?)
        """, (approval_id, station_id, cover_url, now))

    return approval_id


def get_pending_cover_approvals() -> list[dict]:
    """Get all pending cover approvals with station info"""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT ca.*, s.name as station_name, s.network as station_network
            FROM cover_approvals ca
            JOIN stations s ON ca.station_id = s.id
            WHERE ca.status = 'pending'
            ORDER BY ca.submitted_at ASC
        """).fetchall()
        return [_row_to_dict(row) for row in rows]


def get_cover_approval_by_id(approval_id: str) -> Optional[dict]:
    """Get a single cover approval by ID"""
    with get_connection() as conn:
        row = conn.execute("""
            SELECT ca.*, s.name as station_name, s.network as station_network
            FROM cover_approvals ca
            JOIN stations s ON ca.station_id = s.id
            WHERE ca.id = ?
        """, (approval_id,)).fetchone()
        if row:
            return _row_to_dict(row)
        return None


def get_pending_cover_for_station(station_id: str) -> Optional[dict]:
    """Get pending cover approval for a specific station"""
    with get_connection() as conn:
        row = conn.execute("""
            SELECT * FROM cover_approvals
            WHERE station_id = ? AND status = 'pending'
            ORDER BY submitted_at DESC LIMIT 1
        """, (station_id,)).fetchone()
        if row:
            return _row_to_dict(row)
        return None


def approve_cover(approval_id: str) -> bool:
    """Mark a cover approval as approved"""
    now = _now()
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE cover_approvals SET status = 'approved', reviewed_at = ? WHERE id = ? AND status = 'pending'",
            (now, approval_id)
        )
        return cursor.rowcount > 0


def reject_cover(approval_id: str) -> bool:
    """Mark a cover approval as rejected"""
    now = _now()
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE cover_approvals SET status = 'rejected', reviewed_at = ? WHERE id = ? AND status = 'pending'",
            (now, approval_id)
        )
        return cursor.rowcount > 0


def delete_cover_approval(approval_id: str) -> bool:
    """Delete a cover approval record"""
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM cover_approvals WHERE id = ?", (approval_id,))
        return cursor.rowcount > 0


def count_pending_covers() -> int:
    """Count pending cover approvals (only those with valid stations)"""
    with get_connection() as conn:
        return conn.execute("""
            SELECT COUNT(*) as c FROM cover_approvals ca
            JOIN stations s ON ca.station_id = s.id
            WHERE ca.status = 'pending'
        """).fetchone()["c"]


def cleanup_orphaned_cover_approvals() -> int:
    """Delete cover approvals for stations that no longer exist"""
    with get_connection() as conn:
        cursor = conn.execute("""
            DELETE FROM cover_approvals
            WHERE station_id NOT IN (SELECT id FROM stations)
        """)
        return cursor.rowcount


# Initialize database on module import
init_db()
