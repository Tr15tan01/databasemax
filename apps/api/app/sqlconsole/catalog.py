"""Fixed, read-only catalog queries run as the customer's own role: the schema browser and the
health advisor ("insights"). They read metadata and statistics only — never table contents — and
suggested fixes are returned as text for the customer to review; nothing is run on their behalf.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
from typing import Any

from app.sqlconsole.engine import Outcome

_SYSTEM_SCHEMAS = (
    "n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg\\_toast%' "
    "AND n.nspname NOT LIKE 'pg\\_temp%'"
)

MAX_TABLES = 500

SCHEMA_SQL = f"""
SELECT n.nspname, c.relname,
       CASE c.relkind WHEN 'r' THEN 'table' WHEN 'p' THEN 'partitioned table' WHEN 'v' THEN 'view'
                      WHEN 'm' THEN 'materialized view' ELSE 'foreign table' END,
       CASE WHEN c.reltuples >= 0 THEN c.reltuples::bigint END,
       CASE WHEN c.relkind IN ('r', 'p', 'm') THEN pg_total_relation_size(c.oid) END,
       coalesce((SELECT json_agg(json_build_array(
                    a.attname, format_type(a.atttypid, a.atttypmod), NOT a.attnotnull,
                    EXISTS (SELECT 1 FROM pg_index i WHERE i.indrelid = c.oid AND i.indisprimary
                                                       AND a.attnum = ANY (i.indkey)))
                  ORDER BY a.attnum)
                 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped), '[]')
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') AND NOT c.relispartition AND {_SYSTEM_SCHEMAS}
ORDER BY n.nspname = 'public' DESC, n.nspname, c.relname
LIMIT {MAX_TABLES + 1}
"""


def parse_schema(out: Outcome) -> dict[str, Any]:
    rows = out.results[0].rows if out.results else []
    tables = []
    for r in rows[:MAX_TABLES]:
        cols = json.loads(r[5] or "[]")
        tables.append(
            {
                "schema_name": r[0],
                "name": r[1],
                "kind": r[2],
                "rows_estimate": int(r[3]) if r[3] is not None else None,
                "total_bytes": int(r[4]) if r[4] is not None else None,
                "columns": [{"name": c[0], "type": c[1], "nullable": c[2], "primary_key": c[3]} for c in cols],
            }
        )
    return {"tables": tables, "truncated": len(rows) > MAX_TABLES}


# One statement per check; results come back in this order.
INSIGHT_QUERIES: list[tuple[str, str]] = [
    (
        "db",
        "SELECT blks_hit, blks_read, xact_commit, xact_rollback, deadlocks, temp_bytes, stats_reset "
        "FROM pg_stat_database WHERE datname = current_database()",
    ),
    (
        "seq_scans",
        "SELECT schemaname, relname, seq_scan, seq_tup_read, coalesce(idx_scan, 0), n_live_tup "
        "FROM pg_stat_user_tables WHERE n_live_tup >= 10000 AND seq_scan >= 50 "
        "AND seq_tup_read / greatest(seq_scan, 1) >= 5000 AND coalesce(idx_scan, 0) < seq_scan "
        "ORDER BY seq_tup_read DESC LIMIT 5",
    ),
    (
        "unused_indexes",
        "SELECT s.schemaname, s.relname, s.indexrelname, pg_relation_size(s.indexrelid) "
        "FROM pg_stat_user_indexes s JOIN pg_index i ON i.indexrelid = s.indexrelid "
        "WHERE s.idx_scan = 0 AND NOT i.indisunique AND NOT i.indisprimary "
        "AND pg_relation_size(s.indexrelid) >= 1048576 ORDER BY 4 DESC LIMIT 5",
    ),
    (
        "dead_tuples",
        "SELECT schemaname, relname, n_dead_tup, n_live_tup FROM pg_stat_user_tables "
        "WHERE n_dead_tup >= 10000 AND n_dead_tup > 0.2 * greatest(n_live_tup, 1) "
        "ORDER BY n_dead_tup DESC LIMIT 5",
    ),
    (
        "no_primary_key",
        "SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        f"WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition AND {_SYSTEM_SCHEMAS} "
        "AND NOT EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conrelid = c.oid AND k.contype = 'p') "
        "ORDER BY 1, 2 LIMIT 11",
    ),
    (
        "unindexed_fks",
        "SELECT n.nspname, cl.relname, c.conname, "
        "(SELECT string_agg(quote_ident(a.attname), ', ' ORDER BY k.ord) "
        " FROM unnest(c.conkey) WITH ORDINALITY k(attnum, ord) "
        " JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum) "
        "FROM pg_constraint c JOIN pg_class cl ON cl.oid = c.conrelid "
        "JOIN pg_namespace n ON n.oid = cl.relnamespace "
        "WHERE c.contype = 'f' AND NOT EXISTS (SELECT 1 FROM pg_index i WHERE i.indrelid = c.conrelid "
        "AND (string_to_array(i.indkey::text, ' ')::int2[])[1:cardinality(c.conkey)] @> c.conkey "
        "AND (string_to_array(i.indkey::text, ' ')::int2[])[1:cardinality(c.conkey)] <@ c.conkey) "
        "ORDER BY 1, 2 LIMIT 5",
    ),
    (
        "sessions",
        "SELECT count(*) FILTER (WHERE state LIKE 'idle in transaction%' "
        "AND now() - state_change > interval '5 minutes'), "
        "coalesce(extract(epoch FROM max(now() - xact_start)), 0) "
        "FROM pg_stat_activity WHERE usename = current_user AND pid <> pg_backend_pid()",
    ),
    (
        "sequences",
        "SELECT schemaname, sequencename, last_value, max_value FROM pg_sequences "
        "WHERE last_value IS NOT NULL AND max_value > 0 AND last_value > 0.75 * max_value "
        "ORDER BY last_value::numeric / max_value DESC LIMIT 5",
    ),
]

INSIGHTS_SQL = ";\n".join(q for _, q in INSIGHT_QUERIES)


def qident(*parts: str | None) -> str:
    return ".".join('"' + (p or "").replace('"', '""') + '"' for p in parts)


def _num(v: str | None) -> float:
    return float(v) if v not in (None, "") else 0.0


WEIGHTS = {"critical": 25, "warning": 10, "info": 3, "ok": 0}


def grade(score: int) -> str:
    return "excellent" if score >= 90 else "good" if score >= 75 else "fair" if score >= 50 else "poor"


def analyze(out: Outcome) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Findings from the catalog checks plus a few facts used elsewhere (cache ratio, stats age)."""
    sets = {name: (out.results[i].rows if i < len(out.results) else []) for i, (name, _) in enumerate(INSIGHT_QUERIES)}
    findings: list[dict[str, Any]] = []
    facts: dict[str, Any] = {"cache_hit_ratio": None, "stats_since": None}

    def add(
        id_: str,
        severity: str,
        category: str,
        title: str,
        detail: str,
        suggestion: str | None = None,
        sql: str | None = None,
    ) -> None:
        findings.append(
            {
                "id": id_,
                "severity": severity,
                "category": category,
                "title": title,
                "detail": detail,
                "suggestion": suggestion,
                "sql": sql,
            }
        )

    if sets["db"]:
        hit, read, commit, rollback, deadlocks, temp_bytes, reset = sets["db"][0]
        hit_n, read_n = _num(hit), _num(read)
        if reset:
            with contextlib.suppress(ValueError):
                facts["stats_since"] = dt.datetime.fromisoformat(reset)
        if hit_n + read_n > 0:
            ratio = hit_n / (hit_n + read_n)
            facts["cache_hit_ratio"] = round(ratio, 4)
            if hit_n + read_n >= 10_000 and ratio < 0.95:
                add(
                    "cache_hit_ratio",
                    "critical" if ratio < 0.85 else "warning",
                    "performance",
                    f"Only {ratio:.1%} of reads come from memory",
                    "Healthy databases serve 99% of block reads from memory. Below 95% the working set no "
                    "longer fits in RAM and queries wait on disk.",
                    "Add indexes for frequent filters, or move to a larger compute size.",
                )
        total = _num(commit) + _num(rollback)
        if total >= 1000 and _num(rollback) / total > 0.1:
            add(
                "rollbacks",
                "warning",
                "reliability",
                f"{_num(rollback) / total:.0%} of transactions roll back",
                "A high rollback share usually means application errors, constraint violations or retries.",
                "Check your application logs for failing statements.",
            )
        if _num(deadlocks) > 0:
            add(
                "deadlocks",
                "warning" if _num(deadlocks) >= 10 else "info",
                "reliability",
                f"{int(_num(deadlocks))} deadlock(s) since statistics were reset",
                "Two transactions waited on each other's locks and PostgreSQL aborted one of them.",
                "Update rows in a consistent order across transactions and keep transactions short.",
            )
        if _num(temp_bytes) >= 1024**3:
            add(
                "temp_files",
                "info",
                "performance",
                f"Queries wrote {_num(temp_bytes) / 1024**3:.1f} GB of temporary files",
                "Sorts and hashes that don't fit in work_mem spill to disk, which is much slower.",
                "Use EXPLAIN ANALYZE in the SQL editor to find the query; an index often avoids the sort.",
            )
    for s, t, seq, tup_read, idx, live in sets["seq_scans"]:
        add(
            f"seq_scan:{s}.{t}",
            "warning",
            "performance",
            f"{s}.{t} is mostly read by full table scans",
            f"{int(_num(seq)):,} sequential scans read {int(_num(tup_read)):,} rows (≈{int(_num(live)):,} live rows), "
            f"against {int(_num(idx)):,} index scans.",
            "Find the columns your queries filter on (EXPLAIN in the SQL editor shows a Seq Scan) and index them.",
            f"CREATE INDEX CONCURRENTLY ON {qident(s, t)} (/* column */);",
        )
    for s, t, idx, size in sets["unused_indexes"]:
        add(
            f"unused_index:{s}.{idx}",
            "info",
            "storage",
            f"Index {idx} on {s}.{t} has never been used",
            f"It takes {_num(size) / 1024**2:.1f} MB and slows every write to the table. Statistics only cover "
            "the time since the last reset or restart, so check it's also unused on replicas or by rare jobs.",
            "Drop it if no query needs it.",
            f"DROP INDEX CONCURRENTLY {qident(s, idx)};",
        )
    for s, t, dead, live in sets["dead_tuples"]:
        add(
            f"dead_tuples:{s}.{t}",
            "warning",
            "storage",
            f"{s}.{t} has {int(_num(dead)):,} dead rows",
            f"That is {int(_num(dead)):,} dead versus {int(_num(live)):,} live rows: autovacuum is falling behind, "
            "which wastes space and slows scans.",
            "Run VACUUM now; if it keeps happening, batch large UPDATE/DELETE jobs.",
            f"VACUUM (ANALYZE) {qident(s, t)};",
        )
    no_pk = sets["no_primary_key"]
    if no_pk:
        names = ", ".join(f"{s}.{t}" for s, t in no_pk[:10])
        more = " and more" if len(no_pk) > 10 else ""
        add(
            "no_primary_key",
            "warning" if len(no_pk) >= 3 else "info",
            "schema",
            f"{len(no_pk) if len(no_pk) <= 10 else '10+'} table(s) without a primary key",
            f"{names}{more}. Rows without a key can't be updated or deleted reliably, and many tools "
            "(ORMs, logical replication) require one.",
            "Add a primary key.",
            f"ALTER TABLE {qident(no_pk[0][0], no_pk[0][1])} "
            "ADD COLUMN id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY;",
        )
    for s, t, con, cols in sets["unindexed_fks"]:
        add(
            f"unindexed_fk:{s}.{con}",
            "info",
            "performance",
            f"Foreign key {con} on {s}.{t} has no index",
            "Deleting or updating the referenced rows must scan this whole table, and joins on the key are slower.",
            "Index the foreign key columns.",
            f"CREATE INDEX CONCURRENTLY ON {qident(s, t)} ({cols});",
        )
    if sets["sessions"]:
        stuck, oldest = sets["sessions"][0]
        if _num(stuck) > 0:
            add(
                "idle_in_transaction",
                "warning",
                "reliability",
                f"{int(_num(stuck))} session(s) idle in a transaction for over 5 minutes",
                "Open transactions hold locks and stop VACUUM from cleaning up, so tables bloat.",
                "Make sure your application commits or rolls back, and set idle_in_transaction_session_timeout.",
            )
        elif _num(oldest) > 3600:
            add(
                "long_transaction",
                "info",
                "reliability",
                f"A transaction has been open for {_num(oldest) / 3600:.1f} hours",
                "Long transactions hold back VACUUM.",
            )
    for s, seq, last, mx in sets["sequences"]:
        pct = _num(last) / _num(mx) if _num(mx) else 0
        add(
            f"sequence:{s}.{seq}",
            "critical" if pct > 0.9 else "warning",
            "capacity",
            f"Sequence {s}.{seq} has used {pct:.0%} of its range",
            "When it runs out, inserts fail. Columns created as serial/integer stop at 2,147,483,647.",
            "Switch the column and sequence to bigint.",
            f"ALTER SEQUENCE {qident(s, seq)} AS bigint;",
        )
    return findings, facts
