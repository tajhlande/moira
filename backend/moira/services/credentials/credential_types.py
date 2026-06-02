import re
from typing import Any, TypedDict

CREDENTIAL_NAME_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.]*$")

RESERVED_NAMESPACES = ("system.", "provider.", "service.", "tool.", "user.")


class ApiKeyValue(TypedDict):
    key: str


class UsernamePasswordValue(TypedDict):
    username: str
    password: str


class BearerTokenValue(TypedDict):
    token: str


CredentialValue = dict[str, Any]


def validate_credential_name(name: str) -> None:
    if not CREDENTIAL_NAME_PATTERN.match(name):
        raise ValueError(
            f"Invalid credential name '{name}'. "
            "Must match [a-zA-Z_][a-zA-Z0-9_.]*"
        )
