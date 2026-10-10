from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SearchImportRequest(BaseModel):
    url: str = Field(min_length=10, max_length=4096)


class SearchDraftPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    parsed_spec: dict[str, Any] | None = None
    rules: dict[str, Any] | None = None
    ai_settings: dict[str, Any] | None = None


class SearchDraftResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    original_url: str
    page_type: str
    name: str
    parsed_spec: dict[str, Any]
    opaque_parameters: dict[str, Any]
    rules: dict[str, Any]
    ai_settings: dict[str, Any]
    expires_at: datetime


class SearchCreateRequest(BaseModel):
    draft_id: UUID
    active: bool = False
    interval_minutes: int = Field(default=15, ge=10, le=10080)


class SearchPatchRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    active: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=10, le=10080)
    rules: dict[str, Any] | None = None
    ai_settings: dict[str, Any] | None = None


class SearchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    original_url: str
    current_url: str
    specification: dict[str, Any]
    opaque_parameters: dict[str, Any]
    rules: dict[str, Any]
    ai_settings: dict[str, Any]
    event_settings: dict[str, Any]
    active: bool
    revision: int
    interval_minutes: int
    next_run_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MeResponse(BaseModel):
    telegram_id: int
    timezone: str
    capabilities: dict[str, bool]


class FilterSchemaResponse(BaseModel):
    fields: list[dict[str, Any]]

    @field_validator("fields")
    @classmethod
    def fields_are_named(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if any("key" not in item for item in value):
            raise ValueError("Every filter field requires a key")
        return value
