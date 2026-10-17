import json
import logging

from app.logging import JsonFormatter


def test_json_logs_include_diagnostic_ids_and_redact_secret_like_values() -> None:
    record = logging.LogRecord(
        "test.logger",
        logging.WARNING,
        __file__,
        1,
        "OpenRouter failed: api_key=top-secret-value token: another-secret",
        (),
        None,
    )
    record.research_id = "research-123"
    record.event = "model_request_failed"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["research_id"] == "research-123"
    assert payload["event"] == "model_request_failed"
    assert "top-secret-value" not in payload["message"]
    assert "another-secret" not in payload["message"]
    assert "[REDACTED]" in payload["message"]
