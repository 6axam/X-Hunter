from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.api.errors import AppError


@dataclass(frozen=True)
class TelegramIdentity:
    telegram_id: int
    auth_date: int


def validate_telegram_init_data(
    raw: str,
    bot_token: str,
    *,
    now: int | None = None,
    max_age_seconds: int = 600,
) -> TelegramIdentity:
    pairs = dict(parse_qsl(raw, keep_blank_values=True, strict_parsing=True))
    received_hash = pairs.pop("hash", None)
    pairs.pop("signature", None)
    if not received_hash:
        raise AppError(
            status_code=401,
            code="invalid_init_data",
            message="Подпись Telegram отсутствует.",
        )
    data_check_string = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise AppError(
            status_code=401,
            code="invalid_init_data",
            message="Подпись Telegram неверна.",
        )
    try:
        auth_date = int(pairs["auth_date"])
        user = json.loads(pairs["user"])
        telegram_id = int(user["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AppError(
            status_code=401,
            code="invalid_init_data",
            message="Данные Telegram неполны.",
        ) from exc
    current = int(time.time()) if now is None else now
    if auth_date > current + 30 or current - auth_date > max_age_seconds:
        raise AppError(
            status_code=401,
            code="expired_init_data",
            message="Авторизация Telegram устарела.",
        )
    return TelegramIdentity(telegram_id=telegram_id, auth_date=auth_date)


class SessionCodec:
    def __init__(self, secret: str) -> None:
        self.serializer = URLSafeTimedSerializer(secret, salt="olx-hunter-session-v1")

    def create(self, telegram_id: int) -> tuple[str, str]:
        csrf = secrets.token_urlsafe(24)
        return self.serializer.dumps({"telegram_id": telegram_id, "csrf": csrf}), csrf

    def read(self, token: str, max_age: int) -> dict:
        try:
            value = self.serializer.loads(token, max_age=max_age)
        except SignatureExpired as exc:
            raise AppError(
                status_code=401, code="session_expired", message="Сессия истекла."
            ) from exc
        except BadSignature as exc:
            raise AppError(
                status_code=401, code="invalid_session", message="Сессия повреждена."
            ) from exc
        if not isinstance(value, dict) or "telegram_id" not in value or "csrf" not in value:
            raise AppError(status_code=401, code="invalid_session", message="Сессия повреждена.")
        return value
