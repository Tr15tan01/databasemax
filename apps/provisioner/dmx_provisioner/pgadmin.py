"""Administrative SQL against customer instances, as dmx_admin over loopback (each instance
listens on its own port; dmx_admin may only log in from 127.0.0.1).

All identifiers are validated by regex first and then quoted with psycopg.sql.Identifier.
Passwords are never handled here: only SCRAM verifiers, embedded with sql.Literal.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import secrets
from collections.abc import Iterator
from typing import Any

import psycopg
from psycopg import sql

from dmx_provisioner.config import get_settings
from dmx_provisioner.scram import admin_password
from dmx_provisioner.validation import db_name as check_db
from dmx_provisioner.validation import role_name as check_role
from dmx_provisioner.validation import scram as check_scram


def _connect(iid: str, host: str, dbname: str = "postgres") -> psycopg.Connection[Any]:
    from dmx_provisioner.instances import admin_port

    s = get_settings()
    return psycopg.connect(
        host=host,
        port=admin_port(iid),
        sslmode="disable",
        user="dmx_admin",
        dbname=dbname,
        password=admin_password(s.admin_secret.get_secret_value(), iid),
        connect_timeout=5,
        autocommit=True,
        application_name="dmx-provisioner",
        options="-c statement_timeout=60000",
    )


def ping(iid: str, host: str) -> None:
    with _connect(iid, host) as conn:
        conn.execute("SELECT 1")


def configure(
    iid: str,
    host: str,
    *,
    database: str,
    username: str,
    scram_verifier: str,
    connection_limit: int,
    tablespace: str | None = None,
) -> None:
    """Idempotently create the customer role and database with least privilege. With a
    hard-limited instance the database lives in its own tablespace (its own filesystem)."""
    check_db(database)
    check_role(username)
    check_scram(scram_verifier)
    if not 1 <= connection_limit <= 500:
        raise ValueError("connection limit out of range")
    with _connect(iid, host) as conn:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (username,)).fetchone()
        verb = "ALTER" if exists else "CREATE"
        conn.execute(
            sql.SQL(
                "{} ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS "
                "INHERIT CONNECTION LIMIT {} PASSWORD {}"
            ).format(
                sql.SQL(verb),
                sql.Identifier(username),
                sql.Literal(connection_limit),
                sql.Literal(scram_verifier),
            )
        )
        if tablespace is not None:
            if tablespace != "dmx_data":
                raise ValueError("unexpected tablespace")
            if not conn.execute("SELECT 1 FROM pg_tablespace WHERE spcname = %s", (tablespace,)).fetchone():
                from dmx_provisioner.instances import tablespace_location

                conn.execute(
                    sql.SQL("CREATE TABLESPACE {} OWNER dmx_admin LOCATION {}").format(
                        sql.Identifier(tablespace), sql.Literal(tablespace_location(iid))
                    )
                )
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,)).fetchone():
            conn.execute(
                sql.SQL("CREATE DATABASE {} OWNER {} ENCODING 'UTF8' TEMPLATE template0{}").format(
                    sql.Identifier(database),
                    sql.Identifier(username),
                    sql.SQL(" TABLESPACE {}").format(sql.Identifier(tablespace)) if tablespace else sql.SQL(""),
                )
            )
        conn.execute("REVOKE ALL ON DATABASE postgres FROM PUBLIC")
        conn.execute("REVOKE ALL ON DATABASE template1 FROM PUBLIC")
        conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database)))
        conn.execute(
            sql.SQL("GRANT ALL ON DATABASE {} TO {}").format(sql.Identifier(database), sql.Identifier(username))
        )
    with _connect(iid, host, database) as conn:
        conn.execute(sql.SQL("ALTER SCHEMA public OWNER TO {}").format(sql.Identifier(username)))
        conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")


def rotate(iid: str, host: str, *, username: str, scram_verifier: str, terminate_sessions: bool = True) -> int:
    check_role(username)
    check_scram(scram_verifier)
    with _connect(iid, host) as conn:
        conn.execute(
            sql.SQL("ALTER ROLE {} WITH PASSWORD {}").format(sql.Identifier(username), sql.Literal(scram_verifier))
        )
        if not terminate_sessions:
            return 0
        rows = conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = %s AND pid <> pg_backend_pid()",
            (username,),
        ).fetchall()
        return len(rows)


def set_connection_limit(iid: str, host: str, *, username: str, connection_limit: int) -> None:
    check_role(username)
    with _connect(iid, host) as conn:
        conn.execute(
            sql.SQL("ALTER ROLE {} CONNECTION LIMIT {}").format(sql.Identifier(username), sql.Literal(connection_limit))
        )


def set_read_only(iid: str, host: str, *, database: str, read_only: bool) -> None:
    check_db(database)
    with _connect(iid, host) as conn:
        conn.execute(
            sql.SQL("ALTER DATABASE {} SET default_transaction_read_only = {}").format(
                sql.Identifier(database), sql.SQL("on" if read_only else "off")
            )
        )


def stats(iid: str, host: str, *, database: str, username: str) -> dict[str, Any]:
    check_db(database)
    check_role(username)
    with _connect(iid, host) as conn:
        size = conn.execute("SELECT pg_database_size(%s)", (database,)).fetchone()
        conns = conn.execute(
            "SELECT count(*), max(state_change) FROM pg_stat_activity WHERE usename = %s", (username,)
        ).fetchone()
        up = conn.execute("SELECT extract(epoch FROM now() - pg_postmaster_start_time())::bigint").fetchone()
        xact = conn.execute(
            "SELECT xact_commit + xact_rollback, tup_inserted + tup_updated + tup_deleted, "
            "tup_returned + tup_fetched FROM pg_stat_database WHERE datname = %s",
            (database,),
        ).fetchone()
        ro = conn.execute(
            "SELECT coalesce((SELECT setting FROM pg_db_role_setting s JOIN pg_database d ON d.oid = s.setdatabase "
            "CROSS JOIN LATERAL unnest(s.setconfig) setting WHERE d.datname = %s AND setting LIKE "
            "'default_transaction_read_only=%%' LIMIT 1), '')",
            (database,),
        ).fetchone()
        version = conn.execute("SHOW server_version").fetchone()
    return {
        "database_size_bytes": int(size[0]) if size and size[0] is not None else None,
        "active_connections": int(conns[0]) if conns else 0,
        "last_activity_at": conns[1].isoformat() if conns and conns[1] else None,
        "uptime_seconds": int(up[0]) if up else None,
        "transactions_total": int(xact[0]) if xact and xact[0] is not None else None,
        "rows_written_total": int(xact[1]) if xact and xact[1] is not None else None,
        "rows_read_total": int(xact[2]) if xact and xact[2] is not None else None,
        "read_only": bool(ro and ro[0] == "default_transaction_read_only=on"),
        "server_version": version[0] if version else None,
    }


LIVE_DB_SQL = (
    "SELECT xact_commit, xact_rollback, tup_returned + tup_fetched, tup_inserted + tup_updated + tup_deleted, "
    "blks_hit, blks_read, deadlocks, temp_bytes FROM pg_stat_database WHERE datname = %s"
)
# Counts only: query text and client addresses of the customer's sessions are never read here.
LIVE_ACTIVITY_SQL = (
    "SELECT count(*) FILTER (WHERE state = 'active'), count(*) FILTER (WHERE state = 'idle'), "
    "count(*) FILTER (WHERE state LIKE 'idle in transaction%%'), "
    "count(*) FILTER (WHERE wait_event_type = 'Lock'), "
    "coalesce(extract(epoch FROM max(now() - query_start) FILTER (WHERE state = 'active')), 0), "
    "coalesce(extract(epoch FROM max(now() - xact_start)), 0) "
    "FROM pg_stat_activity WHERE usename = %s AND datname = %s"
)


def live(iid: str, host: str, *, database: str, username: str) -> dict[str, Any]:
    """Cumulative counters and current session counts for the live monitor (rates are derived by
    the caller from two samples)."""
    check_db(database)
    check_role(username)
    with _connect(iid, host) as conn:
        d = conn.execute(LIVE_DB_SQL, (database,)).fetchone()
        a = conn.execute(LIVE_ACTIVITY_SQL, (username, database)).fetchone()
    out: dict[str, Any] = {}
    if d:
        keys = ("xact_commit", "xact_rollback", "rows_read", "rows_written", "blks_hit", "blks_read", "deadlocks")
        out.update({k: int(v or 0) for k, v in zip(keys, d[:7], strict=True)})
        out["temp_bytes"] = int(d[7] or 0)
    if a:
        out.update(
            sessions_active=int(a[0]),
            sessions_idle=int(a[1]),
            sessions_idle_in_transaction=int(a[2]),
            sessions_waiting_on_locks=int(a[3]),
            longest_query_seconds=round(float(a[4]), 1),
            oldest_transaction_seconds=round(float(a[5]), 1),
        )
    return out


COUNT_TABLES_SQL = (
    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE c.relkind IN ('r', 'p') AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
    "AND n.nspname NOT LIKE 'pg_toast%%' AND n.nspname NOT LIKE 'pg_temp%%'"
)


def count_tables(iid: str, host: str, *, database: str) -> int:
    """User tables (including partitioned parents) in the database."""
    check_db(database)
    with _connect(iid, host, database) as conn:
        row = conn.execute(COUNT_TABLES_SQL).fetchone()
    return int(row[0]) if row else 0


RESTORE_GROUP = "dmx_restorers"


def create_restore_role(iid: str, host: str, *, database: str, owner_role: str, scram_verifier: str) -> str:
    """A throwaway LOGIN role that is a member of the customer role and nothing else.

    pg_restore connects as this role (never as the superuser), so any customer-defined code that
    runs during the restore — CHECK constraints, defaults, domain checks — runs without superuser
    rights even if it does RESET ROLE. It may only log in over loopback (pg_hba), and the gateway
    refuses every dmx_* name.
    """
    check_db(database)
    check_role(owner_role)
    check_scram(scram_verifier)
    name = "dmx_r_" + secrets.token_hex(6)
    with _connect(iid, host) as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (RESTORE_GROUP,)).fetchone():
            conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(RESTORE_GROUP)))
        conn.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT "
                "CONNECTION LIMIT 2 VALID UNTIL {} PASSWORD {} IN ROLE {}, {}"
            ).format(
                sql.Identifier(name),
                sql.Literal((dt.datetime.now(dt.UTC) + dt.timedelta(hours=6)).isoformat()),
                sql.Literal(scram_verifier),
                sql.Identifier(RESTORE_GROUP),
                sql.Identifier(owner_role),
            )
        )
    return name


def drop_restore_role(iid: str, host: str, *, database: str, owner_role: str, name: str) -> None:
    check_db(database)
    check_role(owner_role)
    if not name.startswith("dmx_r_"):
        raise ValueError("not a restore role")
    with _connect(iid, host) as conn:
        conn.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = %s", (name,))
    with _connect(iid, host, database) as conn:
        conn.execute(sql.SQL("REASSIGN OWNED BY {} TO {}").format(sql.Identifier(name), sql.Identifier(owner_role)))
        conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(name)))
    with _connect(iid, host) as conn:
        conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(name)))


def database_size(iid: str, host: str, *, database: str) -> int:
    check_db(database)
    with _connect(iid, host) as conn:
        row = conn.execute("SELECT pg_database_size(%s)", (database,)).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


@contextlib.contextmanager
def snapshot(iid: str, host: str, *, database: str) -> Iterator[tuple[str, int]]:
    """Export a REPEATABLE READ snapshot (kept open for the duration) and count tables in it."""
    check_db(database)
    with _connect(iid, host, database) as conn:
        conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        try:
            snap = conn.execute("SELECT pg_export_snapshot()").fetchone()
            row = conn.execute(COUNT_TABLES_SQL).fetchone()
            assert snap is not None
            yield str(snap[0]), int(row[0]) if row else 0
        finally:
            conn.execute("ROLLBACK")


def terminate_sessions(iid: str, host: str, *, username: str) -> int:
    check_role(username)
    with _connect(iid, host) as conn:
        rows = conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = %s AND pid <> pg_backend_pid()",
            (username,),
        ).fetchall()
    return len(rows)
