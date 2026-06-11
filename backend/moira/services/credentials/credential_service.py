import json
import logging
from dataclasses import dataclass
from typing import Any

from moira.persistence.interfaces import CredentialRepository, CredentialRow
from moira.services.credentials.credential_types import (
    CredentialValue,
    validate_credential_name,
)
from moira.services.credentials.secrets import (
    decrypt_value,
    encrypt_value,
    generate_salt,
    is_plaintext_allowed,
)

logger = logging.getLogger(__name__)

ENCRYPTION_VERSION = 1


class CredentialError(Exception):
    pass


class CredentialEncryptionError(CredentialError):
    pass


@dataclass
class CredentialInfo:
    owner: str
    name: str
    encryption_version: int
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "name": self.name,
            "encryption_version": self.encryption_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class CredentialService:
    DEFAULT_OWNER = "system"

    def __init__(self, repo: "CredentialRepository", master_key: str | None):
        if master_key is None and not is_plaintext_allowed():
            raise CredentialEncryptionError(
                "MOIRA_SECRETS_KEY not configured and plaintext storage is "
                "not allowed. Set MOIRA_SECRETS_KEY or explicitly enable "
                "plaintext mode with MOIRA_ALLOW_PLAINTEXT_SECRETS=true."
            )
        self._repo = repo
        self._master_key = master_key
        self._plaintext = master_key is None
        if self._plaintext:
            logger.warning(
                "!!!!!!!!!! Credential service running in PLAINTEXT mode. "
                "Secrets will NOT be encrypted at rest. !!!!!!!!!!"
            )
        else:
            logger.info("Credential service running with encryption enabled")

    async def get_credential(self, name: str, owner: str | None = None) -> CredentialValue | None:
        owner = owner or self.DEFAULT_OWNER
        row = await self._repo.get_by_name(owner, name)
        if row is None:
            return None
        return self._decrypt(row)

    async def store_credential(
        self,
        name: str,
        value: CredentialValue,
        owner: str | None = None,
    ) -> CredentialInfo:
        owner = owner or self.DEFAULT_OWNER
        validate_credential_name(name)
        plaintext = json.dumps(value, sort_keys=True)

        existing = await self._repo.get_by_name(owner, name)
        if existing is not None:
            salt = existing.salt
        else:
            salt = generate_salt()

        encrypted_data = self._encrypt(plaintext, salt)
        await self._repo.save(
            owner=owner,
            name=name,
            encrypted_data=encrypted_data,
            salt=salt,
            encryption_version=ENCRYPTION_VERSION,
        )

        row = await self._repo.get_by_name(owner, name)
        assert row is not None
        return self._row_to_info(row)

    async def delete_credential(self, name: str, owner: str | None = None) -> bool:
        owner = owner or self.DEFAULT_OWNER
        return await self._repo.delete(owner, name)

    async def list_credentials(self, owner: str | None = None) -> list[CredentialInfo]:
        rows = await self._repo.list_all(owner=owner)
        return [self._row_to_info(r) for r in rows]

    def _encrypt(self, plaintext: str, salt: str) -> str:
        if self._plaintext:
            return plaintext
        assert self._master_key
        return encrypt_value(plaintext, self._master_key, salt)

    def _decrypt(self, row: CredentialRow) -> CredentialValue:
        if self._plaintext:
            return json.loads(row.encrypted_data)
        assert self._master_key
        decrypted = decrypt_value(row.encrypted_data, self._master_key, row.salt)
        return json.loads(decrypted)

    @staticmethod
    def _row_to_info(row: CredentialRow) -> CredentialInfo:
        return CredentialInfo(
            owner=row.owner,
            name=row.name,
            encryption_version=row.encryption_version,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
