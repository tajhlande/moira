import logging
import sqlite3

from moira.persistence.interfaces import (
    CredentialRepository,
    CredentialRow,
)
from moira.persistence.sqlite.repos._connection import connect

logger = logging.getLogger(__name__)


class SqliteCredentialRepository(CredentialRepository):
    def __init__(self, db_path: str):
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        return connect(self._db_path)

    @staticmethod
    def _row_to_credential_row(row: sqlite3.Row) -> CredentialRow:
        return CredentialRow(
            owner=row["owner"],
            name=row["name"],
            encrypted_data=row["encrypted_data"],
            salt=row["salt"],
            encryption_version=row["encryption_version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def get_by_name(self, owner: str, name: str) -> CredentialRow | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT owner, name, encrypted_data, salt, "
                "encryption_version, created_at, updated_at "
                "FROM credentials WHERE owner = ? AND name = ?",
                (owner, name),
            ).fetchone()
            return self._row_to_credential_row(row) if row else None
        finally:
            conn.close()

    async def save(
        self,
        owner: str,
        name: str,
        encrypted_data: str,
        salt: str,
        encryption_version: int,
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO credentials "
                "(owner, name, encrypted_data, salt, encryption_version) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(owner, name) DO UPDATE SET "
                "encrypted_data = excluded.encrypted_data, "
                "salt = excluded.salt, "
                "encryption_version = excluded.encryption_version, "
                "updated_at = datetime('now')",
                (owner, name, encrypted_data, salt, encryption_version),
            )
            conn.commit()
        finally:
            conn.close()

    async def delete(self, owner: str, name: str) -> bool:
        conn = self._connect()
        try:
            cursor = conn.execute(
                "DELETE FROM credentials WHERE owner = ? AND name = ?",
                (owner, name),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    async def list_all(self, owner: str | None = None) -> list[CredentialRow]:
        conn = self._connect()
        try:
            if owner is not None:
                rows = conn.execute(
                    "SELECT owner, name, encrypted_data, salt, "
                    "encryption_version, created_at, updated_at "
                    "FROM credentials WHERE owner = ? ORDER BY name",
                    (owner,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT owner, name, encrypted_data, salt, "
                    "encryption_version, created_at, updated_at "
                    "FROM credentials ORDER BY owner, name"
                ).fetchall()
            return [self._row_to_credential_row(r) for r in rows]
        finally:
            conn.close()
