import logging

import jsonschema

from moira.persistence.interfaces import (
    SCOPE_SYSTEM,
    SYSTEM_SCOPE_ID,
    ResolvedSetting,
    SettingEntry,
    SystemSettingsRepository,
)
from moira.services.settings.definitions import SETTING_DEFINITIONS, SettingDefinition

logger = logging.getLogger(__name__)


class UnknownSettingError(KeyError):
    pass


class InvalidSettingValueError(ValueError):
    pass


class SettingsService:
    def __init__(self, repo: SystemSettingsRepository):
        self._repo = repo

    async def seed_defaults(self) -> None:
        """Ensure every known setting key has a row in the DB.

        Insert-only: keys already present are left untouched so runtime
        changes survive server restarts. Called once at startup."""
        existing = await self._repo.get_prefix("", SCOPE_SYSTEM, SYSTEM_SCOPE_ID)
        existing_keys = {e.key for e in existing}
        missing = [
            SettingEntry(key=k, value=d.default, scope=SCOPE_SYSTEM, scope_id=SYSTEM_SCOPE_ID)
            for k, d in SETTING_DEFINITIONS.items()
            if k not in existing_keys
        ]
        if missing:
            await self._repo.set_batch(missing)
            logger.info("Seeded %d missing setting defaults", len(missing))

    async def get(
        self, key: str, scopes: list[tuple[str, str]] | None = None
    ) -> ResolvedSetting | None:
        """Resolve a setting by walking scope layers in precedence order.

        If scopes is provided, checks each (scope, scope_id) in order and
        returns the first hit. If no layer has a value, falls back to the
        definition's default. Returns None only for unknown keys."""
        defn = SETTING_DEFINITIONS.get(key)
        if defn is None:
            return None

        if scopes is not None:
            for scope, scope_id in scopes:
                entry = await self._repo.get(key, scope, scope_id)
                if entry is not None:
                    return ResolvedSetting(
                        key=key,
                        value=entry.value,
                        type=defn.type,
                        scope=scope,
                        scope_id=scope_id,
                    )

        return ResolvedSetting(
            key=key,
            value=defn.default,
            type=defn.type,
            scope=SCOPE_SYSTEM,
            scope_id=SYSTEM_SCOPE_ID,
        )

    async def get_prefix(
        self, prefix: str, scope: str = SCOPE_SYSTEM, scope_id: str = SYSTEM_SCOPE_ID
    ) -> list[SettingEntry]:
        """Fetch all settings whose keys start with the given prefix.

        Used by the API to load a settings group (e.g. all "budget.cost.*"
        entries) and by the run manager to bulk-resolve cost weights."""
        return await self._repo.get_prefix(prefix, scope, scope_id)

    async def get_typed(self, key: str, scopes: list[tuple[str, str]] | None = None):
        """Like get() but returns the value as a native Python type.

        Parses the stored string into int/float/bool according to the
        key's definition. Used by sync consumers that receive resolved
        values through run state."""
        resolved = await self.get(key, scopes)
        if resolved is None:
            return None
        defn = SETTING_DEFINITIONS[key]
        return defn.parse(resolved.value)

    async def get_typed_prefix(
        self, prefix: str, scope: str = SCOPE_SYSTEM, scope_id: str = SYSTEM_SCOPE_ID
    ) -> dict[str, int | float | bool | str]:
        """Bulk-resolve all settings under a prefix as native Python types.

        Returns a dict keyed by the suffix after the prefix (e.g. prefix
        "budget.cost." yields {"planning": 2, "verification": 4, ...}).
        Intended for the run manager to resolve cost weights once at the
        async boundary."""
        entries = await self._repo.get_prefix(prefix, scope, scope_id)
        result: dict[str, int | float | bool | str] = {}
        for entry in entries:
            defn = SETTING_DEFINITIONS.get(entry.key)
            if defn is None:
                continue
            short_key = entry.key[len(prefix):]
            if short_key.startswith("."):
                short_key = short_key[1:]
            result[short_key] = defn.parse(entry.value)
        return result

    async def set(
        self, key: str, value: str, scope: str = SCOPE_SYSTEM, scope_id: str = SYSTEM_SCOPE_ID
    ) -> None:
        """Write a setting after two-layer validation.

        Layer 1: structural validation via JSON Schema (type, range).
        Layer 2: semantic validation (not yet wired — will check things
        like model availability when inference settings are added)."""
        defn = SETTING_DEFINITIONS.get(key)
        if defn is None:
            raise UnknownSettingError(key)

        parsed = defn.parse(value)
        jsonschema.validate(instance=parsed, schema=defn.constraints)

        await self._repo.set(
            SettingEntry(key=key, value=value, scope=scope, scope_id=scope_id)
        )

    async def set_batch(self, entries: list[SettingEntry]) -> None:
        """Validate all entries, then persist atomically.

        All-or-nothing: if any entry fails validation, nothing is written."""
        for entry in entries:
            defn = SETTING_DEFINITIONS.get(entry.key)
            if defn is None:
                raise UnknownSettingError(entry.key)
            parsed = defn.parse(entry.value)
            jsonschema.validate(instance=parsed, schema=defn.constraints)

        await self._repo.set_batch(entries)

    async def reset_defaults(
        self,
        keys: list[str] | None = None,
        scope: str = SCOPE_SYSTEM,
        scope_id: str = SYSTEM_SCOPE_ID,
    ) -> None:
        """Restore specified keys (or all) to their definition defaults.

        Deletes existing rows then re-inserts with default values so the
        DB state matches a fresh seed."""
        target_keys = keys or list(SETTING_DEFINITIONS.keys())
        for key in target_keys:
            if key not in SETTING_DEFINITIONS:
                continue
            await self._repo.delete(key, scope, scope_id)
        entries = [
            SettingEntry(
                key=k,
                value=SETTING_DEFINITIONS[k].default,
                scope=scope,
                scope_id=scope_id,
            )
            for k in target_keys
            if k in SETTING_DEFINITIONS
        ]
        if entries:
            await self._repo.set_batch(entries)

    def get_definition(self, key: str) -> SettingDefinition | None:
        """Look up a single definition by key. Used by the router for
        response enrichment."""
        return SETTING_DEFINITIONS.get(key)

    def get_all_definitions(self) -> list[SettingDefinition]:
        """Return all known definitions. Used by the definitions API
        endpoint for dynamic UI rendering."""
        return list(SETTING_DEFINITIONS.values())
