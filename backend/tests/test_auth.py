import hashlib
import hmac
import json
from urllib.parse import urlencode

import pytest

from app.api.errors import AppError
from app.services.auth import SessionCodec, validate_telegram_init_data


def signed_init_data(bot_token: str, telegram_id: int, auth_date: int) -> str:
    values = {
        "auth_date": str(auth_date),
        "query_id": "AAE-test",
        "user": json.dumps({"id": telegram_id, "first_name": "Owner"}, separators=(",", ":")),
    }
    check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def test_telegram_init_data_signature_and_age() -> None:
    raw = signed_init_data("123:token", 111, 1_000_000)
    identity = validate_telegram_init_data(raw, "123:token", now=1_000_100)
    assert identity.telegram_id == 111

    with pytest.raises(AppError) as expired:
        validate_telegram_init_data(raw, "123:token", now=1_001_000)
    assert expired.value.code == "expired_init_data"


def test_telegram_init_data_rejects_tampering() -> None:
    raw = signed_init_data("123:token", 111, 1_000_000).replace("Owner", "Intruder")
    with pytest.raises(AppError) as invalid:
        validate_telegram_init_data(raw, "123:token", now=1_000_001)
    assert invalid.value.code == "invalid_init_data"


def test_telegram_init_data_includes_signature_in_hmac_payload() -> None:
    bot_token = "123:token"
    values = {
        "auth_date": "1000000",
        "query_id": "AAE-test",
        "signature": "telegram-ed25519-signature",
        "user": json.dumps({"id": 111, "first_name": "Owner"}, separators=(",", ":")),
    }
    check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()

    identity = validate_telegram_init_data(urlencode(values), bot_token, now=1_000_001)
    assert identity.telegram_id == 111


def test_session_codec_rejects_modified_cookie() -> None:
    codec = SessionCodec("a" * 32)
    token, csrf = codec.create(111)
    assert codec.read(token, 60) == {"telegram_id": 111, "csrf": csrf}
    with pytest.raises(AppError):
        codec.read(token + "x", 60)
