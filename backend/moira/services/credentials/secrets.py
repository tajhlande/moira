import base64
import logging
import os
import secrets

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger(__name__)

HKDF_INFO = b"moira-credential-encryption"
HKDF_LENGTH = 32
SALT_BYTES = 16


def is_encryption_configured() -> bool:
    return bool(os.environ.get("MOIRA_SECRETS_KEY"))


def is_plaintext_allowed() -> bool:
    return os.environ.get("MOIRA_ALLOW_PLAINTEXT_SECRETS", "").lower() in ("true", "1", "yes")


def get_master_key() -> str | None:
    return os.environ.get("MOIRA_SECRETS_KEY")


def generate_salt() -> str:
    return base64.b64encode(secrets.token_bytes(SALT_BYTES)).decode()


def derive_key(master_key: str, salt: str) -> bytes:
    hkdf = HKDF(
        algorithm=SHA256(),
        length=HKDF_LENGTH,
        salt=base64.b64decode(salt),
        info=HKDF_INFO,
    )
    raw = hkdf.derive(master_key.encode())
    return base64.urlsafe_b64encode(raw)


def encrypt_value(plaintext: str, master_key: str, salt: str) -> str:
    key = derive_key(master_key, salt)
    return Fernet(key).encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str, master_key: str, salt: str) -> str:
    key = derive_key(master_key, salt)
    return Fernet(key).decrypt(ciphertext.encode()).decode()
