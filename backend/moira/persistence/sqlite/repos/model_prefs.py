import logging
import sqlite3

from moira.persistence.interfaces import (
    ModelPreferences,
    ModelPreferencesRepository,
)
from moira.persistence.sqlite.repos._connection import connect

logger = logging.getLogger(__name__)


class SqliteModelPreferencesRepository(ModelPreferencesRepository):
    def __init__(self, db_path: str):
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        return connect(self._db_path)

    async def get_preferences(self, user_id: str) -> ModelPreferences:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT user_id, intelligence_endpoint, intelligence_model, "
                "task_endpoint, task_model FROM model_preferences "
                "WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row is None:
                return ModelPreferences(user_id=user_id)
            return ModelPreferences(**dict(row))
        finally:
            conn.close()

    async def set_preferences(self, preferences: ModelPreferences) -> None:
        logger.debug("Setting model preferences for user %s", preferences.user_id)
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO model_preferences "
                "(user_id, intelligence_endpoint, intelligence_model, "
                "task_endpoint, task_model) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "intelligence_endpoint = excluded.intelligence_endpoint, "
                "intelligence_model = excluded.intelligence_model, "
                "task_endpoint = excluded.task_endpoint, "
                "task_model = excluded.task_model",
                (
                    preferences.user_id,
                    preferences.intelligence_endpoint,
                    preferences.intelligence_model,
                    preferences.task_endpoint,
                    preferences.task_model,
                ),
            )
            conn.commit()
        finally:
            conn.close()
