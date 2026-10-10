import asyncio
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import User
from app.services.exports import ExportService, csv_safe
from app.services.scanning import ScanService
from app.sources.types import ListingCandidate
from tests.test_scanning import make_search


def test_csv_formula_values_are_neutralized() -> None:
    assert csv_safe("=HYPERLINK('bad')").startswith("'")
    assert csv_safe(" normal") == " normal"


@pytest.mark.asyncio
async def test_csv_and_json_exports_contain_real_findings(
    db_session: AsyncSession, tmp_path
) -> None:
    owner = User(telegram_id=111)
    db_session.add(owner)
    await db_session.commit()
    await db_session.refresh(owner)
    search = await make_search(db_session, owner)
    search.rules = {"required_groups": []}
    await db_session.commit()
    await ScanService(db_session).ingest(
        search,
        ListingCandidate(
            source_listing_id="IDexport",
            canonical_url="https://www.olx.ua/d/uk/obyavlenie/item-IDexport.html",
            title="=HYPERLINK('https://bad.test')",
            price=Decimal("100"),
            currency="UAH",
        ),
    )
    service = ExportService(db_session, Settings(export_root=tmp_path))
    csv_job = await service.create(owner, "csv", False)
    json_job = await service.create(owner, "json", False)

    csv_text = await asyncio.to_thread(Path(csv_job.safe_path).read_text, encoding="utf-8-sig")
    json_text = await asyncio.to_thread(Path(json_job.safe_path).read_text, encoding="utf-8")
    assert "'=HYPERLINK" in csv_text
    assert '"schema_version": 1' in json_text
    assert "IDexport" in json_text
