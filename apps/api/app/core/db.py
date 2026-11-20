"""Control-plane database engine and session handling.

Row-Level Security context: every transaction started by a Session carries two settings,
applied with `set_config(..., is_local => true)` right after BEGIN:

- `dmx.user_id`   — the authenticated tenant, or '' when none
- `dmx.bypass_rls` — 'on' only for explicitly system-scoped sessions (worker, admin, auth lookups)

Tenant tables have forced RLS policies based on these settings (see migrations). A code path
that forgets an ownership filter therefore sees no foreign rows.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        s = get_settings()
        _engine = create_engine(
            s.database_url.get_secret_value(),
            pool_size=s.db_pool_size,
            max_overflow=s.db_pool_size,
            pool_pre_ping=True,
            pool_recycle=1800,
            connect_args={"connect_timeout": 5, "options": "-c statement_timeout=30000"},
        )
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    return _engine


def reset_engine() -> None:
    """Used by tests to rebuild the engine after settings change."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def _session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


@event.listens_for(Session, "after_begin")
def _apply_rls_context(session: Session, transaction: Any, connection: Any) -> None:
    ctx = session.info.get("rls", {})
    user_id = ctx.get("user_id") or ""
    bypass = "on" if ctx.get("bypass") else "off"
    connection.exec_driver_sql(
        "SELECT set_config('dmx.user_id', %(uid)s, true), set_config('dmx.bypass_rls', %(bp)s, true)",
        {"uid": str(user_id), "bp": bypass},
    )


def new_session(*, user_id: uuid.UUID | None = None, system: bool = False) -> Session:
    session = _session_factory()()
    set_rls(session, user_id=user_id, system=system)
    return session


def set_rls(session: Session, *, user_id: uuid.UUID | None = None, system: bool = False) -> None:
    """Change the RLS scope. Takes effect from the next transaction; if a transaction is open,
    it is applied immediately as well."""
    session.info["rls"] = {"user_id": str(user_id) if user_id else "", "bypass": system}
    if session.in_transaction():
        session.execute(
            text("SELECT set_config('dmx.user_id', :uid, true), set_config('dmx.bypass_rls', :bp, true)"),
            {"uid": str(user_id) if user_id else "", "bp": "on" if system else "off"},
        )


@contextmanager
def system_session() -> Iterator[Session]:
    """A session that bypasses RLS. Only for worker, admin and authentication internals."""
    session = new_session(system=True)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request. Starts unscoped (no tenant visibility);
    the auth dependency narrows it to the authenticated user."""
    session = new_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
