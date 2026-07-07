import sqlite3
import time
from pathlib import Path
from typing import Optional


class Store:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS otp_codes (
                    phone TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    code_hash TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    sent_at INTEGER NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    verified_at INTEGER,
                    PRIMARY KEY (phone, purpose)
                )
                """
            )
            conn.execute(
                "PRAGMA journal_mode=WAL"
            )

    def get_otp(self, phone: str, purpose: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM otp_codes WHERE phone = ? AND purpose = ?",
                (phone, purpose),
            ).fetchone()

    def save_otp(self, phone: str, purpose: str, code_hash: str, ttl_seconds: int) -> None:
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO otp_codes(phone, purpose, code_hash, expires_at, sent_at, attempts, verified_at)
                VALUES (?, ?, ?, ?, ?, 0, NULL)
                ON CONFLICT(phone, purpose) DO UPDATE SET
                    code_hash = excluded.code_hash,
                    expires_at = excluded.expires_at,
                    sent_at = excluded.sent_at,
                    attempts = 0,
                    verified_at = NULL
                """,
                (phone, purpose, code_hash, now + ttl_seconds, now),
            )

    def increment_attempts(self, phone: str, purpose: str) -> int:
        with self._connect() as conn:
            conn.execute(
                "UPDATE otp_codes SET attempts = attempts + 1 WHERE phone = ? AND purpose = ?",
                (phone, purpose),
            )
            row = conn.execute(
                "SELECT attempts FROM otp_codes WHERE phone = ? AND purpose = ?",
                (phone, purpose),
            ).fetchone()
            return int(row["attempts"]) if row else 0

    def mark_verified(self, phone: str, purpose: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE otp_codes SET verified_at = ? WHERE phone = ? AND purpose = ?",
                (int(time.time()), phone, purpose),
            )
