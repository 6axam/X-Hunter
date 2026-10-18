from __future__ import annotations

import io
import json
import zipfile
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.config import Settings
from app.models import ResearchSession, ResearchTraceEvent, User
from app.services.research_trace import ResearchTraceExportService, ResearchTraceRecorder


async def _research_session(
    db: AsyncSession, telegram_id: int = 9001
) -> tuple[User, ResearchSession]:
    owner = User(telegram_id=telegram_id)
    db.add(owner)
    await db.flush()
    session = ResearchSession(
        owner_id=owner.id,
        query="мини-ПК до 5000 грн",
        marketplaces=["olx"],
        model="vendor/model",
        status="running",
        stage="deep_analysis",
        budget_usd=Decimal("0.10"),
    )
    db.add(session)
    await db.flush()
    return owner, session


@pytest.mark.asyncio
async def test_diagnostic_export_contains_required_files_and_jsonl_fields(
    db_session: AsyncSession,
) -> None:
    owner, session = await _research_session(db_session)
    trace = ResearchTraceRecorder(db_session, Settings())
    await trace.record(
        session,
        stage="deep_analysis",
        event_type="deep_analysis_completed",
        round_id=2,
        query_id="r2-q1",
        listing_id="OLX-123",
        duration_ms=87,
        payload={"score": 81, "recommendation": "shortlist", "reason": "Intel confirmed"},
    )
    await trace.contradiction(
        session,
        code="example_inconsistency",
        message="Diagnostic evidence only.",
        evidence={"score": 7, "recommendation": "shortlist"},
        round_id=2,
        listing_id="OLX-123",
    )
    await db_session.commit()

    archive, filename = await ResearchTraceExportService(db_session, Settings()).build(
        owner, session.id
    )

    assert filename == f"x-hunter-research-{session.id}-diagnostics.zip"
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        assert set(package.namelist()) == {
            "trace.jsonl",
            "summary.json",
            "manifest.json",
            "report.md",
        }
        rows = [json.loads(line) for line in package.read("trace.jsonl").splitlines()]
        required = {
            "research_id",
            "round_id",
            "query_id",
            "listing_id",
            "timestamp",
            "stage",
            "event_type",
            "duration_ms",
        }
        assert rows and all(required <= row.keys() for row in rows)
        assert [row["sequence"] for row in rows] == [1, 2]
        summary = json.loads(package.read("summary.json"))
        manifest = json.loads(package.read("manifest.json"))
        assert summary["trace"]["event_count"] == 2
        assert summary["trace"]["contradiction_count"] == 1
        assert manifest["research_id"] == str(session.id)
        assert manifest["privacy"]["hidden_model_reasoning_included"] is False
        assert "example_inconsistency" in package.read("report.md").decode()


@pytest.mark.asyncio
async def test_trace_masks_secrets_headers_urls_and_seller_personal_data(
    db_session: AsyncSession,
) -> None:
    owner, session = await _research_session(db_session, telegram_id=9002)
    trace = ResearchTraceRecorder(db_session, Settings())
    await trace.record(
        session,
        stage="olx",
        event_type="privacy_probe",
        payload={
            "api_key": "sk-ultra-secret-value-123456",
            "authorization": "Bearer ultra-secret-bearer-value",
            "headers": {"X-Private": "must-never-export"},
            "seller_name": "Sensitive Seller Name",
            "description": "Write private.person@example.com or call +380 67 123 45 67",
            "requested_url": "https://www.olx.ua/list/?token=private-query-token&q=mini-pc",
        },
    )
    await db_session.commit()

    archive, _ = await ResearchTraceExportService(db_session, Settings()).build(owner, session.id)
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        exported = "\n".join(
            package.read(name).decode(errors="replace") for name in package.namelist()
        )

    for private_value in (
        "sk-ultra-secret-value-123456",
        "ultra-secret-bearer-value",
        "must-never-export",
        "Sensitive Seller Name",
        "private.person@example.com",
        "+380 67 123 45 67",
        "private-query-token",
    ):
        assert private_value not in exported
    assert "[REDACTED]" in exported
    assert "[REDACTED_PERSONAL_DATA]" in exported
    assert "[REDACTED_EMAIL]" in exported
    assert "[REDACTED_PHONE]" in exported


@pytest.mark.asyncio
async def test_trace_sequence_and_checkpoint_survive_session_reload(
    db_session: AsyncSession,
) -> None:
    _, session = await _research_session(db_session, telegram_id=9003)
    settings = Settings()
    await ResearchTraceRecorder(db_session, settings).record(
        session,
        stage="persistence",
        event_type="round_checkpoint_saved",
        round_id=1,
        payload={"cursor": 3},
    )
    session_id = session.id
    await db_session.commit()
    db_session.expunge_all()

    restored = await db_session.get(ResearchSession, session_id)
    assert restored is not None
    await ResearchTraceRecorder(db_session, settings).record(
        restored,
        stage="persistence",
        event_type="worker_resumed",
        round_id=1,
        payload={"cursor": restored.cursor},
    )
    await db_session.commit()

    events = list(
        await db_session.scalars(
            select(ResearchTraceEvent)
            .where(ResearchTraceEvent.session_id == session_id)
            .order_by(ResearchTraceEvent.sequence)
        )
    )
    await db_session.refresh(restored)
    assert [event.sequence for event in events] == [1, 2]
    assert [event.event_type for event in events] == [
        "round_checkpoint_saved",
        "worker_resumed",
    ]
    assert restored.trace_event_count == 2
    assert restored.trace_payload_bytes > 0


@pytest.mark.asyncio
async def test_trace_volume_limit_marks_session_as_truncated(db_session: AsyncSession) -> None:
    _, session = await _research_session(db_session, telegram_id=9006)
    recorder = ResearchTraceRecorder(
        db_session,
        Settings(research_trace_max_events=1, research_trace_max_total_bytes=1024),
    )
    first = await recorder.record(
        session,
        stage="persistence",
        event_type="first",
        payload={"checkpoint": 1},
    )
    second = await recorder.record(
        session,
        stage="persistence",
        event_type="second",
        payload={"checkpoint": 2},
    )
    await db_session.commit()

    assert first is not None
    assert second is None
    assert session.trace_event_count == 1
    assert session.trace_truncated is True


@pytest.mark.asyncio
async def test_diagnostic_export_is_owner_scoped(db_session: AsyncSession) -> None:
    owner, session = await _research_session(db_session, telegram_id=9004)
    other = User(telegram_id=9005)
    db_session.add(other)
    await db_session.commit()

    with pytest.raises(AppError) as error:
        await ResearchTraceExportService(db_session, Settings()).build(other, session.id)

    assert error.value.status_code == 404
    assert error.value.code == "research_not_found"
    archive, _ = await ResearchTraceExportService(db_session, Settings()).build(owner, session.id)
    assert zipfile.is_zipfile(io.BytesIO(archive))
