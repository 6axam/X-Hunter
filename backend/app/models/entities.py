from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import JSON as SAJSON
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UuidPrimaryKeyMixin


class User(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Kyiv")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    settings: Mapped[dict[str, Any]] = mapped_column(SAJSON, default=dict)


class SearchDraft(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "search_drafts"

    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    original_url: Mapped[str] = mapped_column(Text)
    page_type: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(160), default="Новый поиск")
    parsed_spec: Mapped[dict[str, Any]] = mapped_column(SAJSON, default=dict)
    opaque_parameters: Mapped[dict[str, Any]] = mapped_column(SAJSON, default=dict)
    rules: Mapped[dict[str, Any]] = mapped_column(SAJSON, default=dict)
    ai_settings: Mapped[dict[str, Any]] = mapped_column(SAJSON, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    owner: Mapped[User] = relationship()


class Search(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "searches"
    __table_args__ = (Index("ix_search_owner_active_next", "owner_id", "active", "next_run_at"),)

    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    original_url: Mapped[str] = mapped_column(Text)
    current_url: Mapped[str] = mapped_column(Text)
    specification: Mapped[dict[str, Any]] = mapped_column(SAJSON, default=dict)
    opaque_parameters: Mapped[dict[str, Any]] = mapped_column(SAJSON, default=dict)
    rules: Mapped[dict[str, Any]] = mapped_column(SAJSON, default=dict)
    ai_settings: Mapped[dict[str, Any]] = mapped_column(SAJSON, default=dict)
    event_settings: Mapped[dict[str, Any]] = mapped_column(SAJSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=15)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    baseline_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    owner: Mapped[User] = relationship()


class Listing(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("source", "market", "source_listing_id"),
        Index("ix_listing_last_seen", "last_seen_at"),
    )

    source: Mapped[str] = mapped_column(String(32), default="olx")
    market: Mapped[str] = mapped_column(String(16), default="ua")
    source_listing_id: Mapped[str] = mapped_column(String(128))
    canonical_url: Mapped[str] = mapped_column(Text)
    current_version_id: Mapped[UUID | None] = mapped_column(nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    unavailable_checks: Mapped[int] = mapped_column(Integer, default=0)


class ListingVersion(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "listing_versions"
    __table_args__ = (UniqueConstraint("listing_id", "content_hash"),)

    listing_id: Mapped[UUID] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), index=True
    )
    content_hash: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    raw_price: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_type: Mapped[str] = mapped_column(String(32), default="unknown")
    city: Mapped[str | None] = mapped_column(String(160), nullable=True)
    condition: Mapped[str | None] = mapped_column(String(80), nullable=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(SAJSON, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    listing: Mapped[Listing] = relationship()


class SearchMatch(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "search_matches"
    __table_args__ = (UniqueConstraint("search_id", "listing_id"),)

    search_id: Mapped[UUID] = mapped_column(
        ForeignKey("searches.id", ondelete="CASCADE"), index=True
    )
    listing_id: Mapped[UUID] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), index=True
    )
    search_revision: Mapped[int] = mapped_column(Integer)
    listing_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("listing_versions.id", ondelete="CASCADE")
    )
    decision: Mapped[str] = mapped_column(String(32), index=True)
    operation_state: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    reasons: Mapped[list[dict[str, Any]]] = mapped_column(SAJSON, default=list)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UserListingState(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_listing_state"
    __table_args__ = (UniqueConstraint("owner_id", "listing_id"),)

    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    listing_id: Mapped[UUID] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), index=True
    )
    saved: Mapped[bool] = mapped_column(Boolean, default=False)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    seen: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class AiEvaluation(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ai_evaluations"
    __table_args__ = (UniqueConstraint("cache_key"),)

    listing_id: Mapped[UUID] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), index=True
    )
    search_id: Mapped[UUID] = mapped_column(
        ForeignKey("searches.id", ondelete="CASCADE"), index=True
    )
    cache_key: Mapped[str] = mapped_column(String(160))
    schema_version: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(160))
    instruction_hash: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), index=True)
    decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    value_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    result: Mapped[dict[str, Any]] = mapped_column(SAJSON, default=dict)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)


class ResearchSession(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """A resumable, owner-scoped AI buying research run."""

    __tablename__ = "research_sessions"
    __table_args__ = (Index("ix_research_owner_status", "owner_id", "status", "updated_at"),)

    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    query: Mapped[str] = mapped_column(Text)
    marketplaces: Mapped[list[str]] = mapped_column(SAJSON, default=list)
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(80), default="queued")
    max_items: Mapped[int] = mapped_column(Integer, default=20)
    cursor: Mapped[int] = mapped_column(Integer, default=0)
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    accepted_count: Mapped[int] = mapped_column(Integer, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0)
    rejected_before_analysis_count: Mapped[int] = mapped_column(Integer, default=0)
    collected_count: Mapped[int] = mapped_column(Integer, default=0)
    search_plan: Mapped[dict[str, Any]] = mapped_column(SAJSON, default=dict)
    executed_searches: Mapped[list[dict[str, Any]]] = mapped_column(SAJSON, default=list)
    compact_memory: Mapped[dict[str, Any]] = mapped_column(SAJSON, default=dict)
    # Agent v2 keeps public, checkable conclusions only.  The fields are JSON so
    # older sessions remain readable after migration and every completed stage is
    # durable before the next model/OLX call starts.
    technical_reasoning: Mapped[dict[str, Any]] = mapped_column(SAJSON, default=dict)
    product_hypotheses: Mapped[list[dict[str, Any]]] = mapped_column(SAJSON, default=list)
    research_rounds: Mapped[list[dict[str, Any]]] = mapped_column(SAJSON, default=list)
    supervisor_decisions: Mapped[list[dict[str, Any]]] = mapped_column(SAJSON, default=list)
    market_statistics: Mapped[dict[str, Any]] = mapped_column(SAJSON, default=dict)
    final_comparison: Mapped[dict[str, Any]] = mapped_column(SAJSON, default=dict)
    stage_usage: Mapped[dict[str, Any]] = mapped_column(SAJSON, default=dict)
    current_round: Mapped[int] = mapped_column(Integer, default=0)
    budget_usd: Mapped[Decimal] = mapped_column(Numeric(14, 6), default=Decimal("0.10"))
    estimated_cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 6), default=Decimal("0"))
    actual_cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 6), default=Decimal("0"))
    planning_estimated_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(14, 6), default=Decimal("0")
    )
    planning_actual_cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 6), default=Decimal("0"))
    planning_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    planning_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ResearchItem(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "research_items"
    __table_args__ = (
        UniqueConstraint("session_id", "listing_id"),
        Index("ix_research_items_session_rank", "session_id", "score", "created_at"),
    )

    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_sessions.id", ondelete="CASCADE"), index=True
    )
    listing_id: Mapped[UUID] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    recommendation: Mapped[str | None] = mapped_column(String(32), nullable=True)
    listing_snapshot: Mapped[dict[str, Any]] = mapped_column(SAJSON, default=dict)
    analysis: Mapped[dict[str, Any]] = mapped_column(SAJSON, default=dict)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    actual_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)


class ResearchEvent(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "research_events"
    __table_args__ = (Index("ix_research_events_session_created", "session_id", "created_at"),)

    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_sessions.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(48), index=True)
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(SAJSON, default=dict)


class Media(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "media"
    __table_args__ = (UniqueConstraint("listing_id", "position"),)

    listing_id: Mapped[UUID] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), index=True
    )
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    safe_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    byte_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    position: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    telegram_file_id: Mapped[str | None] = mapped_column(Text, nullable=True)


class ScanRun(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "scan_runs"
    __table_args__ = (Index("ix_scan_search_status_created", "search_id", "status", "created_at"),)

    search_id: Mapped[UUID] = mapped_column(
        ForeignKey("searches.id", ondelete="CASCADE"), index=True
    )
    search_revision: Mapped[int] = mapped_column(Integer)
    fencing_token: Mapped[int] = mapped_column(BigInteger)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), index=True)
    pages_checked: Mapped[int] = mapped_column(Integer, default=0)
    items_seen: Mapped[int] = mapped_column(Integer, default=0)
    counters: Mapped[dict[str, Any]] = mapped_column(SAJSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)


class NotificationEvent(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notification_events"
    __table_args__ = (UniqueConstraint("dedup_key"),)

    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    listing_id: Mapped[UUID] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), index=True
    )
    search_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("searches.id", ondelete="CASCADE"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    dedup_key: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict[str, Any]] = mapped_column(SAJSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)


class DeliveryAttempt(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "delivery_attempts"

    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("notification_events.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[str] = mapped_column(String(32), default="telegram")
    destination: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(32), index=True)
    message_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    retry_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uncertain_delivery: Mapped[bool] = mapped_column(Boolean, default=False)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)


class BudgetLedger(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "budget_ledger"
    __table_args__ = (Index("ix_budget_owner_period", "owner_id", "period_start", "period_end"),)

    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    search_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("searches.id", ondelete="CASCADE"), nullable=True, index=True
    )
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reserved_usd: Mapped[Decimal] = mapped_column(Numeric(14, 6), default=Decimal("0"))
    actual_usd: Mapped[Decimal] = mapped_column(Numeric(14, 6), default=Decimal("0"))
    uncertain_usd: Mapped[Decimal] = mapped_column(Numeric(14, 6), default=Decimal("0"))


class ExportJob(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "export_jobs"

    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    format: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32), index=True)
    safe_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    nonce_hash: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
