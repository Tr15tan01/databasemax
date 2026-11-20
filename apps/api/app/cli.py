"""Operator CLI. On a host: sudo dmx-admin <command> (runs as the API user with its environment).

  python -m app.cli grant-admin --email ops@example.com --reason "initial operator"
  python -m app.cli revoke-admin --email ops@example.com --reason "left team"
  python -m app.cli list-admins
  DMX_USER_PASSWORD=... python -m app.cli create-user --email test1@example.com [--name "Test 1"] [--unverified]
  python -m app.cli verify-user --email someone@example.com --reason "test account"
  python -m app.cli reencrypt-secrets
  python -m app.cli check-config
  DMX_HOST_TOKEN=... python -m app.cli register-host --name worker-2 --endpoint http://10.0.1.12:8090 \
      --region oci-fra-1 [--labels '{"tier":"standard"}']
  python -m app.cli list-hosts
  python -m app.cli firewall-plan --host worker-2 --control-ip 10.0.1.11 --gateway-ip 10.0.1.11

Admin grants are explicit, audited and never inferred from email domains or client input.
"""

from __future__ import annotations

import argparse
import sys
import uuid

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import system_session
from app.models import AdminRoleAssignment, User, utcnow
from app.security.secrets_store import secret_store
from app.services.audit import audit
from app.services.auth_service import find_user_by_email


def _read_password() -> str:
    """From DMX_USER_PASSWORD, else stdin (never argv, which other local users can read)."""
    import os

    pw = os.environ.get("DMX_USER_PASSWORD")
    if pw:
        return pw
    if sys.stdin.isatty():
        import getpass

        return getpass.getpass("Password for the new account: ")
    return sys.stdin.readline().rstrip("\n")


def create_user(email: str, name: str | None, unverified: bool) -> int:
    """Create an account without the email round-trip (test users, demo accounts). Same
    password policy, plan setup and audit as web registration; source recorded as `cli`."""
    from pydantic import ValidationError

    from app.core.errors import ApiError
    from app.schemas.auth import RegisterIn
    from app.services.auth_service import register

    password = _read_password()
    try:
        body = RegisterIn(email=email, password=password or "x", display_name=name)
    except ValidationError:
        print(f"not a valid email address or name: {email}", file=sys.stderr)
        return 1
    try:
        with system_session() as db:
            user = register(
                db,
                email=body.email,
                password=password,
                display_name=name,
                ip=None,
                pre_verified=not unverified,
                source="cli",
            )
            if user is None:
                print(f"an account with email {email} already exists", file=sys.stderr)
                return 1
            uid = user.id
    except ApiError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    state = "verification email queued" if unverified else "email marked verified"
    print(f"created {email} ({uid}); {state}")
    return 0


def verify_user(email: str, reason: str) -> int:
    from app.services.auth_service import mark_email_verified

    with system_session() as db:
        user = find_user_by_email(db, email)
        if user is None:
            print(f"no user with email {email}", file=sys.stderr)
            return 1
        if not mark_email_verified(db, user):
            print("already verified")
            return 0
        audit(
            db,
            "admin.user_email_verified",
            actor_type="system",
            tenant_user_id=user.id,
            resource_type="user",
            resource_id=user.id,
            reason=reason,
            metadata={"source": "cli"},
        )
    print(f"marked {email} as verified")
    return 0


def grant_admin(email: str, reason: str) -> int:
    with system_session() as db:
        user = find_user_by_email(db, email)
        if user is None:
            print(f"no user with email {email}", file=sys.stderr)
            return 1
        if not user.email_verified_at:
            print("refusing: the account's email is not verified", file=sys.stderr)
            return 1
        existing = db.scalar(
            select(AdminRoleAssignment).where(
                AdminRoleAssignment.user_id == user.id, AdminRoleAssignment.revoked_at.is_(None)
            )
        )
        if existing:
            print("already an admin")
            return 0
        db.add(AdminRoleAssignment(id=uuid.uuid4(), user_id=user.id, role="admin", grant_source="cli", reason=reason))
        audit(
            db,
            "admin.role_granted",
            actor_type="system",
            tenant_user_id=user.id,
            resource_type="user",
            resource_id=user.id,
            reason=reason,
            metadata={"source": "cli"},
        )
    print(f"granted admin to {email}")
    return 0


def revoke_admin(email: str, reason: str) -> int:
    with system_session() as db:
        user = find_user_by_email(db, email)
        if user is None:
            print(f"no user with email {email}", file=sys.stderr)
            return 1
        rows = db.scalars(
            select(AdminRoleAssignment).where(
                AdminRoleAssignment.user_id == user.id, AdminRoleAssignment.revoked_at.is_(None)
            )
        ).all()
        for r in rows:
            r.revoked_at = utcnow()
        audit(
            db,
            "admin.role_revoked",
            actor_type="system",
            tenant_user_id=user.id,
            resource_type="user",
            resource_id=user.id,
            reason=reason,
            metadata={"source": "cli"},
        )
    print(f"revoked admin from {email}")
    return 0


def list_admins() -> int:
    with system_session() as db:
        for a, email in db.execute(
            select(AdminRoleAssignment, User.email).join(User, User.id == AdminRoleAssignment.user_id)
        ):
            state = "revoked" if a.revoked_at else "active"
            print(f"{email}\t{a.role}\t{state}\t{a.granted_at:%Y-%m-%d}\t{a.reason}")
    return 0


def reencrypt() -> int:
    with system_session() as db:
        n = secret_store.reencrypt_all(db)
    print(f"re-encrypted {n} secret(s) with key {get_settings().secrets_active_key}")
    return 0


def check_config() -> int:
    s = get_settings()
    print(
        f"environment={s.environment} google={s.google_enabled} paddle={s.paddle_enabled} "
        f"email={s.email_backend} pg_versions={s.supported_pg_versions} region={s.region}"
    )
    return 0


def register_host(name: str, endpoint: str, region: str, labels_json: str | None) -> int:
    """Register (or update) a worker host. The provisioner token is read from DMX_HOST_TOKEN or
    stdin — never from argv (shell history, process lists) — verified live, stored encrypted."""
    import json
    import os
    import re

    from app.provisioning.client import ProvisionerClient, ProvisionerError
    from app.provisioning.hosts import register_host as _register

    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}", name):
        print("invalid host name (lowercase letters, digits, dashes)", file=sys.stderr)
        return 2
    if not re.fullmatch(r"https?://[A-Za-z0-9.\-:\[\]]+(:\d+)?", endpoint):
        print("invalid endpoint (http(s)://host:port)", file=sys.stderr)
        return 2
    token = os.environ.get("DMX_HOST_TOKEN") or sys.stdin.readline().strip()
    if len(token) < 32:
        print("the provisioner token must be at least 32 characters", file=sys.stderr)
        return 2
    labels = json.loads(labels_json) if labels_json else None
    client = ProvisionerClient(base_url=endpoint, token=token, timeout=10)
    try:
        cap = client.capacity()
    except ProvisionerError as e:
        print(f"could not reach the provisioner with that token: {e.code}", file=sys.stderr)
        return 1
    if cap.get("host_name") != name:
        print(f"the provisioner reports host name {cap.get('host_name')!r}, not {name!r}", file=sys.stderr)
        return 1
    if cap.get("region") != region:
        print(f"the provisioner reports region {cap.get('region')!r}, not {region!r}", file=sys.stderr)
        return 1
    with system_session() as db:
        h = _register(db, name=name, endpoint=endpoint, region=region, token=token, labels=labels)
        h.capacity_metadata = cap
        h.last_heartbeat_at = utcnow()
        audit(
            db,
            "admin.host_registered",
            actor_type="admin",
            resource_type="host",
            resource_id=h.id,
            metadata={"host": name, "endpoint": endpoint, "region": region, "source": "cli"},
        )
        print(f"registered host {name} ({h.id}) status={h.status}")
    return 0


def list_hosts() -> int:
    from app.models import InfrastructureHost

    with system_session() as db:
        for h in db.scalars(select(InfrastructureHost).order_by(InfrastructureHost.name)):
            beat = h.last_heartbeat_at.isoformat(timespec="seconds") if h.last_heartbeat_at else "never"
            print(f"{h.name:20} {h.status:9} {h.region:16} {h.endpoint:32} heartbeat={beat}")
    return 0


def firewall_plan(host: str, control_ip: str, gateway_ip: str) -> int:
    """Print iptables rules for a worker host: its provisioner only from the control plane, its
    instance ports only from the gateway host. Review, then apply on that host. (Outbound
    connections from customer databases are already blocked by their units: IPAddressDeny=any.)"""
    import ipaddress

    from app.models import InfrastructureHost

    for ip in (control_ip, gateway_ip):
        ipaddress.ip_address(ip)
    with system_session() as db:
        h = db.scalar(select(InfrastructureHost).where(InfrastructureHost.name == host))
        if h is None:
            print("unknown host", file=sys.stderr)
            return 1
        cap = h.capacity_metadata or {}
    port = (h.endpoint.rsplit(":", 1)[-1]).rstrip("/") if h.endpoint.count(":") >= 2 else "8090"
    lo, hi = cap.get("port_min", 20000), cap.get("port_max", 29999)
    print(f"# DatabaseMax worker firewall for {host} — review before applying (as root on {host})")
    print("iptables -N DMX-WORKER 2>/dev/null || iptables -F DMX-WORKER")
    print(f"iptables -A DMX-WORKER -p tcp --dport {port} -s {control_ip} -j ACCEPT")
    print(f"iptables -A DMX-WORKER -p tcp --dport {port} -j DROP")
    print(f"iptables -A DMX-WORKER -p tcp --dport {lo}:{hi} -s {gateway_ip} -j ACCEPT")
    print(f"iptables -A DMX-WORKER -p tcp --dport {lo}:{hi} -j DROP")
    print("iptables -A DMX-WORKER -j RETURN")
    print("iptables -C INPUT -j DMX-WORKER 2>/dev/null || iptables -I INPUT 1 -j DMX-WORKER")
    print("netfilter-persistent save")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="dmx")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("grant-admin")
    g.add_argument("--email", required=True)
    g.add_argument("--reason", required=True)
    r = sub.add_parser("revoke-admin")
    r.add_argument("--email", required=True)
    r.add_argument("--reason", required=True)
    sub.add_parser("list-admins")
    cu = sub.add_parser("create-user", help="create an account without email verification")
    cu.add_argument("--email", required=True)
    cu.add_argument("--name")
    cu.add_argument("--unverified", action="store_true", help="send the normal verification email instead")
    vu = sub.add_parser("verify-user", help="mark an existing account's email as verified")
    vu.add_argument("--email", required=True)
    vu.add_argument("--reason", required=True)
    sub.add_parser("reencrypt-secrets")
    sub.add_parser("check-config")
    rh = sub.add_parser("register-host")
    rh.add_argument("--name", required=True)
    rh.add_argument("--endpoint", required=True)
    rh.add_argument("--region", required=True)
    rh.add_argument("--labels")
    sub.add_parser("list-hosts")
    fp = sub.add_parser("firewall-plan")
    fp.add_argument("--host", required=True)
    fp.add_argument("--control-ip", required=True)
    fp.add_argument("--gateway-ip", required=True)
    a = p.parse_args(argv)
    if a.cmd == "register-host":
        return register_host(a.name, a.endpoint, a.region, a.labels)
    if a.cmd == "list-hosts":
        return list_hosts()
    if a.cmd == "firewall-plan":
        return firewall_plan(a.host, a.control_ip, a.gateway_ip)
    if a.cmd == "create-user":
        return create_user(a.email, a.name, a.unverified)
    if a.cmd == "verify-user":
        return verify_user(a.email, a.reason)
    if a.cmd == "grant-admin":
        return grant_admin(a.email, a.reason)
    if a.cmd == "revoke-admin":
        return revoke_admin(a.email, a.reason)
    if a.cmd == "list-admins":
        return list_admins()
    if a.cmd == "reencrypt-secrets":
        return reencrypt()
    return check_config()


if __name__ == "__main__":
    raise SystemExit(main())
