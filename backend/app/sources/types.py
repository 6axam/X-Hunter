from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class ListingCandidate(BaseModel):
    source_listing_id: str
    canonical_url: str
    title: str
    description: str | None = None
    price: Decimal | None = None
    currency: str | None = None
    raw_price: str | None = None
    price_type: str = "unknown"
    city: str | None = None
    condition: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)
    image_urls: list[str] = Field(default_factory=list)
    published_at: datetime | None = None
    promoted: bool = False
