"""Development-only helpers (never mounted in production)."""

from __future__ import annotations

import email
from email import policy
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from app.core.config import get_settings

router = APIRouter(prefix="/dev", tags=["dev"], include_in_schema=False)


@router.get("/mailbox")
def mailbox(to: str | None = Query(None, max_length=320), limit: int = Query(20, le=100)) -> list[dict[str, str]]:
    s = get_settings()
    if s.is_production or s.email_backend != "file":
        raise HTTPException(404, "not found")
    d = Path(s.email_file_dir)
    if not d.exists():
        return []
    out = []
    for f in sorted(d.glob("*.eml"), reverse=True):
        msg = email.message_from_bytes(f.read_bytes(), policy=policy.default)
        if to and msg["To"].lower() != to.lower():
            continue
        body = msg.get_body(preferencelist=("plain",))
        out.append(
            {
                "to": str(msg["To"]),
                "subject": str(msg["Subject"]),
                "date": f.name[:15],
                "text": body.get_content() if body else "",
            }
        )
        if len(out) >= limit:
            break
    return out


@router.post("/reset-rate-limits")
def reset_rate_limits() -> dict[str, int]:
    """Lets local end-to-end runs register many accounts from 127.0.0.1. Development only."""
    s = get_settings()
    if s.is_production or s.email_backend != "file":
        raise HTTPException(404, "not found")
    from app.core.redis import get_redis

    r = get_redis()
    n = 0
    for key in r.scan_iter(match="dmx:rl:*", count=500):
        r.delete(key)
        n += 1
    return {"deleted": n}
