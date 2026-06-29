import logging
import sqlite3

from moira.persistence.interfaces import (
    InferenceModelRow,
    InferenceProvider,
    InferenceProviderRepository,
)
from moira.persistence.sqlite.repos._connection import connect

logger = logging.getLogger(__name__)


class SqliteInferenceProviderRepository(InferenceProviderRepository):
    """SQLite-backed repository for inference_providers and inference_models tables.

    Providers hold connection metadata (base_url, provider_type, credential_name).
    The slug is a kebab-case identifier used as the primary key; display_name is
    the human-readable label. Per-model capability flags (native_tool_calling)
    are stored in inference_models and are preserved across model discovery
    refreshes."""

    def __init__(self, db_path: str):
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        return connect(self._db_path)

    async def get_all_providers(self) -> list[InferenceProvider]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT slug, display_name, base_url, provider_type, "
                "credential_name, created_at, updated_at "
                "FROM inference_providers ORDER BY display_name"
            ).fetchall()
            return [InferenceProvider(**dict(r)) for r in rows]
        finally:
            conn.close()

    async def get_provider(self, slug: str) -> InferenceProvider | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT slug, display_name, base_url, provider_type, "
                "credential_name, created_at, updated_at "
                "FROM inference_providers WHERE slug = ?",
                (slug,),
            ).fetchone()
            return InferenceProvider(**dict(row)) if row else None
        finally:
            conn.close()

    async def upsert_provider(self, provider: InferenceProvider) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO inference_providers "
                "(slug, display_name, base_url, provider_type, credential_name, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now')) "
                "ON CONFLICT(slug) DO UPDATE SET "
                "display_name = excluded.display_name, "
                "base_url = excluded.base_url, "
                "provider_type = excluded.provider_type, "
                "credential_name = excluded.credential_name, "
                "updated_at = datetime('now')",
                (
                    provider.slug,
                    provider.display_name,
                    provider.base_url,
                    provider.provider_type,
                    provider.credential_name,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    async def delete_provider(self, slug: str) -> bool:
        conn = self._connect()
        try:
            # FK ON DELETE CASCADE removes associated inference_models rows
            cursor = conn.execute("DELETE FROM inference_providers WHERE slug = ?", (slug,))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    async def get_models(self, provider_slug: str) -> list[InferenceModelRow]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT provider_slug, model_id, native_tool_calling, discovered_at "
                "FROM inference_models WHERE provider_slug = ? ORDER BY model_id",
                (provider_slug,),
            ).fetchall()
            return [
                InferenceModelRow(
                    provider_slug=r["provider_slug"],
                    model_id=r["model_id"],
                    native_tool_calling=bool(r["native_tool_calling"]),
                    discovered_at=r["discovered_at"],
                )
                for r in rows
            ]
        finally:
            conn.close()

    async def get_all_models(self) -> list[InferenceModelRow]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT provider_slug, model_id, native_tool_calling, discovered_at "
                "FROM inference_models ORDER BY provider_slug, model_id"
            ).fetchall()
            return [
                InferenceModelRow(
                    provider_slug=r["provider_slug"],
                    model_id=r["model_id"],
                    native_tool_calling=bool(r["native_tool_calling"]),
                    discovered_at=r["discovered_at"],
                )
                for r in rows
            ]
        finally:
            conn.close()

    async def upsert_model(
        self, provider_slug: str, model_id: str, native_tool_calling: bool
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO inference_models "
                "(provider_slug, model_id, native_tool_calling, discovered_at) "
                "VALUES (?, ?, ?, datetime('now')) "
                "ON CONFLICT(provider_slug, model_id) DO UPDATE SET "
                "native_tool_calling = excluded.native_tool_calling",
                (provider_slug, model_id, int(native_tool_calling)),
            )
            conn.commit()
        finally:
            conn.close()

    async def upsert_discovered_models(self, provider_slug: str, model_ids: list[str]) -> None:
        """Insert newly discovered models without overwriting existing capability
        flags. Models already in the table are left untouched so user-configured
        native_tool_calling settings survive discovery refreshes."""
        if not model_ids:
            return
        conn = self._connect()
        try:
            for model_id in model_ids:
                conn.execute(
                    "INSERT INTO inference_models "
                    "(provider_slug, model_id, native_tool_calling, discovered_at) "
                    "VALUES (?, ?, 0, datetime('now')) "
                    "ON CONFLICT(provider_slug, model_id) DO NOTHING",
                    (provider_slug, model_id),
                )
            conn.commit()
        finally:
            conn.close()

    async def delete_stale_models(self, provider_slug: str, current_model_ids: list[str]) -> None:
        """Remove models no longer reported by the provider. Keeps the
        inference_models table in sync with what the provider actually serves."""
        conn = self._connect()
        try:
            if current_model_ids:
                placeholders = ",".join("?" for _ in current_model_ids)
                conn.execute(
                    f"DELETE FROM inference_models WHERE provider_slug = ? "
                    f"AND model_id NOT IN ({placeholders})",
                    (provider_slug, *current_model_ids),
                )
            else:
                conn.execute(
                    "DELETE FROM inference_models WHERE provider_slug = ?",
                    (provider_slug,),
                )
            conn.commit()
        finally:
            conn.close()
