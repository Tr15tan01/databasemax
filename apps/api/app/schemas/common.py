from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class OutModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[T](OutModel):
    items: list[T]
    total: int
    page: int
    page_size: int


class ErrorDetail(OutModel):
    code: str
    message: str
    request_id: str | None = None
    details: object | None = None


class ErrorResponse(OutModel):
    error: ErrorDetail


class Message(OutModel):
    status: str
    message: str | None = None
