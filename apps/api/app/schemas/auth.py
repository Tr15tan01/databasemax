from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import EmailStr, Field, field_validator

from app.schemas.common import ApiModel, OutModel


class RegisterIn(ApiModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    display_name: str | None = Field(None, max_length=120)
    accept_terms: bool = True

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.strip().lower()


class LoginIn(ApiModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.strip().lower()


class TokenIn(ApiModel):
    token: str = Field(min_length=10, max_length=200)


class ForgotIn(ApiModel):
    email: EmailStr

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.strip().lower()


class ResetIn(ApiModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordIn(ApiModel):
    current_password: str | None = Field(None, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class ReauthIn(ApiModel):
    password: str = Field(min_length=1, max_length=256)


class ProvidersOut(OutModel):
    password: bool = True
    google: bool


class PlanSummary(OutModel):
    id: str
    name: str
    status: str


class MeOut(OutModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    email_verified: bool
    has_password: bool
    is_admin: bool
    created_at: dt.datetime
    preferences: dict[str, Any]
    plan: PlanSummary
    auth_method: str | None = None
    linked_providers: list[str]


class SessionOut(OutModel):
    id: uuid.UUID
    created_at: dt.datetime
    last_seen_at: dt.datetime
    expires_at: dt.datetime
    ip: str | None
    user_agent: str | None
    auth_method: str
    current: bool


class AccountUpdateIn(ApiModel):
    display_name: str | None = Field(None, max_length=120)
    preferences: dict[str, Any] | None = None

    @field_validator("preferences")
    @classmethod
    def _prefs(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is None:
            return v
        allowed = {"email_notifications", "theme", "usage_alert_thresholds"}
        extra = set(v) - allowed
        if extra:
            raise ValueError(f"unknown preference keys: {sorted(extra)}")
        if "theme" in v and v["theme"] not in ("system", "light", "dark"):
            raise ValueError("theme must be system, light or dark")
        if "email_notifications" in v:
            en = v["email_notifications"]
            if not isinstance(en, dict) or not all(isinstance(x, bool) for x in en.values()):
                raise ValueError("email_notifications must map notification types to booleans")
        if "usage_alert_thresholds" in v:
            th = v["usage_alert_thresholds"]
            if not isinstance(th, list) or not all(isinstance(x, int) and 1 <= x <= 100 for x in th) or len(th) > 5:
                raise ValueError("usage_alert_thresholds must be up to 5 integers between 1 and 100")
        return v


class DeleteAccountIn(ApiModel):
    confirm: str = Field(max_length=320)
