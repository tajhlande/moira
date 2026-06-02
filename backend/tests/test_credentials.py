import json

import pytest

from moira.persistence.sqlite.repos import SqliteCredentialRepository
from moira.persistence.sqlite.schema import run_migrations
from moira.services.credentials.credential_service import (
    CredentialEncryptionError,
    CredentialService,
)
from moira.services.credentials.credential_types import validate_credential_name
from moira.services.credentials.secrets import (
    decrypt_value,
    derive_key,
    encrypt_value,
    generate_salt,
)


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def cred_repo(db_path):
    run_migrations(db_path)
    return SqliteCredentialRepository(db_path)


@pytest.fixture
def master_key():
    return "test-master-key-that-is-at-least-32-bytes-long!!"


@pytest.fixture
def cred_service(cred_repo, master_key):
    return CredentialService(repo=cred_repo, master_key=master_key)


@pytest.fixture
def plaintext_service(cred_repo, monkeypatch):
    monkeypatch.setenv("MOIRA_ALLOW_PLAINTEXT_SECRETS", "true")
    return CredentialService(repo=cred_repo, master_key=None)


# --- secrets.py tests ---


class TestGenerateSalt:
    def test_returns_base64_string(self):
        salt = generate_salt()
        import base64

        decoded = base64.b64decode(salt)
        assert len(decoded) == 16

    def test_unique_salts(self):
        salts = {generate_salt() for _ in range(20)}
        assert len(salts) == 20


class TestDeriveKey:
    def test_returns_32_bytes(self, master_key):
        salt = generate_salt()
        key = derive_key(master_key, salt)
        assert len(key) == 44

    def test_deterministic(self, master_key):
        salt = generate_salt()
        key1 = derive_key(master_key, salt)
        key2 = derive_key(master_key, salt)
        assert key1 == key2

    def test_different_salts_different_keys(self, master_key):
        key1 = derive_key(master_key, generate_salt())
        key2 = derive_key(master_key, generate_salt())
        assert key1 != key2

    def test_different_keys_produce_different_derived(self):
        salt = generate_salt()
        key1 = derive_key("master-key-one-aaaaaaaaaaaaaaaaaa!", salt)
        key2 = derive_key("master-key-two-bbbbbbbbbbbbbbbbbb!", salt)
        assert key1 != key2


class TestEncryptDecrypt:
    def test_round_trip(self, master_key):
        salt = generate_salt()
        plaintext = '{"key": "secret-value"}'
        encrypted = encrypt_value(plaintext, master_key, salt)
        assert encrypted != plaintext
        decrypted = decrypt_value(encrypted, master_key, salt)
        assert decrypted == plaintext

    def test_different_salts_different_ciphertext(self, master_key):
        plaintext = '{"key": "value"}'
        salt1 = generate_salt()
        salt2 = generate_salt()
        encrypted1 = encrypt_value(plaintext, master_key, salt1)
        encrypted2 = encrypt_value(plaintext, master_key, salt2)
        assert encrypted1 != encrypted2

    def test_wrong_key_fails(self):
        salt = generate_salt()
        plaintext = '{"key": "value"}'
        encrypted = encrypt_value(plaintext, "correct-key-aaaaaaaaaaaaaaaaaaaaa!", salt)
        with pytest.raises(Exception):
            decrypt_value(encrypted, "wrong-key-bbbbbbbbbbbbbbbbbbbbb!", salt)

    def test_tampered_ciphertext_fails(self, master_key):
        salt = generate_salt()
        encrypted = encrypt_value("hello", master_key, salt)
        tampered = encrypted[:-5] + "XXXXX"
        with pytest.raises(Exception):
            decrypt_value(tampered, master_key, salt)


# --- credential_types.py tests ---


class TestValidateCredentialName:
    def test_valid_simple(self):
        validate_credential_name("api_key")

    def test_valid_dot_qualified(self):
        validate_credential_name("brave.api_key")

    def test_valid_deep_hierarchy(self):
        validate_credential_name("service.brave.production.api_key")

    def test_valid_leading_underscore(self):
        validate_credential_name("_private")

    def test_valid_numeric_after_first(self):
        validate_credential_name("key_123")

    def test_invalid_starts_with_number(self):
        with pytest.raises(ValueError, match="Invalid credential name"):
            validate_credential_name("123key")

    def test_invalid_empty(self):
        with pytest.raises(ValueError, match="Invalid credential name"):
            validate_credential_name("")

    def test_invalid_spaces(self):
        with pytest.raises(ValueError, match="Invalid credential name"):
            validate_credential_name("my key")

    def test_invalid_dashes(self):
        with pytest.raises(ValueError, match="Invalid credential name"):
            validate_credential_name("my-key")

    def test_invalid_dots_only(self):
        with pytest.raises(ValueError, match="Invalid credential name"):
            validate_credential_name("...")

    def test_invalid_special_chars(self):
        with pytest.raises(ValueError, match="Invalid credential name"):
            validate_credential_name("key@value")


# --- SqliteCredentialRepository tests ---


@pytest.mark.asyncio
class TestSqliteCredentialRepository:
    async def test_save_and_get(self, cred_repo):
        await cred_repo.save(
            owner="system",
            name="brave.api_key",
            encrypted_data="encrypted-data",
            salt="some-salt",
            encryption_version=1,
        )
        row = await cred_repo.get_by_name("system", "brave.api_key")
        assert row is not None
        assert row.owner == "system"
        assert row.name == "brave.api_key"
        assert row.encrypted_data == "encrypted-data"
        assert row.salt == "some-salt"
        assert row.encryption_version == 1

    async def test_get_not_found(self, cred_repo):
        row = await cred_repo.get_by_name("system", "nonexistent")
        assert row is None

    async def test_upsert_preserves_created_at(self, cred_repo):
        await cred_repo.save(
            owner="system", name="test.key",
            encrypted_data="first", salt="salt1", encryption_version=1,
        )
        first = await cred_repo.get_by_name("system", "test.key")
        assert first is not None
        created_at = first.created_at

        await cred_repo.save(
            owner="system", name="test.key",
            encrypted_data="second", salt="salt2", encryption_version=1,
        )
        updated = await cred_repo.get_by_name("system", "test.key")
        assert updated is not None
        assert updated.encrypted_data == "second"
        assert updated.salt == "salt2"
        assert updated.created_at == created_at
        assert updated.updated_at >= created_at

    async def test_delete(self, cred_repo):
        await cred_repo.save(
            owner="system", name="to.delete",
            encrypted_data="data", salt="salt", encryption_version=1,
        )
        deleted = await cred_repo.delete("system", "to.delete")
        assert deleted is True
        row = await cred_repo.get_by_name("system", "to.delete")
        assert row is None

    async def test_delete_not_found(self, cred_repo):
        deleted = await cred_repo.delete("system", "nonexistent")
        assert deleted is False

    async def test_list_all_with_owner_filter(self, cred_repo):
        await cred_repo.save("owner_a", "key1", "data1", "salt1", 1)
        await cred_repo.save("owner_b", "key2", "data2", "salt2", 1)
        await cred_repo.save("owner_a", "key3", "data3", "salt3", 1)

        rows_a = await cred_repo.list_all(owner="owner_a")
        assert len(rows_a) == 2
        names_a = {r.name for r in rows_a}
        assert names_a == {"key1", "key3"}

        rows_b = await cred_repo.list_all(owner="owner_b")
        assert len(rows_b) == 1
        assert rows_b[0].name == "key2"

    async def test_list_all_no_filter(self, cred_repo):
        await cred_repo.save("owner_a", "key1", "data1", "salt1", 1)
        await cred_repo.save("owner_b", "key2", "data2", "salt2", 1)

        rows = await cred_repo.list_all()
        assert len(rows) == 2

    async def test_list_all_empty(self, cred_repo):
        rows = await cred_repo.list_all()
        assert rows == []

    async def test_different_owners_same_name(self, cred_repo):
        await cred_repo.save("owner_a", "shared.key", "data_a", "salt_a", 1)
        await cred_repo.save("owner_b", "shared.key", "data_b", "salt_b", 1)

        row_a = await cred_repo.get_by_name("owner_a", "shared.key")
        row_b = await cred_repo.get_by_name("owner_b", "shared.key")
        assert row_a.encrypted_data == "data_a"
        assert row_b.encrypted_data == "data_b"


# --- CredentialService tests ---


@pytest.mark.asyncio
class TestCredentialServiceEncrypted:
    async def test_store_and_get(self, cred_service):
        info = await cred_service.store_credential(
            "brave.api_key", {"key": "BSA-secret-key"}
        )
        assert info.name == "brave.api_key"
        assert info.owner == "system"
        assert info.encryption_version == 1

        value = await cred_service.get_credential("brave.api_key")
        assert value == {"key": "BSA-secret-key"}

    async def test_get_not_found(self, cred_service):
        value = await cred_service.get_credential("nonexistent")
        assert value is None

    async def test_store_with_custom_owner(self, cred_service):
        info = await cred_service.store_credential(
            "my.key", {"token": "abc"}, owner="custom"
        )
        assert info.owner == "custom"
        value = await cred_service.get_credential("my.key", owner="custom")
        assert value == {"token": "abc"}

    async def test_update_credential_reuses_salt(self, cred_service):
        await cred_service.store_credential("test.key", {"v": "1"})
        repo = cred_service._repo
        row_before = await repo.get_by_name("system", "test.key")
        assert row_before is not None
        salt_before = row_before.salt

        await cred_service.store_credential("test.key", {"v": "2"})

        row_after = await repo.get_by_name("system", "test.key")
        assert row_after is not None
        assert row_after.salt == salt_before
        assert row_after.encrypted_data != row_before.encrypted_data

        value = await cred_service.get_credential("test.key")
        assert value == {"v": "2"}

    async def test_delete(self, cred_service):
        await cred_service.store_credential("to.delete", {"key": "val"})
        deleted = await cred_service.delete_credential("to.delete")
        assert deleted is True
        value = await cred_service.get_credential("to.delete")
        assert value is None

    async def test_delete_not_found(self, cred_service):
        deleted = await cred_service.delete_credential("nonexistent")
        assert deleted is False

    async def test_list_credentials(self, cred_service):
        await cred_service.store_credential("key1", {"a": "1"})
        await cred_service.store_credential("key2", {"b": "2"})

        creds = await cred_service.list_credentials()
        assert len(creds) == 2
        names = {c.name for c in creds}
        assert names == {"key1", "key2"}
        for c in creds:
            assert c.owner == "system"
            assert c.encryption_version == 1

    async def test_list_credentials_with_owner_filter(self, cred_service):
        await cred_service.store_credential("key1", {"a": "1"}, owner="alice")
        await cred_service.store_credential("key2", {"b": "2"}, owner="bob")

        alice_creds = await cred_service.list_credentials(owner="alice")
        assert len(alice_creds) == 1
        assert alice_creds[0].name == "key1"

    async def test_list_credentials_empty(self, cred_service):
        creds = await cred_service.list_credentials()
        assert creds == []

    async def test_invalid_name_rejected(self, cred_service):
        with pytest.raises(ValueError, match="Invalid credential name"):
            await cred_service.store_credential("bad name!", {"key": "val"})

    async def test_encrypted_data_is_not_plaintext(self, cred_service):
        secret = {"key": "super-secret-value"}
        await cred_service.store_credential("test.enc", secret)

        repo = cred_service._repo
        row = await repo.get_by_name("system", "test.enc")
        assert row is not None
        assert row.encrypted_data != json.dumps(secret)
        assert "super-secret-value" not in row.encrypted_data

    async def test_credential_info_to_dict(self, cred_service):
        await cred_service.store_credential("dict.test", {"x": "y"})
        creds = await cred_service.list_credentials()
        info = creds[0]
        d = info.to_dict()
        assert d["owner"] == "system"
        assert d["name"] == "dict.test"
        assert d["encryption_version"] == 1
        assert "created_at" in d
        assert "updated_at" in d

    async def test_api_key_credential(self, cred_service):
        await cred_service.store_credential("api.test", {"key": "abc123"})
        value = await cred_service.get_credential("api.test")
        assert value == {"key": "abc123"}

    async def test_username_password_credential(self, cred_service):
        await cred_service.store_credential(
            "auth.test", {"username": "admin", "password": "s3cret"}
        )
        value = await cred_service.get_credential("auth.test")
        assert value == {"username": "admin", "password": "s3cret"}

    async def test_bearer_token_credential(self, cred_service):
        await cred_service.store_credential(
            "token.test", {"token": "eyJhbGciOiJIUzI1NiJ9.test.sig"}
        )
        value = await cred_service.get_credential("token.test")
        assert value == {"token": "eyJhbGciOiJIUzI1NiJ9.test.sig"}

    async def test_complex_credential(self, cred_service):
        value = {
            "client_id": "abc",
            "client_secret": "def",
            "tenant_id": "xyz",
        }
        await cred_service.store_credential("oauth.test", value)
        retrieved = await cred_service.get_credential("oauth.test")
        assert retrieved == value


@pytest.mark.asyncio
class TestCredentialServicePlaintext:
    async def test_plaintext_round_trip(self, plaintext_service):
        await plaintext_service.store_credential("plain.key", {"key": "visible"})
        value = await plaintext_service.get_credential("plain.key")
        assert value == {"key": "visible"}

    async def test_plaintext_data_stored_as_json(self, plaintext_service):
        await plaintext_service.store_credential("plain.key", {"key": "visible"})
        repo = plaintext_service._repo
        row = await repo.get_by_name("system", "plain.key")
        assert row is not None
        assert json.loads(row.encrypted_data) == {"key": "visible"}


class TestCredentialServiceConstruction:
    def test_no_key_no_plaintext_raises(self, cred_repo, monkeypatch):
        monkeypatch.delenv("MOIRA_SECRETS_KEY", raising=False)
        monkeypatch.delenv("MOIRA_ALLOW_PLAINTEXT_SECRETS", raising=False)
        with pytest.raises(CredentialEncryptionError, match="MOIRA_SECRETS_KEY"):
            CredentialService(repo=cred_repo, master_key=None)

    def test_plaintext_allowed_with_env(self, cred_repo, monkeypatch):
        monkeypatch.setenv("MOIRA_ALLOW_PLAINTEXT_SECRETS", "true")
        svc = CredentialService(repo=cred_repo, master_key=None)
        assert svc._plaintext is True

    def test_encryption_mode(self, cred_repo, master_key):
        svc = CredentialService(repo=cred_repo, master_key=master_key)
        assert svc._plaintext is False
