import os
from urllib.parse import urlsplit


def endpoint(name: str) -> str:
    value = os.environ.get(name, "https://api.openai.com/v1").rstrip("/")
    parts = urlsplit(value)
    if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username
            or parts.password or parts.query or parts.fragment):
        raise ValueError(f"{name} must be an HTTP(S) base URL without credentials, query or fragment")
    return value
