import os
from urllib.parse import urlsplit


def endpoint(name: str) -> str:
    value = os.environ.get(name, "https://api.openai.com/v1").rstrip("/")
    return validate_endpoint(value)


def validate_endpoint(value: str) -> str:
    value = value.rstrip("/")
    parts = urlsplit(value)
    if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username
            or parts.password or parts.query or parts.fragment):
        raise ValueError("Endpoint must be an HTTP(S) base URL without credentials, query or fragment")
    return value
