from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class SourceState(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    UNAVAILABLE = "unavailable"
    NOT_CONFIGURED = "not_configured"


class SourceResult(BaseModel):
    state: SourceState
    data: Any | None = None
    error_code: str | None = None
    message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ListingSource(ABC):
    @abstractmethod
    def parse_search_url(self, url: str):
        raise NotImplementedError

    @abstractmethod
    def build_search_url(self, specification) -> str:
        raise NotImplementedError

    @abstractmethod
    async def fetch_search_page(self, specification, page: int = 1) -> SourceResult:
        raise NotImplementedError

    @abstractmethod
    async def fetch_listing(self, url: str) -> SourceResult:
        raise NotImplementedError

    @abstractmethod
    async def check_availability(self, url: str) -> SourceResult:
        raise NotImplementedError
