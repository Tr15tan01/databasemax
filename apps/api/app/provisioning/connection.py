"""Customer-visible connection information. URLs are built with proper percent-encoding —
never by naive string concatenation of raw values."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, urlencode

from app.core.config import get_settings
from app.models import Database


@dataclass
class ConnectionInfo:
    host: str
    port: int
    database: str
    username: str
    sslmode: str = "require"


def public_host(db: Database) -> str:
    s = get_settings()
    if s.db_host_suffix:
        return f"{db.route_id}.{s.db_host_suffix}"
    return s.db_public_host


def connection_info(db: Database, username: str) -> ConnectionInfo:
    s = get_settings()
    return ConnectionInfo(host=public_host(db), port=s.db_public_port, database=db.pg_database_name, username=username)


def build_url(info: ConnectionInfo, password: str | None, *, scheme: str = "postgresql") -> str:
    userinfo = quote(info.username, safe="")
    if password is not None:
        userinfo += ":" + quote(password, safe="")
    host = info.host if ":" not in info.host else f"[{info.host}]"
    query = urlencode({"sslmode": info.sslmode})
    return f"{scheme}://{userinfo}@{host}:{info.port}/{quote(info.database, safe='')}?{query}"


MASK = "••••••••"


def masked_url(info: ConnectionInfo) -> str:
    return build_url(info, None).replace("@", ":" + MASK + "@", 1)
