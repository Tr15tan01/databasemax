#!/usr/bin/env python3
"""Split /etc/databasemax/.env (the one file operators edit) into per-service environment files.

Each service gets only the variables it uses, so e.g. the public API never holds the backup keys
or the provisioner's admin secret, and the gateway holds no database or session secrets.

    render-env.py /etc/databasemax/.env /etc/databasemax/env [--role all|worker]

Writes <out>/{api,migrate,provisioner,gateway,web,agent,caddy}.env (0600, root; systemd reads them as root
before starting each service as its own user).
Standard library only; runs with the system python3.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

API = {
    "DATABASE_URL", "REDIS_URL", "SESSION_SECRET", "DMX_API_KEY_PEPPER", "DMX_SECRETS_KEYS", "DMX_SECRETS_ACTIVE_KEY",
    "DMX_ENV", "DMX_PUBLIC_WEB_URL", "DMX_PUBLIC_API_URL", "DMX_CORS_ORIGINS", "DMX_COOKIE_SECURE", "DMX_COOKIE_DOMAIN",
    "DMX_REGION", "DMX_REGION_LABEL", "DMX_LOG_LEVEL", "DMX_EMAIL_BACKEND", "DMX_EMAIL_FROM", "DMX_EMAIL_FILE_DIR",
    "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_STARTTLS",
    "PROVISIONER_URL", "PROVISIONER_TOKEN", "INTERNAL_API_TOKEN", "DMX_PG_VERSIONS", "DMX_DEFAULT_PG_VERSION",
    "DMX_DB_PUBLIC_HOST", "DMX_DB_HOST_SUFFIX", "DMX_DB_PUBLIC_PORT", "DMX_GATEWAY_INTERNAL_HOST",
    "DMX_GATEWAY_INTERNAL_PORT", "DMX_GATEWAY_STATUS_URL", "DMX_GATEWAY_CA_FILE",
    "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI",
    "PADDLE_ENVIRONMENT", "PADDLE_API_KEY", "PADDLE_CLIENT_TOKEN", "PADDLE_WEBHOOK_SECRET", "PADDLE_PRICE_FREE",
    "PADDLE_PRICE_DEVELOPER", "PADDLE_PRICE_PRO", "DMX_BILLING_GRACE_DAYS",
    "DMX_DB_POOL_SIZE", "DMX_RATE_LIMIT_ENABLED", "DMX_API_RATE_PER_MINUTE_API_KEY", "DMX_API_RATE_PER_MINUTE_USER",
    "DMX_API_WRITE_RATE_PER_MINUTE", "DMX_REAUTH_WINDOW_MINUTES", "DMX_SESSION_IDLE_HOURS", "DMX_SESSION_TTL_HOURS",
    "DMX_ARGON2_MEMORY_KIB", "DMX_ARGON2_TIME_COST", "DMX_ARGON2_PARALLELISM", "DMX_METRICS_INTERVAL_SECONDS",
    "DMX_RECONCILE_INTERVAL_SECONDS", "DMX_ORPHAN_GRACE_SECONDS", "DMX_JOB_MAX_ATTEMPTS", "DMX_JOB_LEASE_SECONDS",
    "DMX_LONG_JOB_LEASE_SECONDS", "DMX_JOB_BACKOFF_BASE_SECONDS", "DMX_MAX_CONCURRENT_JOBS_PER_USER",
    "DMX_BACKUPS_ENABLED", "DMX_BACKUP_SCHEDULE_INTERVAL_SECONDS", "DMX_BACKUP_VERIFY_ENABLED",
    "DMX_BACKUP_VERIFY_PER_HOUR",
}  # fmt: skip
PROVISIONER = {
    "DMX_ENV", "PROVISIONER_TOKEN", "PROVISIONER_ADMIN_SECRET", "DMX_PG_VERSIONS", "DMX_HOST_NAME", "DMX_REGION",
    "DMX_DATA_PLANE_MEMORY_MB", "DMX_DATA_PLANE_DISK_MB", "DMX_DATA_PLANE_CPUS", "DMX_MAX_INSTANCES",
    "DMX_HOST_LABELS", "DMX_INSTANCE_LISTEN", "DMX_INSTANCE_PORT_MIN", "DMX_INSTANCE_PORT_MAX",
    "DMX_INSTANCE_CLIENT_CIDRS", "DMX_STORAGE_BACKEND", "DMX_STORAGE_ROOT", "DMX_PROVISIONER_LISTEN",
    "DMX_BACKUP_KEYS", "DMX_BACKUP_ACTIVE_KEY", "DMX_BACKUP_STORE", "DMX_BACKUP_DISK_BUDGET_MB",
    "DMX_BACKUP_S3_ENDPOINT", "DMX_BACKUP_S3_REGION", "DMX_BACKUP_S3_BUCKET", "DMX_BACKUP_S3_ACCESS_KEY",
    "DMX_BACKUP_S3_SECRET_KEY", "DMX_BACKUP_S3_PREFIX", "DMX_BACKUP_TIMEOUT_SECONDS", "DMX_VERIFY_PROFILE",
}  # fmt: skip
GATEWAY = {
    "INTERNAL_API_TOKEN", "DMX_DB_HOST_SUFFIX", "DMX_LOG_LEVEL", "DMX_GATEWAY_HOST", "DMX_GATEWAY_PORT",
    "DMX_GATEWAY_STATUS_HOST", "DMX_GATEWAY_STATUS_PORT", "DMX_GATEWAY_CERT", "DMX_GATEWAY_KEY",
    "DMX_GATEWAY_HANDSHAKE_TIMEOUT", "DMX_GATEWAY_ROUTE_TTL", "DMX_GATEWAY_PER_IP_RATE", "DMX_GATEWAY_MAX_CONNECTIONS",
    "DMX_GATEWAY_PER_IP_MAX_ACTIVE", "DMX_GATEWAY_KEEPALIVE_IDLE", "DMX_GATEWAY_EDITOR_MAX_MESSAGE",
    "DMX_INTERNAL_API_URL",
}  # fmt: skip

# Fixed wiring between services on one host (operators may still override them in .env).
DEFAULTS = {
    "api": {
        "PROVISIONER_URL": "http://127.0.0.1:8090",
        "DMX_GATEWAY_INTERNAL_HOST": "127.0.0.1",
        "DMX_GATEWAY_INTERNAL_PORT": "5432",
        "DMX_GATEWAY_STATUS_URL": "http://127.0.0.1:9187/",
        "DMX_EMAIL_FILE_DIR": "/var/lib/databasemax/mail",
    },
    "provisioner": {
        "DMX_STORAGE_ROOT": "/var/lib/databasemax/data-plane",
        "DMX_PROVISIONER_STATE_DIR": "/var/lib/databasemax/provisioner",
        "DMX_BACKUP_DIR": "/var/lib/databasemax/customer-backups",
        "DMX_AGENT_SOCKET": "/run/databasemax/agent.sock",
        "DMX_PROVISIONER_LISTEN": "127.0.0.1",
    },
    "gateway": {
        "DMX_GATEWAY_HOST": "0.0.0.0",  # noqa: S104 - the public PostgreSQL listener
        "DMX_GATEWAY_PORT": "5432",
        "DMX_GATEWAY_STATUS_HOST": "127.0.0.1",
        "DMX_GATEWAY_CERT": "/etc/databasemax/gateway-certs/fullchain.pem",
        "DMX_GATEWAY_KEY": "/etc/databasemax/gateway-certs/privkey.pem",
        "DMX_INTERNAL_API_URL": "http://127.0.0.1:8001",
    },
    "web": {"DMX_API_ORIGIN": "http://127.0.0.1:8000", "PORT": "3000", "HOSTNAME": "127.0.0.1",
            "NODE_ENV": "production", "NEXT_TELEMETRY_DISABLED": "1"},  # fmt: skip
    "agent": {"DMX_STORAGE_BACKEND": "dir"},
}
SERVICES = ("api", "migrate", "provisioner", "gateway", "web", "agent", "caddy")
KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")


def parse(path: Path) -> dict[str, str]:
    """KEY=value lines; values may be single- or double-quoted (no expansion)."""
    out: dict[str, str] = {}
    for n, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:]
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not KEY.match(key):
            sys.exit(f"{path}:{n}: expected KEY=value")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
            if raw.strip().split("=", 1)[1].strip()[0] == '"':
                value = value.replace('\\"', '"').replace("\\\\", "\\")
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        out[key] = value
    return out


def services(env: dict[str, str]) -> dict[str, dict[str, str]]:
    def pick(keys: set[str], defaults: dict[str, str]) -> dict[str, str]:
        d = dict(defaults)
        d.update({k: v for k, v in env.items() if k in keys and v != ""})
        return d

    api = pick(API, DEFAULTS["api"])
    gateway = pick(GATEWAY, DEFAULTS["gateway"])
    if env.get("DMX_GATEWAY_CERT_DIR") and "DMX_GATEWAY_CERT" not in env:  # older .env files
        gateway["DMX_GATEWAY_CERT"] = env["DMX_GATEWAY_CERT_DIR"].rstrip("/") + "/fullchain.pem"
        gateway["DMX_GATEWAY_KEY"] = env["DMX_GATEWAY_CERT_DIR"].rstrip("/") + "/privkey.pem"
    web = dict(DEFAULTS["web"])
    if env.get("DMX_PUBLIC_WEB_URL"):
        web["NEXT_PUBLIC_SITE_URL"] = env["DMX_PUBLIC_WEB_URL"]
    agent = dict(DEFAULTS["agent"])
    agent.update({k: env[k] for k in ("DMX_STORAGE_BACKEND",) if env.get(k)})
    return {
        "api": api,
        # Schema-owner credentials only for the migration unit, never for the running API.
        "migrate": {"MIGRATIONS_DATABASE_URL": env.get("MIGRATIONS_DATABASE_URL", "")},
        "provisioner": pick(PROVISIONER, DEFAULTS["provisioner"]),
        "gateway": gateway,
        "web": web,
        "agent": agent,
        # Caddy substitutes these in /etc/caddy/Caddyfile (drop-in caddy.service.d/databasemax.conf).
        "caddy": {
            "DMX_DOMAIN": env.get("DMX_DOMAIN", "localhost"),
            "DMX_ACME_EMAIL": env.get("DMX_ACME_EMAIL") or f"ops@{env.get('DMX_DOMAIN', 'localhost')}",
        },
    }


def render(values: dict[str, str]) -> str:
    lines = ["# Generated by infra/scripts/render-env.py from /etc/databasemax/.env — edit that file instead."]
    for k in sorted(values):
        v = values[k]
        if "'" in v or "\n" in v or "\\" in v:
            sys.exit(f"{k}: values may not contain quotes, backslashes or newlines")
        lines.append(f"{k}='{v}'")
    return "\n".join(lines) + "\n"


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    role = sys.argv[4] if len(sys.argv) > 4 and sys.argv[3] == "--role" else "all"
    env = parse(src)
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    wanted = ("provisioner", "agent") if role == "worker" else SERVICES
    for name, values in services(env).items():
        if name not in wanted:
            continue
        f = out / f"{name}.env"
        tmp = f.with_suffix(".tmp")
        tmp.write_text(render(values))
        os.chmod(tmp, 0o600)
        os.replace(tmp, f)
    print(f"wrote {', '.join(f'{n}.env' for n in wanted)} to {out}")


if __name__ == "__main__":
    main()
