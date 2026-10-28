import logging
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit


_SECRET_KEY_PARTS = ("password", "token", "secret", "authorization", "api_key")


def _redact_url(value: str) -> str:
    parts = urlsplit(value)
    if not parts.username and not parts.password:
        return value
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


def redact_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key)
        if any(part in key_text.lower() for part in _SECRET_KEY_PARTS):
            result[key_text] = "[redacted]"
        elif isinstance(item, Mapping):
            result[key_text] = redact_mapping(item)
        elif isinstance(item, list):
            result[key_text] = [redact_mapping(entry) if isinstance(entry, Mapping) else entry for entry in item]
        elif isinstance(item, str) and re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", item):
            result[key_text] = _redact_url(item)
        else:
            result[key_text] = item
    return result


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

