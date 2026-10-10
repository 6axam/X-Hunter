from __future__ import annotations

import csv
import json
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import ExportJob, User
from app.models.base import utcnow
from app.services.findings import FindingService


def csv_safe(value: object) -> str:
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{text}"
    return text


class ExportService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    async def create(self, owner: User, format_: str, saved_only: bool) -> ExportJob:
        job = ExportJob(
            owner_id=owner.id,
            format=format_,
            status="running",
            expires_at=utcnow() + timedelta(hours=24),
        )
        self.db.add(job)
        await self.db.commit()
        await self.db.refresh(job)
        root = self.settings.export_root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{job.id}.{format_}"
        findings = await FindingService(self.db).list(
            owner, saved=True if saved_only else None, status="all"
        )
        if format_ == "json":
            payload = {
                "schema_version": 1,
                "exported_at": utcnow().isoformat(),
                "findings": findings,
            }
            path.write_text(json.dumps(payload, ensure_ascii=False, default=str, indent=2))
        elif format_ == "csv":
            self._write_csv(path, findings)
        else:
            raise ValueError("unsupported export format")
        job.safe_path = str(path)
        job.status = "ready"
        await self.db.commit()
        return job

    @staticmethod
    def _write_csv(path, findings: list[dict]) -> None:
        fields = [
            "title",
            "price",
            "currency",
            "city",
            "condition",
            "canonical_url",
            "searches",
            "decision",
            "ai_value_label",
            "first_seen_at",
            "saved",
        ]
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for finding in findings:
                row = {key: finding.get(key) for key in fields}
                row["searches"] = ", ".join(item["name"] for item in finding["searches"])
                writer.writerow({key: csv_safe(value) for key, value in row.items()})
