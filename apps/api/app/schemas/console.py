from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import Field

from app.schemas.common import ApiModel, OutModel

# --- SQL editor ---------------------------------------------------------------------------------


class SqlRun(ApiModel):
    sql: str = Field(min_length=1, max_length=100_000)
    mode: Literal["read", "write"] = Field(
        "read", description="`read` runs inside a read-only transaction that is always rolled back"
    )
    confirm_destructive: bool = Field(
        False, description="Required in write mode for DROP, TRUNCATE, ALTER, DELETE and UPDATE statements"
    )
    explain: Literal["none", "plan", "analyze"] = Field(
        "none",
        description="`plan` returns the query plan without running the statement; `analyze` runs it (read-only) "
        "and returns actual timings",
    )


class SqlColumnOut(OutModel):
    name: str
    type: str


class SqlResultSetOut(OutModel):
    kind: Literal["rows", "command"]
    command: str
    columns: list[SqlColumnOut]
    rows: list[list[str | None]] = Field(description="Values in PostgreSQL's text format; null is SQL NULL")
    row_count: int = Field(description="Rows the statement produced, including rows not returned")
    truncated: bool


class SqlErrorOut(OutModel):
    message: str
    sqlstate: str | None = None
    position: int | None = Field(None, description="1-based character position in the submitted SQL")
    detail: str | None = None
    hint: str | None = None


class SqlLimitsOut(OutModel):
    timeout_seconds: float
    max_rows: int
    max_bytes: int


class SqlRunOut(OutModel):
    mode: Literal["read", "write"]
    statement_count: int
    results: list[SqlResultSetOut]
    error: SqlErrorOut | None
    notices: list[str]
    duration_ms: int
    timed_out: bool
    truncated: bool
    rolled_back_open_transaction: bool = Field(
        description="A transaction was left open (BEGIN without COMMIT) and has been rolled back"
    )
    omitted_result_sets: int
    plan: Any | None = Field(None, description="EXPLAIN (FORMAT JSON) output when `explain` was requested")
    limits: SqlLimitsOut


# --- schema browser -----------------------------------------------------------------------------


class SchemaColumnOut(OutModel):
    name: str
    type: str
    nullable: bool
    primary_key: bool


class SchemaTableOut(OutModel):
    schema_name: str
    name: str
    kind: Literal["table", "partitioned table", "view", "materialized view", "foreign table"]
    rows_estimate: int | None
    total_bytes: int | None
    columns: list[SchemaColumnOut]


class SchemaOut(OutModel):
    tables: list[SchemaTableOut]
    truncated: bool


# --- insights -----------------------------------------------------------------------------------


class InsightOut(OutModel):
    id: str
    severity: Literal["critical", "warning", "info", "ok"]
    category: Literal["performance", "storage", "reliability", "schema", "capacity"]
    title: str
    detail: str
    suggestion: str | None = None
    sql: str | None = Field(None, description="Suggested SQL to review and run yourself; never run automatically")


class StorageForecastOut(OutModel):
    used_bytes: int | None
    limit_bytes: int
    growth_bytes_per_day: float | None
    days_until_full: float | None
    based_on_days: float | None


class InsightsOut(OutModel):
    score: int = Field(ge=0, le=100)
    grade: Literal["excellent", "good", "fair", "poor"]
    generated_at: dt.datetime
    stats_since: dt.datetime | None
    cache_hit_ratio: float | None
    findings: list[InsightOut]
    forecast: StorageForecastOut


# --- live monitor -------------------------------------------------------------------------------


class LiveOut(OutModel):
    measured_at: dt.datetime
    state: str
    interval_seconds: float | None = Field(None, description="Seconds between the two samples the rates use")
    cpu_percent_of_limit: float | None
    memory_bytes: int | None
    memory_limit_bytes: int | None
    sessions_active: int | None
    sessions_idle: int | None
    sessions_idle_in_transaction: int | None
    sessions_waiting_on_locks: int | None
    max_connections: int
    gateway_connections: int | None
    longest_query_seconds: float | None
    oldest_transaction_seconds: float | None
    transactions_per_second: float | None
    rollbacks_per_second: float | None
    rows_read_per_second: float | None
    rows_written_per_second: float | None
    network_in_bytes_per_second: float | None
    network_out_bytes_per_second: float | None
    cache_hit_ratio: float | None = Field(None, description="Share of block reads served from memory, this interval")
    deadlocks_total: int | None
    temp_bytes_total: int | None
