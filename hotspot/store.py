import sqlite3
import time
from dataclasses import dataclass
from typing import Optional

from api.store import Store


@dataclass
class HotspotStore:
    store: Store

    def __post_init__(self) -> None:
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return self.store._connect()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS hotspot_sessions (
                    client_mac TEXT PRIMARY KEY,
                    phone TEXT NOT NULL,
                    ap_mac TEXT,
                    redirect_url TEXT,
                    requested_at INTEGER NOT NULL,
                    authorized_at INTEGER,
                    valid_until INTEGER,
                    revoked_at INTEGER,
                    blocked_at INTEGER,
                    blocked_reason TEXT,
                    display_name TEXT
                )
                """
            )
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(hotspot_sessions)").fetchall()
            }
            for name, definition in {
                "valid_until": "INTEGER",
                "revoked_at": "INTEGER",
                "blocked_at": "INTEGER",
                "blocked_reason": "TEXT",
                "display_name": "TEXT",
            }.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE hotspot_sessions ADD COLUMN {name} {definition}")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS hotspot_authorizations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_mac TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    ap_mac TEXT,
                    redirect_url TEXT,
                    authorized_at INTEGER NOT NULL,
                    valid_until INTEGER NOT NULL,
                    minutes INTEGER NOT NULL,
                    revoked_at INTEGER,
                    blocked_at INTEGER
                )
                """
            )
            auth_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(hotspot_authorizations)").fetchall()
            }
            if "display_name" not in auth_columns:
                conn.execute("ALTER TABLE hotspot_authorizations ADD COLUMN display_name TEXT")
            conn.execute(
                """
                UPDATE hotspot_authorizations AS older
                SET revoked_at = (
                    SELECT MIN(newer.authorized_at)
                    FROM hotspot_authorizations AS newer
                    WHERE newer.client_mac = older.client_mac
                      AND newer.id > older.id
                      AND newer.revoked_at IS NULL
                      AND newer.blocked_at IS NULL
                )
                WHERE older.revoked_at IS NULL
                  AND older.blocked_at IS NULL
                  AND EXISTS (
                    SELECT 1
                    FROM hotspot_authorizations AS newer
                    WHERE newer.client_mac = older.client_mac
                      AND newer.id > older.id
                      AND newer.revoked_at IS NULL
                      AND newer.blocked_at IS NULL
                  )
                """
            )

    def save_session(
        self,
        client_mac: str,
        phone: str,
        ap_mac: Optional[str],
        redirect_url: Optional[str],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO hotspot_sessions(client_mac, phone, ap_mac, redirect_url, requested_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(client_mac) DO UPDATE SET
                    phone = excluded.phone,
                    ap_mac = excluded.ap_mac,
                    redirect_url = excluded.redirect_url,
                    requested_at = excluded.requested_at,
                    authorized_at = NULL,
                    valid_until = NULL,
                    revoked_at = NULL
                """,
                (client_mac, phone, ap_mac, redirect_url, int(time.time())),
            )

    def mark_authorized(self, client_mac: str, minutes: int) -> int:
        authorized_at = int(time.time())
        valid_until = authorized_at + minutes * 60
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE hotspot_sessions
                SET authorized_at = ?, valid_until = ?, revoked_at = NULL
                WHERE client_mac = ?
                """,
                (authorized_at, valid_until, client_mac),
            )
            row = conn.execute(
                """
                SELECT client_mac, phone, ap_mac, redirect_url, display_name
                FROM hotspot_sessions
                WHERE client_mac = ?
                """,
                (client_mac,),
            ).fetchone()
            if row:
                # A client can only have one current authorization. Extending
                # access closes the previous history row before creating the new one.
                conn.execute(
                    """
                    UPDATE hotspot_authorizations
                    SET revoked_at = ?
                    WHERE client_mac = ? AND revoked_at IS NULL AND blocked_at IS NULL
                    """,
                    (authorized_at, client_mac),
                )
                conn.execute(
                    """
                    INSERT INTO hotspot_authorizations(
                        client_mac, phone, ap_mac, redirect_url, display_name,
                        authorized_at, valid_until, minutes
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["client_mac"],
                        row["phone"],
                        row["ap_mac"],
                        row["redirect_url"],
                        row["display_name"],
                        authorized_at,
                        valid_until,
                        minutes,
                    ),
                )
        return authorized_at

    def set_display_name(self, client_mac: str, display_name: str) -> bool:
        value = display_name.strip()[:120] or None
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE hotspot_sessions SET display_name = ? WHERE client_mac = ?",
                (value, client_mac.lower()),
            )
            conn.execute(
                "UPDATE hotspot_authorizations SET display_name = ? WHERE client_mac = ?",
                (value, client_mac.lower()),
            )
            return cursor.rowcount > 0

    def clear_authorized(self, client_mac: str) -> None:
        revoked_at = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE hotspot_sessions
                SET authorized_at = NULL, valid_until = NULL, revoked_at = ?
                WHERE client_mac = ?
                """,
                (revoked_at, client_mac),
            )
            conn.execute(
                """
                UPDATE hotspot_authorizations
                SET revoked_at = ?
                WHERE client_mac = ? AND revoked_at IS NULL AND blocked_at IS NULL
                """,
                (revoked_at, client_mac),
            )

    def get_session(self, client_mac: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM hotspot_sessions WHERE client_mac = ?",
                (client_mac,),
            ).fetchone()

    def list_active_sessions(self) -> list[sqlite3.Row]:
        now = int(time.time())
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT *
                FROM hotspot_sessions
                WHERE authorized_at IS NOT NULL
                  AND COALESCE(valid_until, authorized_at) > ?
                  AND revoked_at IS NULL
                  AND blocked_at IS NULL
                ORDER BY valid_until DESC, authorized_at DESC
                """,
                (now,),
            ).fetchall()

    def list_archive(self, limit: int = 200) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT *
                FROM hotspot_authorizations
                ORDER BY authorized_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    def mark_blocked(self, client_mac: str, reason: str = "") -> None:
        blocked_at = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE hotspot_sessions
                SET authorized_at = NULL,
                    valid_until = NULL,
                    revoked_at = ?,
                    blocked_at = ?,
                    blocked_reason = ?
                WHERE client_mac = ?
                """,
                (blocked_at, blocked_at, reason, client_mac),
            )
            conn.execute(
                """
                UPDATE hotspot_authorizations
                SET blocked_at = ?
                WHERE client_mac = ? AND blocked_at IS NULL
                """,
                (blocked_at, client_mac),
            )
