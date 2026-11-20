"""Post-provisioning verification: connect through the real public path (gateway + TLS) with
the customer's own credential, exactly as a customer would."""

from __future__ import annotations

import ssl
import time

import psycopg

from app.core.config import get_settings
from app.models import Database
from app.provisioning.connection import public_host


class ConnectivityError(Exception):
    pass


def gateway_params(database: Database, username: str, password: str) -> dict[str, object]:
    """libpq parameters for connecting to `database` through the gateway exactly as a customer
    would (TLS, SNI host, the customer's own role). Also used by the dashboard's SQL editor."""
    s = get_settings()
    params: dict[str, object] = {
        "host": public_host(database),
        "hostaddr": _resolve(s.gateway_internal_host),
        "port": s.gateway_internal_port,
        "user": username,
        "password": password,
        "dbname": database.pg_database_name,
        "connect_timeout": 5,
    }
    if s.gateway_ca_file:
        params.update(sslmode="verify-full", sslrootcert=s.gateway_ca_file)
    else:
        params.update(sslmode="require")
    return params


def verify(
    database: Database, username: str, password: str, *, attempts: int = 6, delay: float = 1.0
) -> dict[str, str]:
    params = {**gateway_params(database, username, password), "application_name": "dmx-healthcheck"}
    last: Exception | None = None
    for _ in range(attempts):
        try:
            with psycopg.connect(**params) as conn:  # type: ignore[arg-type]
                row = conn.execute("SELECT current_user, current_database(), version()").fetchone()
                if not conn.info.pgconn.ssl_in_use:
                    raise ConnectivityError("connection was not encrypted")
                assert row is not None
                if row[0] != username or row[1] != database.pg_database_name:
                    raise ConnectivityError("connected to an unexpected database")
                return {"user": row[0], "database": row[1], "version": row[2].split(" on ")[0]}
        except (psycopg.OperationalError, ssl.SSLError) as e:
            last = e
            time.sleep(delay)
    raise ConnectivityError(f"could not connect through the gateway ({type(last).__name__})")


def _resolve(host: str) -> str:
    import socket

    return socket.gethostbyname(host)
