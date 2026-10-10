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
    def __init__(self, db: AsyncSession, source: ListingSource, max_pages: int = 3) -> None:
        self.db = db
        self.source = source
        self.max_pages = max(1, max_pages)

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
        candidates_by_id: dict[str, ListingCandidate] = {}
        overall_state = SourceState.COMPLETE
        for page in range(1, self.max_pages + 1):
            result = await self.source.fetch_search_page(spec, page=page)
            if result.state not in {SourceState.COMPLETE, SourceState.PARTIAL}:
                if page == 1:
                    run.status = result.state.value
                    run.error_code = result.error_code
                    run.finished_at = utcnow()
                    await self.db.commit()
                    return run
                overall_state = SourceState.PARTIAL
                run.error_code = result.error_code
                break
            page_candidates = [
                ListingCandidate.model_validate(item) for item in (result.data or [])
            ]
            run.pages_checked += 1
            for candidate in page_candidates:
                candidates_by_id[candidate.source_listing_id] = candidate
            if result.state is SourceState.PARTIAL:
                overall_state = SourceState.PARTIAL
                run.error_code = result.error_code
                break
            if not page_candidates:
                break

        scanner = ScanService(self.db)
        candidates = list(candidates_by_id.values())
        for candidate in candidates:
            details = await self.source.fetch_listing(candidate.canonical_url)
            if details.state is SourceState.COMPLETE and details.data:
                candidate = ListingCandidate.model_validate(details.data)
            await scanner.ingest(search, candidate)
        if overall_state is SourceState.COMPLETE:
            await scanner.complete_baseline(search)
        run.status = "succeeded" if overall_state is SourceState.COMPLETE else "partial"
        run.items_seen = len(candidates)
        run.counters = {"ingested": len(candidates)}
        run.finished_at = utcnow()
        await self.db.commit()
        return run
