"""Database lifecycle state machine. The only place database status may change."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import Database, DatabaseEvent, utcnow

TRANSITIONS: dict[str, set[str]] = {
    "pending": {"validating", "failed", "deleting"},
    "validating": {"queued", "failed", "deleting"},
    "queued": {"provisioning", "failed", "deleting"},
    "provisioning": {"configuring", "failed", "deleting", "queued"},
    "configuring": {"testing", "failed", "deleting", "queued"},
    "testing": {"ready", "failed", "deleting", "queued"},
    "ready": {"stopping", "deleting", "configuring", "migrating"},
    # Operator-initiated move to another host; before the switch it rolls back to ready.
    "migrating": {"testing", "ready", "failed", "deleting"},
    "failed": {"queued", "deleting"},
    "stopping": {"stopped", "ready", "failed", "deleting"},
    "stopped": {"queued", "deleting"},
    "deleting": {"deleted", "failed"},
    "deleted": set(),
}

# States in which a provisioning attempt is in flight (used by reconciliation).
IN_FLIGHT = {"pending", "validating", "queued", "provisioning", "configuring", "testing"}
LIVE = set(TRANSITIONS) - {"deleted"}


class InvalidTransition(Exception):
    def __init__(self, current: str, target: str):
        super().__init__(f"invalid database transition {current} -> {target}")
        self.current = current
        self.target = target


def can_transition(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(current, set())


def transition(
    db: Session,
    database: Database,
    target: str,
    *,
    reason: str | None = None,
    actor_type: str = "system",
    event_type: str = "status_changed",
) -> None:
    current = database.status
    if current == target:
        return
    if not can_transition(current, target):
        raise InvalidTransition(current, target)
    database.status = target
    database.status_reason = reason[:300] if reason else None
    database.updated_at = utcnow()
    if target == "ready":
        database.ready_at = database.ready_at or utcnow()
    if target == "deleted":
        database.deleted_at = utcnow()
    db.add(
        DatabaseEvent(
            id=uuid.uuid4(),
            database_id=database.id,
            owner_id=database.owner_id,
            event_type=event_type,
            from_status=current,
            to_status=target,
            message=reason[:500] if reason else None,
            actor_type=actor_type,
        )
    )


def record_event(db: Session, database: Database, event_type: str, message: str, actor_type: str = "system") -> None:
    db.add(
        DatabaseEvent(
            id=uuid.uuid4(),
            database_id=database.id,
            owner_id=database.owner_id,
            event_type=event_type,
            from_status=database.status,
            to_status=database.status,
            message=message[:500],
            actor_type=actor_type,
        )
    )
