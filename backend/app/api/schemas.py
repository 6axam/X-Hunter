from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SearchImportRequest(BaseModel):
    url: str = Field(min_length=10, max_length=4096)


class TelegramAuthRequest(BaseModel):
    init_data: str = Field(min_length=20, max_length=16384)


class TelegramAuthResponse(BaseModel):
    csrf_token: str
    expires_in: int


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
    event_settings: dict[str, Any] | None = None


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


class FindingStatePatch(BaseModel):
    saved: bool | None = None
    hidden: bool | None = None
    seen: bool | None = None
    note: str | None = Field(default=None, max_length=2000)


class FindingStateResponse(BaseModel):
    saved: bool
    hidden: bool
    seen: bool
    note: str | None


class FindingResponse(BaseModel):
    id: UUID
    title: str
    description: str | None
    price: str | None
    currency: str | None
    city: str | None
    condition: str | None
    canonical_url: str
    decision: str
    reasons: list[dict[str, Any]]
    first_seen_at: datetime
    last_seen_at: datetime
    saved: bool
    hidden: bool
    seen: bool
    note: str | None
    cover_media_id: UUID | None
    ai_value_label: str | None
    ai_summary: str | None
    searches: list[dict[str, str]]
    media: list[dict[str, Any]] | None = None


class ScanRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    search_id: UUID
    search_revision: int
    status: str
    pages_checked: int
    items_seen: int
    counters: dict[str, Any]
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None


class SettingsPatch(BaseModel):
    expected_version: int = Field(ge=1)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    quiet_hours: dict[str, Any] | None = None
    notification_mode: str | None = Field(default=None, pattern="^(immediate|digest)$")


class ExportRequest(BaseModel):
    format: str = Field(pattern="^(csv|json)$")
    saved_only: bool = False


class ExportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    format: str
    status: str
    expires_at: datetime | None


class FilterSchemaResponse(BaseModel):
    fields: list[dict[str, Any]]

    @field_validator("fields")
    @classmethod
    def fields_are_named(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if any("key" not in item for item in value):
            raise ValueError("Every filter field requires a key")
        return value
