"""SQL editor, schema browser, insights and live monitor.

The engine runs against a scratch database on the test PostgreSQL as an ordinary (non-superuser)
role, standing in for a customer database; the API tests point the editor at it instead of the
gateway. The gateway path itself is covered by the integration tests.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid

import psycopg
import pytest
from sqlalchemy import select, update

from app.api.v1 import console
from app.core.db import system_session
from app.core.redis import get_redis
from app.models import AuditEvent, Database, ProvisioningJob, Session, UsageRecord, utcnow
from app.sqlconsole import engine, lexer
from tests.conftest import PG_ADMIN_URL

TARGET_DB = "dmx_console_target"
ROLE = "console_owner"
ROLE_PW = "console-owner-pw-42"


@pytest.fixture(scope="module")
def target() -> dict:
    with psycopg.connect(PG_ADMIN_URL, autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {TARGET_DB} WITH (FORCE)")
        conn.execute(f"DROP ROLE IF EXISTS {ROLE}")
        conn.execute(f"CREATE ROLE {ROLE} LOGIN PASSWORD '{ROLE_PW}' NOSUPERUSER NOCREATEDB NOCREATEROLE")
        conn.execute(f"CREATE DATABASE {TARGET_DB} OWNER {ROLE}")
    base = PG_ADMIN_URL.rsplit("/", 1)[0].split("://", 1)[1].rsplit("@", 1)[-1]
    host, _, port = base.partition(":")
    return {
        "host": host,
        "port": int(port or 5432),
        "user": ROLE,
        "password": ROLE_PW,
        "dbname": TARGET_DB,
        "sslmode": "disable",
        "connect_timeout": 5,
    }


# --- lexer ----------------------------------------------------------------------------------------


def test_lexer_ignores_semicolons_and_keywords_in_literals_and_comments():
    sql = """
    select ';drop table x' as a, $f$ delete from y; $f$ as b; -- drop table z;
    /* nested /* drop */ comment */ update t set a = 1 where id = 2;
    delete from "weird;name";
    with gone as (delete from q returning *) select * from gone;
    """
    statements = lexer.split(sql)
    assert [s.keyword for s in statements] == ["SELECT", "UPDATE", "DELETE", "WITH"]
    assert lexer.destructive_kinds(statements) == ["UPDATE", "DELETE without WHERE", "DELETE"]


def test_lexer_escape_strings_and_single_statement():
    assert lexer.single_statement("select E'it\\'s; fine';") == "select E'it\\'s; fine'"
    assert lexer.single_statement("select 1; select 2") is None
    assert lexer.single_statement("-- only a comment") is None
    assert lexer.destructive_kinds(lexer.split("select 1; drop table t; truncate u")) == ["DROP", "TRUNCATE"]
    assert lexer.destructive_kinds(lexer.split("insert into t values (1)")) == []


# --- engine ---------------------------------------------------------------------------------------


def test_engine_multiple_statements_types_and_command_tags(target):
    out = engine.run(
        target,
        "create temp table t (id int, note text, at timestamptz);"
        "insert into t values (1, 'a', now()), (2, null, now());"
        "select id, note, at from t order by id",
        read_only=False,
    )
    assert out.error is None
    kinds = [(r.kind, r.command) for r in out.results]
    assert kinds == [("command", "CREATE TABLE"), ("command", "INSERT 0 2"), ("rows", "SELECT 2")]
    rows = out.results[2]
    assert [c.type for c in rows.columns] == ["int4", "text", "timestamptz"]
    assert rows.rows[0][:2] == ["1", "a"] and rows.rows[1][1] is None


def test_engine_read_only_mode_blocks_writes_even_when_switching_back(target):
    out = engine.run(target, "create table nope (x int)", read_only=True)
    assert out.error and out.error.sqlstate == "25006"
    out = engine.run(target, "set transaction read write; create table nope (x int)", read_only=True)
    assert out.error and out.error.sqlstate == "25001"
    # After an explicit COMMIT later statements still default to read-only.
    out = engine.run(target, "commit; create table nope (x int)", read_only=True)
    assert out.error and out.error.sqlstate == "25006"
    with psycopg.connect(**target) as conn:
        assert conn.execute("select to_regclass('nope')").fetchone() == (None,)


def test_engine_keeps_bounded_rows_and_bytes(target):
    limits = engine.Limits(max_rows=100, max_cell_chars=50)
    out = engine.run(
        target, "select g, repeat('x', 200) from generate_series(1, 5000) g", read_only=True, limits=limits
    )
    rs = out.results[0]
    assert rs.row_count == 5000 and len(rs.rows) == 100 and rs.truncated and out.truncated
    assert len(rs.rows[0][1]) == 51  # 50 characters plus the ellipsis
    out = engine.run(
        target,
        "select repeat('y', 1000) from generate_series(1, 100)",
        read_only=True,
        limits=engine.Limits(max_bytes=5000),
    )
    assert len(out.results[0].rows) == 5 and out.truncated


def test_engine_timeout_cannot_be_disabled_by_the_query(target):
    limits = engine.Limits(timeout_seconds=1)
    out = engine.run(target, "set statement_timeout = 0; select pg_sleep(20)", read_only=False, limits=limits)
    assert out.timed_out and out.error and out.error.sqlstate == "57014"
    assert out.duration_ms < 8000


def test_engine_errors_positions_notices_and_open_transactions(target):
    out = engine.run(target, "select 1;\nselec 2", read_only=True)
    assert out.error and out.error.sqlstate == "42601" and out.error.position == 11
    out = engine.run(target, "do $$ begin raise notice 'hello %', 42; end $$", read_only=False)
    assert out.notices == ["hello 42"]
    out = engine.run(target, "begin; create table left_open (x int)", read_only=False)
    assert out.error is None and out.rolled_back_open_transaction
    with psycopg.connect(**target) as conn:
        assert conn.execute("select to_regclass('left_open')").fetchone() == (None,)
    out = engine.run(target, "copy (select 1) to stdout", read_only=True)
    assert out.error and "COPY" in out.error.message


def test_engine_connect_failure_hides_internal_addresses(target):
    with pytest.raises(engine.ConnectFailed) as e:
        engine.run({**target, "password": "wrong"}, "select 1", read_only=True)
    assert "127.0.0.1" not in str(e.value) and "password authentication failed" in str(e.value)


# --- API ------------------------------------------------------------------------------------------


@pytest.fixture
def ready_db(user, project, target, monkeypatch) -> dict:
    r = user.post("/databases", json={"project_id": project["id"], "name": "console"})
    assert r.status_code == 202, r.text
    d = r.json()
    with system_session() as db:
        db.execute(
            update(ProvisioningJob).where(ProvisioningJob.database_id == uuid.UUID(d["id"])).values(status="cancelled")
        )
        db.execute(update(Database).where(Database.id == uuid.UUID(d["id"])).values(status="ready"))
    monkeypatch.setattr(console, "_params", lambda db, database: dict(target))
    return d


def _audits(action: str) -> list[AuditEvent]:
    with system_session() as db:
        return list(db.scalars(select(AuditEvent).where(AuditEvent.action == action)))


def test_sql_read_mode_runs_and_audits_without_sql_text(user, ready_db):
    secret = "select 'top-secret-value' as v"
    r = user.post(f"/databases/{ready_db['id']}/sql", json={"sql": secret})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "read" and body["error"] is None
    assert body["results"][0]["rows"] == [["top-secret-value"]]
    events = _audits("database.sql_executed")
    assert len(events) == 1 and events[0].metadata_["statement_types"] == ["SELECT"]
    assert "top-secret" not in json.dumps(events[0].metadata_)


def test_sql_write_mode_requires_confirmation_for_destructive_statements(user, ready_db):
    url = f"/databases/{ready_db['id']}/sql"
    assert user.post(url, json={"sql": "create table things (id int)", "mode": "write"}).json()["error"] is None
    r = user.post(url, json={"sql": "delete from things", "mode": "write"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "confirmation_required"
    assert r.json()["error"]["details"]["kinds"] == ["DELETE without WHERE"]
    r = user.post(
        url, json={"sql": "delete from things; drop table things", "mode": "write", "confirm_destructive": True}
    )
    assert r.status_code == 200 and r.json()["error"] is None


def test_sql_requires_dashboard_session_and_recent_password(user, ready_db):
    url = f"/databases/{ready_db['id']}/sql"
    key = user.post("/api-keys", json={"name": "k", "scopes": ["databases:read", "databases:write"]}).json()["key"]
    r = user.c.post(f"/api/v1{url}", json={"sql": "select 1"}, headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "session_required"
    with system_session() as db:
        db.execute(
            update(Session)
            .where(Session.user_id == uuid.UUID(user.user["id"]))
            .values(created_at=utcnow() - dt.timedelta(hours=2), reauthenticated_at=None)
        )
    r = user.post(url, json={"sql": "select 1"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "reauthentication_required"
    assert user.post("/auth/reauthenticate", json={"password": user.password}).status_code == 200
    assert user.post(url, json={"sql": "select 1"}).status_code == 200


def test_sql_other_tenants_database_is_not_found(make_user, ready_db):
    other = make_user()
    r = other.post(f"/databases/{ready_db['id']}/sql", json={"sql": "select 1"})
    assert r.status_code == 404


def test_sql_requires_running_database(user, project):
    d = user.post("/databases", json={"project_id": project["id"], "name": "notyet"}).json()
    r = user.post(f"/databases/{d['id']}/sql", json={"sql": "select 1"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "database_not_ready"


def test_sql_one_query_at_a_time_per_user(user, ready_db):
    import time

    get_redis().zadd(f"dmx:slots:sql:user:{user.user['id']}", {"other-request": time.time() + 60})
    r = user.post(f"/databases/{ready_db['id']}/sql", json={"sql": "select 1"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "query_running"


def test_explain_plan_and_analyze_never_write(user, ready_db):
    url = f"/databases/{ready_db['id']}/sql"
    user.post(url, json={"sql": "create table ex (id int primary key)", "mode": "write"})
    r = user.post(url, json={"sql": "select * from ex where id = 1", "explain": "plan"})
    assert r.status_code == 200 and r.json()["plan"][0]["Plan"]["Node Type"]
    r = user.post(url, json={"sql": "insert into ex values (1)", "explain": "analyze", "mode": "write"})
    assert r.json()["error"]["sqlstate"] == "25006"  # analyze runs read-only, so the insert is refused
    r = user.post(url, json={"sql": "select 1; select 2", "explain": "plan"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "explain_single_statement"


def test_schema_browser_lists_tables_and_columns(user, ready_db):
    url = f"/databases/{ready_db['id']}/sql"
    user.post(
        url, json={"sql": "create table people (id bigint primary key, name text not null, bio text)", "mode": "write"}
    )
    r = user.get(f"/databases/{ready_db['id']}/schema")
    assert r.status_code == 200, r.text
    people = next(t for t in r.json()["tables"] if t["name"] == "people")
    assert people["schema_name"] == "public" and people["kind"] == "table"
    assert people["columns"] == [
        {"name": "id", "type": "bigint", "nullable": False, "primary_key": True},
        {"name": "name", "type": "text", "nullable": False, "primary_key": False},
        {"name": "bio", "type": "text", "nullable": True, "primary_key": False},
    ]


def test_insights_report_schema_problems_and_score(user, ready_db):
    url = f"/databases/{ready_db['id']}/sql"
    user.post(
        url,
        json={
            "sql": "create table parent (id int primary key); create table child (parent_id int references parent);"
            "create table logs (line text); create sequence almost_full as integer;"
            "select setval('almost_full', 2100000000)",
            "mode": "write",
        },
    )
    r = user.get(f"/databases/{ready_db['id']}/insights")
    assert r.status_code == 200, r.text
    body = r.json()
    ids = {f["id"] for f in body["findings"]}
    assert "no_primary_key" in ids
    assert any(i.startswith("unindexed_fk:public.") for i in ids)
    seq = next(f for f in body["findings"] if f["id"] == "sequence:public.almost_full")
    assert seq["severity"] == "critical" and seq["sql"] == 'ALTER SEQUENCE "public"."almost_full" AS bigint;'
    assert 0 <= body["score"] < 100 and body["grade"] in ("excellent", "good", "fair", "poor")


def test_storage_forecast_projects_growth():
    t0 = utcnow() - dt.timedelta(days=10)
    points = [(t0 + dt.timedelta(days=i), 100.0 * 1024**2 + i * 10 * 1024**2) for i in range(11)]
    f = console.storage_forecast(points, used=200 * 1024**2, limit_bytes=500 * 1024**2)
    assert f["growth_bytes_per_day"] == pytest.approx(10 * 1024**2, rel=0.01)
    assert f["days_until_full"] == pytest.approx(30, rel=0.02)
    assert console.storage_forecast(points[:3], used=1, limit_bytes=2)["days_until_full"] is None


class _FakeProv:
    def __init__(self):
        self.calls = 0

    def live(self, iid, database, username):
        self.calls += 1
        n = self.calls
        return {
            "state": "running",
            "measured": {
                "xact_commit": 1000 * n,
                "xact_rollback": 10 * n,
                "rows_read": 50_000 * n,
                "rows_written": 500 * n,
                "blks_hit": 990 * n,
                "blks_read": 10 * n,
                "deadlocks": 0,
                "temp_bytes": 0,
                "sessions_active": 2,
                "sessions_idle": 3,
                "sessions_idle_in_transaction": 0,
                "sessions_waiting_on_locks": 0,
                "longest_query_seconds": 0.4,
                "oldest_transaction_seconds": 0.4,
                "cpu_percent": 12.5,
                "memory_usage_bytes": 100 * 1024**2,
                "memory_limit_bytes": 256 * 1024**2,
            },
        }


def test_live_monitor_derives_rates_from_two_samples(user, ready_db, monkeypatch):
    from types import SimpleNamespace

    prov = _FakeProv()
    monkeypatch.setattr(console.ProvisionerPool, "for_host_id", lambda self, db, host_id: prov)
    monkeypatch.setattr(console.svc, "active_credential", lambda db, d: SimpleNamespace(username="u_test"))
    url = f"/databases/{ready_db['id']}/live"
    first = user.get(url).json()
    assert first["transactions_per_second"] is None and first["cpu_percent_of_limit"] == 50.0  # 12.5% of 0.25 CPU
    # Pretend the previous sample is 5 s old and the shared 2 s cache has expired.
    r = get_redis()
    prev_key = f"dmx:live:prev:{ready_db['id']}"
    prev = json.loads(r.get(prev_key))
    prev["ts"] -= 5
    r.set(prev_key, json.dumps(prev))
    r.delete(f"dmx:live:resp:{ready_db['id']}")
    second = user.get(url).json()
    assert second["transactions_per_second"] == pytest.approx(1010 / second["interval_seconds"], rel=0.01)
    assert second["cache_hit_ratio"] == pytest.approx(0.99)
    assert second["sessions_active"] == 2 and second["max_connections"] == ready_db["max_connections"]
    # A third request within 2 s is served from the shared sample (no provisioner call).
    calls = prov.calls
    user.get(url)
    assert prov.calls == calls


def test_usage_rates_from_cumulative_counters(user, ready_db):
    now = utcnow()
    values = [(30, 1000), (20, 1600), (10, 2200), (5, 100)]  # minutes ago, counter (last one: reset)
    with system_session() as db:
        d = db.get(Database, uuid.UUID(ready_db["id"]))
        for minutes, v in values:
            at = now - dt.timedelta(minutes=minutes)
            db.add(
                UsageRecord(
                    id=uuid.uuid4(),
                    user_id=d.owner_id,
                    project_id=d.project_id,
                    database_id=d.id,
                    metric="transactions_total",
                    quantity=v,
                    source="measured",
                    granularity="raw",
                    measured_at=at,
                    period=at.strftime("%Y-%m"),
                )
            )
    r = user.get(f"/databases/{ready_db['id']}/usage", params={"metric": "transactions_per_minute", "days": 1})
    body = r.json()
    assert body["unit"] == "per_minute" and body["source"] == "derived"
    assert [p["v"] for p in body["points"]] == [60.0, 60.0]  # the reset interval is skipped
