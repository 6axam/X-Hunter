from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ScanRun, Search
from app.models.base import utcnow
from app.services.scanning import ScanService
from app.sources.base import ListingSource, SourceState
from app.sources.olx import OlxSearchSpec
from app.sources.types import ListingCandidate


class RunService:
    def __init__(self, db: AsyncSession, source: ListingSource) -> None:
        self.db = db
        self.source = source

    async def start(self, search: Search) -> ScanRun:
        active = await self.db.scalar(
            select(ScanRun).where(
                ScanRun.search_id == search.id,
                ScanRun.status.in_(["pending", "running"]),
            )
        )
        if active:
            return active
        now = utcnow()
        run = ScanRun(
            search_id=search.id,
            search_revision=search.revision,
            fencing_token=int(now.timestamp() * 1_000_000),
            lease_expires_at=now + timedelta(minutes=5),
            status="running",
            started_at=now,
            counters={},
        )
        self.db.add(run)
        await self.db.commit()
        await self.db.refresh(run)

        spec = OlxSearchSpec.model_validate(search.specification)
        result = await self.source.fetch_search_page(spec, page=1)
        if result.state not in {SourceState.COMPLETE, SourceState.PARTIAL}:
            run.status = result.state.value
            run.error_code = result.error_code
            run.finished_at = utcnow()
            await self.db.commit()
            return run

        scanner = ScanService(self.db)
        candidates = [ListingCandidate.model_validate(item) for item in (result.data or [])]
        for candidate in candidates:
            await scanner.ingest(search, candidate)
        if result.state is SourceState.COMPLETE:
            await scanner.complete_baseline(search)
        run.status = "succeeded" if result.state is SourceState.COMPLETE else "partial"
        run.pages_checked = int(result.metadata.get("pages_checked", 1))
        run.items_seen = len(candidates)
        run.counters = {"ingested": len(candidates)}
        run.finished_at = utcnow()
        await self.db.commit()
        return run
