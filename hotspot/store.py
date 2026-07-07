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
                    authorized_at INTEGER
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
                    authorized_at = NULL
                """,
                (client_mac, phone, ap_mac, redirect_url, int(time.time())),
            )

    def mark_authorized(self, client_mac: str) -> int:
        authorized_at = int(time.time())
        with self._connect() as conn:
            conn.execute(
                "UPDATE hotspot_sessions SET authorized_at = ? WHERE client_mac = ?",
                (authorized_at, client_mac),
            )
        return authorized_at

    def clear_authorized(self, client_mac: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE hotspot_sessions SET authorized_at = NULL WHERE client_mac = ?",
                (client_mac,),
            )

    def get_session(self, client_mac: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM hotspot_sessions WHERE client_mac = ?",
                (client_mac,),
            ).fetchone()
