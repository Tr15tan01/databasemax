"""Application settings.

All configuration comes from environment variables (see the repository-level `.env.example`).
Secrets are typed as `SecretStr` so they are never rendered by `repr()` or accidentally logged.
"""

from __future__ import annotations

import base64
import json
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    # --- general -----------------------------------------------------------------------
    environment: Literal["development", "test", "production"] = Field("development", alias="DMX_ENV")
    app_name: str = "DatabaseMax"
    public_web_url: str = Field("http://localhost:3000", alias="DMX_PUBLIC_WEB_URL")
    public_api_url: str = Field("http://localhost:3000/api", alias="DMX_PUBLIC_API_URL")
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"], alias="DMX_CORS_ORIGINS"
    )
    log_level: str = Field("INFO", alias="DMX_LOG_LEVEL")
    region: str = Field("oci-local-1", alias="DMX_REGION")
    region_label: str = Field("Local development", alias="DMX_REGION_LABEL")

    # --- control-plane database ------------------------------------------------------------
    database_url: SecretStr = Field(
        SecretStr("postgresql+psycopg://dmx_app:dmx_app@127.0.0.1:55432/dmx_control"), alias="DATABASE_URL"
    )
    migrations_database_url: SecretStr | None = Field(None, alias="MIGRATIONS_DATABASE_URL")
    db_pool_size: int = Field(10, alias="DMX_DB_POOL_SIZE")

    # --- redis -----------------------------------------------------------------------------
    redis_url: SecretStr = Field(SecretStr("redis://127.0.0.1:56379/0"), alias="REDIS_URL")

    # --- auth / sessions -------------------------------------------------------------------
    session_secret: SecretStr = Field(SecretStr("dev-only-session-secret-change-me-0123456789"), alias="SESSION_SECRET")
    session_cookie_name: str = "dmx_session"
    csrf_cookie_name: str = "dmx_csrf"
    session_ttl_hours: int = Field(24 * 14, alias="DMX_SESSION_TTL_HOURS")
    session_idle_timeout_hours: int = Field(24 * 3, alias="DMX_SESSION_IDLE_HOURS")
    reauth_window_minutes: int = Field(15, alias="DMX_REAUTH_WINDOW_MINUTES")
    cookie_secure: bool = Field(False, alias="DMX_COOKIE_SECURE")
    cookie_domain: str | None = Field(None, alias="DMX_COOKIE_DOMAIN")
    email_verification_ttl_hours: int = 48
    password_reset_ttl_minutes: int = 30
    argon2_time_cost: int = Field(3, alias="DMX_ARGON2_TIME_COST")
    argon2_memory_kib: int = Field(65536, alias="DMX_ARGON2_MEMORY_KIB")
    argon2_parallelism: int = Field(4, alias="DMX_ARGON2_PARALLELISM")

    # --- Google OAuth (optional) -------------------------------------------------------------
    google_client_id: str | None = Field(None, alias="GOOGLE_CLIENT_ID")
    google_client_secret: SecretStr | None = Field(None, alias="GOOGLE_CLIENT_SECRET")
    google_redirect_uri: str | None = Field(None, alias="GOOGLE_REDIRECT_URI")

    # --- email -------------------------------------------------------------------------------
    email_backend: Literal["smtp", "file", "memory"] = Field("file", alias="DMX_EMAIL_BACKEND")
    email_file_dir: str = Field("./.dev-mail", alias="DMX_EMAIL_FILE_DIR")
    email_from: str = Field("DatabaseMax <no-reply@databasemax.local>", alias="DMX_EMAIL_FROM")
    smtp_host: str | None = Field(None, alias="SMTP_HOST")
    smtp_port: int = Field(587, alias="SMTP_PORT")
    smtp_username: str | None = Field(None, alias="SMTP_USERNAME")
    smtp_password: SecretStr | None = Field(None, alias="SMTP_PASSWORD")
    smtp_starttls: bool = Field(True, alias="SMTP_STARTTLS")

    # --- secrets encryption ----------------------------------------------------------------
    # JSON object {"key_id": "<base64 32-byte key>"}; DMX_SECRETS_ACTIVE_KEY selects the key used
    # for new encryptions. Older keys remain for decryption during rotation.
    secrets_keys: SecretStr = Field(
        SecretStr('{"dev1": "ZGV2LW9ubHktc2VjcmV0cy1rZXktMzJieXRlcyEhISE="}'), alias="DMX_SECRETS_KEYS"
    )
    secrets_active_key: str = Field("dev1", alias="DMX_SECRETS_ACTIVE_KEY")
    api_key_pepper: SecretStr = Field(SecretStr("dev-only-api-key-pepper"), alias="DMX_API_KEY_PEPPER")

    # --- provisioning / data plane -------------------------------------------------------------
    provisioner_url: str = Field("http://127.0.0.1:8090", alias="PROVISIONER_URL")
    provisioner_token: SecretStr = Field(SecretStr("dev-only-provisioner-token"), alias="PROVISIONER_TOKEN")
    internal_api_token: SecretStr = Field(SecretStr("dev-only-internal-token"), alias="INTERNAL_API_TOKEN")
    supported_pg_versions: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["16"], alias="DMX_PG_VERSIONS"
    )
    default_pg_version: str = Field("16", alias="DMX_DEFAULT_PG_VERSION")
    db_public_host: str = Field("localhost", alias="DMX_DB_PUBLIC_HOST")
    db_public_port: int = Field(6432, alias="DMX_DB_PUBLIC_PORT")
    db_host_suffix: str | None = Field(None, alias="DMX_DB_HOST_SUFFIX")
    # Where the worker reaches the gateway to run the post-provisioning connectivity test.
    gateway_internal_host: str = Field("127.0.0.1", alias="DMX_GATEWAY_INTERNAL_HOST")
    gateway_internal_port: int = Field(6432, alias="DMX_GATEWAY_INTERNAL_PORT")
    gateway_ca_file: str | None = Field(None, alias="DMX_GATEWAY_CA_FILE")
    gateway_status_url: str | None = Field(None, alias="DMX_GATEWAY_STATUS_URL")
    job_max_attempts: int = Field(5, alias="DMX_JOB_MAX_ATTEMPTS")
    job_lease_seconds: int = Field(120, alias="DMX_JOB_LEASE_SECONDS")
    # Backups/restores/verification can run for a long time inside one provisioner call.
    long_job_lease_seconds: int = Field(3900, alias="DMX_LONG_JOB_LEASE_SECONDS")
    backups_enabled: bool = Field(True, alias="DMX_BACKUPS_ENABLED")
    backup_schedule_interval_seconds: int = Field(300, alias="DMX_BACKUP_SCHEDULE_INTERVAL_SECONDS")
    backup_verify_enabled: bool = Field(True, alias="DMX_BACKUP_VERIFY_ENABLED")
    backup_verify_per_hour: int = Field(3, alias="DMX_BACKUP_VERIFY_PER_HOUR")
    job_backoff_base_seconds: float = Field(5.0, alias="DMX_JOB_BACKOFF_BASE_SECONDS")
    max_concurrent_jobs_per_user: int = Field(3, alias="DMX_MAX_CONCURRENT_JOBS_PER_USER")
    metrics_interval_seconds: int = Field(300, alias="DMX_METRICS_INTERVAL_SECONDS")
    reconcile_interval_seconds: int = Field(300, alias="DMX_RECONCILE_INTERVAL_SECONDS")
    orphan_grace_seconds: int = Field(900, alias="DMX_ORPHAN_GRACE_SECONDS")

    # --- billing (Paddle Billing) ------------------------------------------------------------
    paddle_api_key: SecretStr | None = Field(None, alias="PADDLE_API_KEY")
    paddle_client_token: str | None = Field(None, alias="PADDLE_CLIENT_TOKEN")
    paddle_webhook_secret: SecretStr | None = Field(None, alias="PADDLE_WEBHOOK_SECRET")
    paddle_environment: Literal["sandbox", "production"] = Field("sandbox", alias="PADDLE_ENVIRONMENT")
    paddle_price_free: str | None = Field(None, alias="PADDLE_PRICE_FREE")
    paddle_price_developer: str | None = Field(None, alias="PADDLE_PRICE_DEVELOPER")
    paddle_price_pro: str | None = Field(None, alias="PADDLE_PRICE_PRO")
    billing_grace_days: int = Field(14, alias="DMX_BILLING_GRACE_DAYS")

    # --- limits ---------------------------------------------------------------------------------
    max_request_body_bytes: int = 1_048_576
    rate_limit_enabled: bool = Field(True, alias="DMX_RATE_LIMIT_ENABLED")
    # Authenticated API budgets per minute (fail-open if Redis is down; auth endpoints fail closed).
    api_rate_per_minute_api_key: int = Field(600, alias="DMX_API_RATE_PER_MINUTE_API_KEY")
    api_rate_per_minute_user: int = Field(1200, alias="DMX_API_RATE_PER_MINUTE_USER")
    api_write_rate_per_minute: int = Field(120, alias="DMX_API_WRITE_RATE_PER_MINUTE")

    @field_validator("cors_origins", "supported_pg_versions", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("["):
                return json.loads(s)
            return [x.strip() for x in s.split(",") if x.strip()]
        return v

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        if self.environment == "production":
            weak = []
            for name in ("session_secret", "provisioner_token", "internal_api_token", "api_key_pepper"):
                val = getattr(self, name).get_secret_value()
                if val.startswith("dev-only") or len(val) < 32:
                    weak.append(name)
            if "dev1" in self.secrets_key_map():
                weak.append("secrets_keys")
            if not self.cookie_secure:
                weak.append("cookie_secure")
            if self.email_backend != "smtp":
                weak.append("email_backend")
            if weak:
                raise ValueError(f"insecure production configuration: {', '.join(weak)}")
        return self

    # --- helpers -------------------------------------------------------------------------------
    def secrets_key_map(self) -> dict[str, bytes]:
        raw = json.loads(self.secrets_keys.get_secret_value())
        out: dict[str, bytes] = {}
        for kid, b64 in raw.items():
            key = base64.b64decode(b64)
            if len(key) != 32:
                raise ValueError(f"secrets key {kid!r} must be 32 bytes")
            out[kid] = key
        return out

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret and self.google_redirect_uri)

    @property
    def paddle_enabled(self) -> bool:
        return bool(self.paddle_api_key and self.paddle_webhook_secret and self.paddle_client_token)

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
