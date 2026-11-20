"""API key format and hashing.

Format: dmx_live_<prefix 8>_<secret 40>. The prefix is public (shown in the UI, used for
lookup); the full key is hashed with HMAC-SHA256 keyed by DMX_API_KEY_PEPPER.
"""

from __future__ import annotations

import hashlib
import hmac

from app.core.config import get_settings
from app.security.tokens import random_lower_alnum

# Scopes are deliberately fine-grained so future machine identities (including AI agents)
# never need blanket access.
SCOPES: dict[str, str] = {
    "projects:read": "List and read projects",
    "projects:write": "Create, update and archive projects",
    "projects:delete": "Delete projects",
    "databases:read": "List and read databases, status and events",
    "databases:write": "Create, rename, stop and start databases",
    "databases:delete": "Delete databases",
    "credentials:read": "Read connection details including the password",
    "credentials:rotate": "Rotate database credentials",
    "backups:read": "List backups and their status",
    "backups:write": "Take backups and restore them into new databases",
    "usage:read": "Read usage, quotas and billing estimates",
    "activity:read": "Read the activity log",
}


def generate_api_key() -> tuple[str, str]:
    prefix = random_lower_alnum(8)
    secret = random_lower_alnum(40)
    return f"dmx_live_{prefix}_{secret}", prefix


def parse_prefix(key: str) -> str | None:
    parts = key.split("_")
    if len(parts) != 4 or parts[0] != "dmx" or parts[1] != "live" or len(parts[2]) != 8 or len(parts[3]) != 40:
        return None
    return parts[2]


def hash_api_key(key: str) -> bytes:
    pepper = get_settings().api_key_pepper.get_secret_value().encode()
    return hmac.new(pepper, key.encode(), hashlib.sha256).digest()
