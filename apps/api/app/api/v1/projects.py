from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import ApiError, Conflict, NotFound
from app.models import Database, Project, utcnow
from app.schemas.common import Message, Page
from app.schemas.resources import DatabaseOut, ProjectCreate, ProjectOut, ProjectUpdate
from app.security.deps import Principal, require_scope
from app.services import approvals, database_service, idempotency, plans
from app.services.audit import audit

router = APIRouter(prefix="/projects", tags=["projects"])


def _stats(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[int, int, int]]:
    if not ids:
        return {}
    rows = db.execute(
        select(
            Database.project_id,
            func.count(Database.id),
            func.coalesce(func.sum(Database.allocated_storage_mb), 0),
            func.coalesce(func.sum(Database.storage_used_bytes), 0),
        )
        .where(Database.project_id.in_(ids), Database.status != "deleted")
        .group_by(Database.project_id)
    ).all()
    return {r[0]: (int(r[1] or 0), int(r[2] or 0), int(r[3] or 0)) for r in rows}


def _out(p: Project, st: tuple[int, int, int] | None) -> ProjectOut:
    o = ProjectOut.model_validate(p)
    if st:
        o.database_count, o.allocated_storage_mb, o.storage_used_bytes = st
    return o


def _get(db: Session, principal: Principal, project_id: uuid.UUID) -> Project:
    p = db.scalar(
        select(Project).where(
            Project.id == project_id, Project.owner_id == principal.user.id, Project.deleted_at.is_(None)
        )
    )
    if p is None or not principal.can_access_project(p.id):
        raise NotFound("Project not found.")
    return p


@router.post("", status_code=201, response_model=ProjectOut, summary="Create a project")
def create_project(
    body: ProjectCreate,
    response: Response,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("projects:write")),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> ProjectOut:
    if principal.project_ids is not None:
        raise ApiError(
            "API keys restricted to specific projects cannot create projects.", code="forbidden", status_code=403
        )
    rec, replay = idempotency.begin(db, principal.user.id, idempotency_key, "projects.create", body.model_dump())
    if replay:
        response.status_code = replay[0]
        response.headers["Idempotent-Replayed"] = "true"
        return ProjectOut.model_validate(replay[1])
    plans.check_can_create_project(db, principal.user.id)
    if db.scalar(
        select(Project.id).where(
            Project.owner_id == principal.user.id,
            Project.deleted_at.is_(None),
            func.lower(Project.name) == body.name.lower(),
        )
    ):
        raise Conflict("You already have a project with this name.", code="name_taken")
    p = Project(
        id=uuid.uuid4(),
        owner_id=principal.user.id,
        name=body.name,
        description=body.description,
        environment=body.environment,
        status="active",
    )
    db.add(p)
    db.flush()
    audit(
        db,
        "project.create",
        principal=principal,
        resource_type="project",
        resource_id=p.id,
        metadata={"name": p.name, "environment": p.environment},
    )
    out = _out(p, None)
    idempotency.complete(rec, 201, out.model_dump(mode="json"))
    return out


@router.get("", response_model=Page[ProjectOut], summary="List projects")
def list_projects(
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("projects:read")),
    status: Literal["active", "archived", "all"] = "all",
    q: str | None = Query(None, max_length=64),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
) -> Page[ProjectOut]:
    stmt = select(Project).where(Project.owner_id == principal.user.id, Project.deleted_at.is_(None))
    if principal.project_ids is not None:
        stmt = stmt.where(Project.id.in_(principal.project_ids))
    if status != "all":
        stmt = stmt.where(Project.status == status)
    if q:
        stmt = stmt.where(or_(Project.name.ilike(f"%{q}%"), Project.description.ilike(f"%{q}%")))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Project.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    st = _stats(db, [p.id for p in rows])
    return Page(items=[_out(p, st.get(p.id)) for p in rows], total=total, page=page, page_size=page_size)


@router.get("/{project_id}", response_model=ProjectOut, summary="Get a project")
def get_project(
    project_id: uuid.UUID,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("projects:read")),
) -> ProjectOut:
    p = _get(db, principal, project_id)
    return _out(p, _stats(db, [p.id]).get(p.id))


@router.get("/{project_id}/databases", response_model=list[DatabaseOut], summary="Databases in a project")
def project_databases(
    project_id: uuid.UUID,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("databases:read")),
) -> list[DatabaseOut]:
    p = _get(db, principal, project_id)
    rows = db.scalars(
        select(Database)
        .where(Database.project_id == p.id, Database.status != "deleted")
        .order_by(Database.created_at.desc())
    ).all()
    return [database_service.to_out(db, d, project_name=p.name) for d in rows]


@router.patch("/{project_id}", response_model=ProjectOut, summary="Update a project")
def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdate,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("projects:write")),
) -> ProjectOut:
    p = _get(db, principal, project_id)
    changes = {}
    if body.name is not None and body.name != p.name:
        if db.scalar(
            select(Project.id).where(
                Project.owner_id == principal.user.id,
                Project.deleted_at.is_(None),
                func.lower(Project.name) == body.name.lower(),
                Project.id != p.id,
            )
        ):
            raise Conflict("You already have a project with this name.", code="name_taken")
        changes["name"] = body.name
        p.name = body.name
    if body.environment is not None:
        changes["environment"] = body.environment
        p.environment = body.environment
    if body.description is not None:
        changes["description"] = "updated"
        p.description = body.description or None
    if changes:
        p.updated_at = utcnow()
        audit(db, "project.update", principal=principal, resource_type="project", resource_id=p.id, metadata=changes)
    return _out(p, _stats(db, [p.id]).get(p.id))


@router.post("/{project_id}/archive", response_model=ProjectOut, summary="Archive a project")
def archive_project(
    project_id: uuid.UUID,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("projects:write")),
) -> ProjectOut:
    p = _get(db, principal, project_id)
    if p.status != "archived":
        p.status = "archived"
        p.archived_at = utcnow()
        audit(db, "project.archive", principal=principal, resource_type="project", resource_id=p.id)
    return _out(p, _stats(db, [p.id]).get(p.id))


@router.post("/{project_id}/unarchive", response_model=ProjectOut, summary="Restore an archived project")
def unarchive_project(
    project_id: uuid.UUID,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("projects:write")),
) -> ProjectOut:
    p = _get(db, principal, project_id)
    if p.status == "archived":
        p.status = "active"
        p.archived_at = None
        audit(db, "project.unarchive", principal=principal, resource_type="project", resource_id=p.id)
    return _out(p, _stats(db, [p.id]).get(p.id))


@router.delete("/{project_id}", response_model=Message, summary="Delete an empty project")
def delete_project(
    project_id: uuid.UUID,
    confirm: str = Query(..., max_length=64, description="Project name"),
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("projects:delete")),
) -> Message:
    p = _get(db, principal, project_id)
    if confirm != p.name:
        raise ApiError("Type the project name to confirm deletion.", code="confirmation_mismatch", status_code=400)
    _check_empty(db, p)
    approvals.gate(
        db,
        principal,
        "project.delete",
        resource_type="project",
        resource_id=p.id,
        summary=f"delete project “{p.name}”",
    )
    delete_project_record(db, principal, p)
    return Message(status="deleted")


def _check_empty(db: Session, p: Project) -> None:
    live = db.scalar(select(func.count(Database.id)).where(Database.project_id == p.id, Database.status != "deleted"))
    if live:
        raise Conflict(f"Delete the {live} database(s) in this project first.", code="project_not_empty")


def delete_project_record(db: Session, principal: Principal, p: Project) -> None:
    """Soft-delete an empty project (also used when an approved request is executed)."""
    if p.deleted_at is not None:
        raise Conflict("This project was already deleted.", code="invalid_state")
    _check_empty(db, p)
    p.status = "deleted"
    p.deleted_at = utcnow()
    audit(
        db, "project.delete", principal=principal, resource_type="project", resource_id=p.id, metadata={"name": p.name}
    )
