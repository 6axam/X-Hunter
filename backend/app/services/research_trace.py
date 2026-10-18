from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.config import Settings
from app.models import ResearchItem, ResearchSession, ResearchTraceEvent, User
from app.models.base import utcnow

TRACE_SCHEMA_VERSION = "1.0"
REDACTED = "[REDACTED]"

_SECRET_KEYS = {
    "api_key",
    "authorization",
    "cookie",
    "cookies",
    "init_data",
    "password",
    "proxy_authorization",
    "refresh_token",
    "secret",
    "session_secret",
    "set_cookie",
    "telegram_bot_token",
    "access_token",
}
_PERSONAL_KEYS = {
    "contact",
    "email",
    "phone",
    "seller_last_seen",
    "seller_member_since",
    "seller_name",
    "seller_profile_url",
    "username",
}
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\s().-]*){8,}\d(?!\w)")
_SECRET_TEXT_RE = re.compile(
    r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{12,}|"
    r"((?:sk|key|token)[-_][A-Za-z0-9._-]{12,})"
)
_URL_KEYS = {"access_token", "api_key", "auth", "authorization", "key", "signature", "token"}


def _safe_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if parsed.scheme not in {"http", "https"}:
        return value
    query = [
        (key, REDACTED if key.casefold() in _URL_KEYS else item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    host = parsed.hostname or ""
    try:
        parsed_port = parsed.port
    except ValueError:
        parsed_port = None
    port = f":{parsed_port}" if parsed_port else ""
    return urlunsplit((parsed.scheme, f"{host}{port}", parsed.path, urlencode(query), ""))


def _mask_text(value: str, max_chars: int) -> str:
    value = _EMAIL_RE.sub("[REDACTED_EMAIL]", value)
    value = _PHONE_RE.sub("[REDACTED_PHONE]", value)
    value = _SECRET_TEXT_RE.sub(REDACTED, value)
    if value.startswith(("http://", "https://")):
        value = _safe_url(value)
    if len(value) > max_chars:
        return f"{value[:max_chars]}…[TRUNCATED {len(value) - max_chars} chars]"
    return value


def sanitize_trace_value(
    value: Any,
    *,
    verbose: bool = True,
    max_string_chars: int = 12_000,
    depth: int = 0,
) -> Any:
    """Return JSON-safe diagnostic data with secrets and seller PII removed."""
    if depth > 10:
        return "[TRUNCATED_DEPTH]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (Decimal, UUID, datetime, date, Enum)):
        return str(value)
    if isinstance(value, str):
        return _mask_text(value, max_string_chars if verbose else min(max_string_chars, 1200))
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        limit = 200 if verbose else 60
        for index, (raw_key, item) in enumerate(value.items()):
            if index >= limit:
                result["_truncated_keys"] = len(value) - limit
                break
            key = str(raw_key)
            normalized = key.casefold().replace("-", "_")
            if normalized in _SECRET_KEYS or normalized.endswith(("_api_key", "_secret")):
                result[key] = REDACTED
            elif normalized in _PERSONAL_KEYS:
                result[key] = "[REDACTED_PERSONAL_DATA]"
            elif normalized == "headers":
                result[key] = "[REDACTED_HEADERS]"
            else:
                result[key] = sanitize_trace_value(
                    item,
                    verbose=verbose,
                    max_string_chars=max_string_chars,
                    depth=depth + 1,
                )
        return result
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        limit = 300 if verbose else 50
        result = [
            sanitize_trace_value(
                item,
                verbose=verbose,
                max_string_chars=max_string_chars,
                depth=depth + 1,
            )
            for item in items[:limit]
        ]
        if len(items) > limit:
            result.append({"_truncated_items": len(items) - limit})
        return result
    return _mask_text(str(value), max_string_chars)


def bounded_trace_payload(payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
    safe = sanitize_trace_value(payload, verbose=settings.research_trace_verbose)
    encoded = json.dumps(safe, ensure_ascii=False, separators=(",", ":")).encode()
    if len(encoded) <= settings.research_trace_max_payload_bytes:
        return safe
    digest = hashlib.sha256(encoded).hexdigest()
    preview_limit = max(512, settings.research_trace_max_payload_bytes // 4)
    preview = _mask_text(encoded.decode(errors="replace"), preview_limit)
    return {
        "_truncated": True,
        "original_bytes": len(encoded),
        "sha256": digest,
        "preview": preview,
    }


class ResearchTraceRecorder:
    """Adds trace rows to the caller's existing transaction; it never commits on its own."""

    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    async def record(
        self,
        session: ResearchSession,
        *,
        stage: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        round_id: str | int | None = None,
        query_id: str | None = None,
        listing_id: str | UUID | None = None,
        duration_ms: int | None = None,
    ) -> ResearchTraceEvent | None:
        if session.trace_event_count >= self.settings.research_trace_max_events:
            session.trace_truncated = True
            return None
        safe_payload = bounded_trace_payload(payload or {}, self.settings)
        payload_bytes = len(
            json.dumps(safe_payload, ensure_ascii=False, separators=(",", ":")).encode()
        )
        projected_bytes = session.trace_payload_bytes + payload_bytes
        if projected_bytes > self.settings.research_trace_max_total_bytes:
            session.trace_truncated = True
            return None
        session.trace_event_count += 1
        session.trace_payload_bytes += payload_bytes
        event = ResearchTraceEvent(
            session_id=session.id,
            sequence=session.trace_event_count,
            round_id=str(round_id) if round_id is not None else None,
            query_id=query_id,
            listing_id=str(listing_id) if listing_id is not None else None,
            occurred_at=utcnow(),
            stage=stage[:80],
            event_type=event_type[:120],
            duration_ms=max(duration_ms, 0) if duration_ms is not None else None,
            payload=safe_payload,
        )
        self.db.add(event)
        return event

    async def contradiction(
        self,
        session: ResearchSession,
        *,
        code: str,
        message: str,
        evidence: dict[str, Any],
        listing_id: str | UUID | None = None,
        round_id: str | int | None = None,
    ) -> None:
        await self.record(
            session,
            stage="diagnostics",
            event_type="logical_contradiction",
            listing_id=listing_id,
            round_id=round_id,
            payload={"code": code, "message": message, "evidence": evidence},
        )


class ResearchTraceExportService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    async def build(self, owner: User, session_id: UUID) -> tuple[bytes, str]:
        session = await self.db.scalar(
            select(ResearchSession).where(
                ResearchSession.id == session_id,
                ResearchSession.owner_id == owner.id,
            )
        )
        if session is None:
            raise AppError(
                status_code=404,
                code="research_not_found",
                message="Исследование не найдено.",
            )
        events = list(
            await self.db.scalars(
                select(ResearchTraceEvent)
                .where(ResearchTraceEvent.session_id == session.id)
                .order_by(ResearchTraceEvent.sequence)
            )
        )
        items = list(
            await self.db.scalars(
                select(ResearchItem)
                .where(ResearchItem.session_id == session.id)
                .order_by(ResearchItem.sequence)
            )
        )
        trace_rows = [self._event_dict(session, event) for event in events]
        trace_jsonl = "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in trace_rows
        ).encode()
        event_counts = Counter(row["event_type"] for row in trace_rows)
        stage_counts = Counter(row["stage"] for row in trace_rows)
        contradictions = [row for row in trace_rows if row["event_type"] == "logical_contradiction"]
        summary = sanitize_trace_value(
            {
                "schema_version": TRACE_SCHEMA_VERSION,
                "research": {
                    "research_id": session.id,
                    "query": session.query,
                    "model": session.model,
                    "status": session.status,
                    "stage": session.stage,
                    "created_at": session.created_at,
                    "started_at": session.started_at,
                    "finished_at": session.finished_at,
                    "current_round": session.current_round,
                    "counts": {
                        "collected": session.collected_count,
                        "processed": session.processed_count,
                        "accepted": session.accepted_count,
                        "rejected": session.rejected_count,
                        "rejected_before_analysis": session.rejected_before_analysis_count,
                    },
                    "cost_usd": session.actual_cost_usd,
                    "technical_reasoning": session.technical_reasoning,
                    "product_hypotheses": session.product_hypotheses,
                    "search_plan": session.search_plan,
                    "research_rounds": session.research_rounds,
                    "supervisor_decisions": session.supervisor_decisions,
                    "market_statistics": session.market_statistics,
                    "final_comparison": session.final_comparison,
                    "stage_usage": session.stage_usage,
                },
                "items": [
                    {
                        "listing_id": item.listing_id,
                        "sequence": item.sequence,
                        "status": item.status,
                        "score": item.score,
                        "confidence": item.confidence,
                        "recommendation": item.recommendation,
                        "listing_snapshot": item.listing_snapshot,
                        "analysis": item.analysis,
                        "tokens": {
                            "input": item.input_tokens,
                            "output": item.output_tokens,
                            "cached_input": item.cached_input_tokens,
                        },
                        "cost_usd": item.actual_cost_usd,
                        "error_code": item.error_code,
                    }
                    for item in items
                ],
                "trace": {
                    "event_count": len(trace_rows),
                    "payload_bytes": session.trace_payload_bytes,
                    "truncated": session.trace_truncated,
                    "event_types": dict(event_counts),
                    "stages": dict(stage_counts),
                    "contradiction_count": len(contradictions),
                    "contradictions": [row["payload"] for row in contradictions],
                },
            },
            verbose=self.settings.research_trace_verbose,
        )
        summary_bytes = json.dumps(summary, ensure_ascii=False, indent=2).encode()
        report_bytes = self._report(session, len(trace_rows), event_counts, contradictions).encode()
        files = {
            "trace.jsonl": trace_jsonl,
            "summary.json": summary_bytes,
            "report.md": report_bytes,
        }
        manifest = {
            "schema_version": TRACE_SCHEMA_VERSION,
            "exported_at": utcnow().isoformat(),
            "research_id": str(session.id),
            "privacy": {
                "secrets_redacted": True,
                "seller_personal_data_redacted": True,
                "hidden_model_reasoning_included": False,
                "structured_model_outputs_included": True,
            },
            "trace": {
                "verbose": self.settings.research_trace_verbose,
                "truncated": session.trace_truncated,
                "retention_days": self.settings.research_trace_retention_days,
                "stored_payload_bytes": session.trace_payload_bytes,
                "limits": {
                    "max_events": self.settings.research_trace_max_events,
                    "max_payload_bytes": self.settings.research_trace_max_payload_bytes,
                    "max_total_bytes": self.settings.research_trace_max_total_bytes,
                },
            },
            "files": {
                name: {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
                for name, content in files.items()
            },
        }
        files["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for name, content in files.items():
                output.writestr(name, content)
        filename = f"x-hunter-research-{session.id}-diagnostics.zip"
        return archive.getvalue(), filename

    def _event_dict(self, session: ResearchSession, event: ResearchTraceEvent) -> dict[str, Any]:
        return {
            "schema_version": TRACE_SCHEMA_VERSION,
            "research_id": str(session.id),
            "round_id": event.round_id,
            "query_id": event.query_id,
            "listing_id": event.listing_id,
            "timestamp": event.occurred_at.isoformat(),
            "stage": event.stage,
            "event_type": event.event_type,
            "duration_ms": event.duration_ms,
            "sequence": event.sequence,
            "payload": sanitize_trace_value(
                event.payload, verbose=self.settings.research_trace_verbose
            ),
        }

    @staticmethod
    def _report(
        session: ResearchSession,
        event_count: int,
        event_counts: Counter[str],
        contradictions: list[dict[str, Any]],
    ) -> str:
        status = "ограничена лимитом" if session.trace_truncated else "полная в пределах retention"
        contradiction_lines = (
            "\n".join(
                f"- `{row['payload'].get('code', 'unknown')}`: {row['payload'].get('message', '')}"
                for row in contradictions
            )
            or "- Не обнаружены."
        )
        return (
            "# X-Hunter AI Research diagnostic report\n\n"
            f"- Research ID: `{session.id}`\n"
            f"- Status: `{session.status}` / `{session.stage}`\n"
            f"- Trace: {status}, {event_count} событий\n"
            f"- Rounds: {session.current_round}\n"
            f"- Evaluated: {session.processed_count}, shortlisted: {session.accepted_count}\n"
            f"- Actual cost: ${session.actual_cost_usd}\n\n"
            "## Logical contradictions\n\n"
            f"{contradiction_lines}\n\n"
            "## Event coverage\n\n"
            + "\n".join(f"- `{key}`: {value}" for key, value in sorted(event_counts.items()))
            + "\n\n"
            "Архив содержит только структурированные входы, ответы и обоснования. "
            "Скрытое внутреннее рассуждение модели не записывается.\n"
        )
