"""Executes SQL for the dashboard's SQL editor.

Security model:
- The editor connects through the public gateway path with the customer's own credential, so
  it can do exactly what the customer's own clients can do — nothing more. It never uses the
  per-instance admin role.
- Results are streamed from libpq (single-row/chunked mode) and only the first rows/bytes are
  kept, so a large result can't exhaust the API's memory. The gateway additionally caps the size
  of any single protocol message on editor connections (APPLICATION_NAME below), which bounds the
  memory libpq needs for one row.
- A client-side deadline backs up `statement_timeout` (which the user's SQL could change): when
  it passes, the query is cancelled and, failing that, the connection is closed.
- "Read-only" mode is a guard against accidents, not a security boundary (the role may write
  anyway): the SQL runs inside `BEGIN READ ONLY` after a snapshot has been taken (so it can't be
  switched back to read-write), with `default_transaction_read_only=on` for anything after a
  COMMIT, and is always rolled back.
"""

from __future__ import annotations

import select
import time
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg import pq

# The gateway recognises this name and caps protocol message sizes for the connection.
APPLICATION_NAME = "dmx-sql-editor"

ES = pq.ExecStatus
_ROW_STATUSES = (ES.SINGLE_TUPLE, ES.TUPLES_CHUNK)


@dataclass(frozen=True)
class Limits:
    timeout_seconds: float = 15.0
    max_rows: int = 1000  # rows kept per result set
    max_bytes: int = 3 * 1024 * 1024  # text kept across all result sets
    max_cell_chars: int | None = 10_000
    max_result_sets: int = 20
    max_columns: int = 300
    max_notices: int = 20


class ConnectFailed(Exception):
    """The database couldn't be reached; the message is safe to show to its owner."""


@dataclass
class Column:
    name: str
    type_oid: int
    type: str = ""


@dataclass
class ResultSet:
    kind: str  # "rows" | "command"
    command: str = ""
    columns: list[Column] = field(default_factory=list)
    rows: list[list[str | None]] = field(default_factory=list)
    row_count: int = 0  # rows the statement produced, including ones not kept
    truncated: bool = False


@dataclass
class SqlError:
    message: str
    sqlstate: str | None = None
    position: int | None = None  # 1-based character offset into the submitted SQL
    detail: str | None = None
    hint: str | None = None


@dataclass
class Outcome:
    results: list[ResultSet] = field(default_factory=list)
    error: SqlError | None = None
    notices: list[str] = field(default_factory=list)
    duration_ms: int = 0
    timed_out: bool = False
    truncated: bool = False
    rolled_back_open_transaction: bool = False
    omitted_result_sets: int = 0


class _Timeout:
    pass


_TIMEOUT = _Timeout()


def _connect_error_message(e: psycopg.OperationalError) -> str:
    # libpq messages start with 'connection to server at "<internal address>" ... failed:'; keep
    # only the server's (or gateway's) own explanation, which is written for customers.
    text = str(e)
    for marker in ("FATAL:", "ERROR:"):
        if marker in text:
            return text.rsplit(marker, 1)[1].strip().splitlines()[0]
    return "Couldn't connect to the database. Check that it's running and try again."


def connect(params: dict[str, Any], *, read_only: bool, limits: Limits) -> psycopg.Connection[Any]:
    options = [
        f"-c statement_timeout={int(limits.timeout_seconds * 1000)}",
        "-c lock_timeout=5000",
        "-c idle_in_transaction_session_timeout=30000",
    ]
    if read_only:
        options.append("-c default_transaction_read_only=on")
    try:
        return psycopg.connect(
            **params,
            options=" ".join(options),
            application_name=APPLICATION_NAME,
            client_encoding="UTF8",
            autocommit=True,
        )
    except psycopg.OperationalError as e:
        raise ConnectFailed(_connect_error_message(e)) from e


def _diag(res: pq.abc.PGresult) -> SqlError:
    def f(field_: pq.DiagnosticField) -> str | None:
        v = res.error_field(field_)
        return v.decode("utf-8", "replace") if v else None

    pos = f(pq.DiagnosticField.STATEMENT_POSITION)
    msg = f(pq.DiagnosticField.MESSAGE_PRIMARY) or (res.error_message or b"").decode("utf-8", "replace").strip()
    return SqlError(
        message=msg or "The query failed.",
        sqlstate=f(pq.DiagnosticField.SQLSTATE),
        position=int(pos) if pos and pos.isdigit() else None,
        detail=f(pq.DiagnosticField.MESSAGE_DETAIL),
        hint=f(pq.DiagnosticField.MESSAGE_HINT),
    )


def _wait(pg: pq.abc.PGconn, deadline: float, write: bool = False) -> bool:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False
    fd = pg.socket
    select.select([] if write else [fd], [fd] if write else [], [], min(remaining, 1.0))
    return True


def _next_result(pg: pq.abc.PGconn, deadline: float) -> pq.abc.PGresult | _Timeout | None:
    while pg.is_busy():
        if not _wait(pg, deadline):
            return _TIMEOUT
        pg.consume_input()
    return pg.get_result()


def _send(pg: pq.abc.PGconn, sql: str, deadline: float) -> None:
    pg.send_query(sql.encode("utf-8"))
    while pg.flush() == 1:
        if not _wait(pg, deadline, write=True):
            raise TimeoutError
    try:
        pg.set_chunked_rows_mode(100)  # libpq >= 17
    except psycopg.NotSupportedError:
        pg.set_single_row_mode()


class _Collector:
    def __init__(self, limits: Limits, outcome: Outcome):
        self.limits = limits
        self.out = outcome
        self.kept_bytes = 0
        self.current: ResultSet | None = None

    def _start(self, res: pq.abc.PGresult) -> ResultSet:
        n = min(res.nfields, self.limits.max_columns)
        cols = [
            Column(name=(res.fname(i) or b"?column?").decode("utf-8", "replace"), type_oid=res.ftype(i))
            for i in range(n)
        ]
        return ResultSet(kind="rows", columns=cols)

    def rows(self, res: pq.abc.PGresult) -> None:
        if self.current is None:
            self.current = self._start(res)
        rs = self.current
        if res.command_status:  # chunked mode reports "SELECT n" on the last chunk
            rs.command = res.command_status.decode()
        ncols = len(rs.columns)
        cap = self.limits.max_cell_chars
        for r in range(res.ntuples):
            rs.row_count += 1
            if len(rs.rows) >= self.limits.max_rows or self.kept_bytes >= self.limits.max_bytes:
                rs.truncated = True
                continue
            row: list[str | None] = []
            for c in range(ncols):
                v = res.get_value(r, c)
                if v is None:
                    row.append(None)
                    continue
                s = v.decode("utf-8", "replace")
                if cap is not None and len(s) > cap:
                    s = s[:cap] + "…"
                    rs.truncated = True
                self.kept_bytes += len(s)
                row.append(s)
            rs.rows.append(row)

    def finish_rows(self, res: pq.abc.PGresult) -> None:
        rs = self.current or self._start(res)
        self.current = None
        if res.command_status:
            rs.command = res.command_status.decode()
        self._add(rs)

    def command(self, res: pq.abc.PGresult) -> None:
        self._add(ResultSet(kind="command", command=(res.command_status or b"").decode()))

    def _add(self, rs: ResultSet) -> None:
        if rs.truncated:
            self.out.truncated = True
        if len(self.out.results) >= self.limits.max_result_sets:
            self.out.omitted_result_sets += 1
            return
        self.out.results.append(rs)


def _type_names(conn: psycopg.Connection[Any], outcome: Outcome) -> None:
    cols = [c for rs in outcome.results for c in rs.columns]
    unknown: set[int] = set()
    for c in cols:
        info = conn.adapters.types.get(c.type_oid)
        if info is not None:
            c.type = info.name
        else:
            unknown.add(c.type_oid)
    if unknown and conn.pgconn.transaction_status == pq.TransactionStatus.IDLE:
        try:
            names = dict(
                conn.execute(
                    "SELECT oid::int, format_type(oid, NULL) FROM pg_type WHERE oid = ANY(%s)", (list(unknown),)
                ).fetchall()
            )
        except psycopg.Error:
            names = {}
        for c in cols:
            if not c.type:
                c.type = names.get(c.type_oid, "unknown")


def run(params: dict[str, Any], sql: str, *, read_only: bool, limits: Limits | None = None) -> Outcome:
    """Run `sql` (one or more statements) and collect bounded results. Raises ConnectFailed."""
    limits = limits or Limits()
    out = Outcome()
    started = time.monotonic()
    conn = connect(params, read_only=read_only, limits=limits)

    def on_notice(d: psycopg.errors.Diagnostic) -> None:
        if len(out.notices) < limits.max_notices and d.message_primary:
            out.notices.append(d.message_primary[:1000])

    conn.add_notice_handler(on_notice)
    abandon = False
    try:
        if read_only:
            conn.execute("BEGIN READ ONLY")
            conn.execute("SELECT 1")  # takes the snapshot: SET TRANSACTION READ WRITE is refused after this
        pg = conn.pgconn
        deadline = time.monotonic() + limits.timeout_seconds + 2
        cancelled = False
        collect = _Collector(limits, out)
        try:
            _send(pg, sql, deadline)
        except TimeoutError:
            out.timed_out, abandon = True, True
        while not abandon:
            res = _next_result(pg, deadline)
            if isinstance(res, _Timeout):
                out.timed_out = True
                if cancelled:
                    abandon = True  # cancel didn't take effect: drop the connection
                    break
                cancelled = True
                try:
                    conn.cancel_safe(timeout=3)
                except psycopg.Error:
                    abandon = True
                    break
                deadline = time.monotonic() + 3
                continue
            if res is None:
                break
            st = res.status
            if st in _ROW_STATUSES:
                collect.rows(res)
            elif st == ES.TUPLES_OK:
                collect.finish_rows(res)
            elif st == ES.COMMAND_OK:
                collect.command(res)
            elif st == ES.EMPTY_QUERY:
                pass
            elif st == ES.COPY_IN:
                pg.put_copy_end(b"COPY FROM STDIN isn't supported in the SQL editor")
            elif st == ES.COPY_OUT:
                out.error = SqlError("COPY TO STDOUT isn't supported in the SQL editor. Use a SELECT instead.")
                abandon = True
            elif st == ES.FATAL_ERROR:
                collect.current = None  # a partial result set is dropped
                out.error = _diag(res)
                if out.timed_out and out.error.sqlstate == "57014":
                    out.error = SqlError(
                        f"The query was stopped after {limits.timeout_seconds:g} seconds.", sqlstate="57014"
                    )
            else:
                out.error = SqlError("The database returned an unexpected response.")
        if not abandon:
            status = pg.transaction_status
            if read_only:
                if status != pq.TransactionStatus.IDLE:
                    conn.execute("ROLLBACK")
            elif status in (pq.TransactionStatus.INTRANS, pq.TransactionStatus.INERROR):
                out.rolled_back_open_transaction = True
                conn.execute("ROLLBACK")
            _type_names(conn, out)
    except psycopg.OperationalError:
        # Connection lost mid-query (e.g. the gateway stopped an oversized result).
        abandon = True
        if out.error is None:
            out.error = SqlError(
                "The connection was closed while reading the result. A single row may be larger than the "
                "SQL editor allows (8 MB); select fewer or smaller columns."
            )
    finally:
        out.duration_ms = int((time.monotonic() - started) * 1000)
        conn.close()
    if out.timed_out and out.error is None:
        out.error = SqlError(f"The query was stopped after {limits.timeout_seconds:g} seconds.", sqlstate="57014")
    return out


def run_fixed(params: dict[str, Any], sql: str, *, limits: Limits | None = None) -> Outcome:
    """Run one of the platform's own read-only catalog queries (schema browser, insights)."""
    return run(params, sql, read_only=True, limits=limits or Limits(max_cell_chars=None, max_rows=5000))
