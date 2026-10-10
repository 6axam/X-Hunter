from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, Field

from app.api.errors import AppError
from app.sources.base import ListingSource, SourceResult, SourceState

ALLOWED_SEARCH_HOSTS = {"olx.ua", "www.olx.ua"}
TRACKING_PARAMETERS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
}


class OlxPageType(StrEnum):
    SEARCH = "search"
    LISTING = "listing"


class OlxSearchSpec(BaseModel):
    original_url: str
    current_url: str
    path: str
    currency: str | None = None
    price_from: Decimal | None = None
    price_to: Decimal | None = None
    seller_type: str | None = None
    order: str | None = None
    opaque_query: dict[str, list[str]] = Field(default_factory=dict)


class ParsedOlxUrl(BaseModel):
    page_type: OlxPageType
    search: OlxSearchSpec | None = None
    listing_url: str | None = None


class OlxSource(ListingSource):
    known_parameters = {
        "currency",
        "search[filter_float_price:from]",
        "search[filter_float_price:to]",
        "search[private_business]",
        "search[order]",
    }

    def __init__(self, *, http_enabled: bool = False) -> None:
        self.http_enabled = http_enabled

    @staticmethod
    def _validate_url(url: str) -> tuple[str, str, str, str]:
        try:
            parsed = urlsplit(url)
        except ValueError as exc:
            raise AppError(
                status_code=422,
                code="invalid_url",
                message="Не удалось разобрать ссылку.",
                field_errors={"url": ["Проверьте формат ссылки."]},
            ) from exc
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").lower().rstrip(".")
        if scheme not in {"http", "https"} or host not in ALLOWED_SEARCH_HOSTS:
            raise AppError(
                status_code=422,
                code="unsupported_source_url",
                message="Поддерживаются только ссылки OLX.ua.",
                field_errors={"url": ["Используйте адрес https://www.olx.ua/…"]},
            )
        if parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
            raise AppError(
                status_code=422,
                code="unsafe_url",
                message="Ссылка содержит неподдерживаемые данные подключения.",
                field_errors={"url": ["Удалите логин, пароль или нестандартный порт."]},
            )
        path = parsed.path or "/"
        normalized_base = urlunsplit(("https", "www.olx.ua", path, "", ""))
        return host, path, parsed.query, normalized_base

    @staticmethod
    def _decimal(value: str, field: str) -> Decimal:
        try:
            number = Decimal(value)
        except InvalidOperation as exc:
            raise AppError(
                status_code=422,
                code="invalid_filter_value",
                message="Некоторые параметры OLX имеют неверный формат.",
                field_errors={field: ["Ожидается числовое значение."]},
            ) from exc
        if number < 0:
            raise AppError(
                status_code=422,
                code="invalid_filter_value",
                message="Цена не может быть отрицательной.",
                field_errors={field: ["Укажите значение не меньше нуля."]},
            )
        return number

    def parse_search_url(self, url: str) -> ParsedOlxUrl:
        _, path, raw_query, normalized_base = self._validate_url(url)
        if path.startswith("/d/") or "/obyavlenie/" in path:
            query = [(key, value) for key, value in parse_qsl(raw_query) if key == "search_reason"]
            listing_url = normalized_base
            if query:
                listing_url = f"{listing_url}?{urlencode(query)}"
            return ParsedOlxUrl(page_type=OlxPageType.LISTING, listing_url=listing_url)

        values: dict[str, list[str]] = defaultdict(list)
        for key, value in parse_qsl(raw_query, keep_blank_values=True):
            if key in TRACKING_PARAMETERS or key == "page":
                continue
            values[key].append(value)

        def one(key: str) -> str | None:
            entries = values.pop(key, [])
            return entries[-1] if entries else None

        currency = one("currency")
        raw_min = one("search[filter_float_price:from]")
        raw_max = one("search[filter_float_price:to]")
        seller_type = one("search[private_business]")
        order = one("search[order]")
        price_from = self._decimal(raw_min, "price_from") if raw_min else None
        price_to = self._decimal(raw_max, "price_to") if raw_max else None
        if price_from is not None and price_to is not None and price_from > price_to:
            raise AppError(
                status_code=422,
                code="invalid_price_range",
                message="Минимальная цена больше максимальной.",
                field_errors={"price_from": ["Цена от должна быть не больше цены до."]},
            )

        spec = OlxSearchSpec(
            original_url=url,
            current_url="",
            path=path,
            currency=currency,
            price_from=price_from,
            price_to=price_to,
            seller_type=seller_type,
            order=order,
            opaque_query=dict(values),
        )
        spec.current_url = self.build_search_url(spec)
        return ParsedOlxUrl(page_type=OlxPageType.SEARCH, search=spec)

    def build_search_url(self, specification: OlxSearchSpec) -> str:
        pairs: list[tuple[str, str]] = []
        if specification.currency:
            pairs.append(("currency", specification.currency))
        if specification.seller_type:
            pairs.append(("search[private_business]", specification.seller_type))
        if specification.order:
            pairs.append(("search[order]", specification.order))
        if specification.price_from is not None:
            pairs.append(("search[filter_float_price:from]", format(specification.price_from, "f")))
        if specification.price_to is not None:
            pairs.append(("search[filter_float_price:to]", format(specification.price_to, "f")))
        for key in sorted(specification.opaque_query):
            for value in specification.opaque_query[key]:
                pairs.append((key, value))
        return urlunsplit(("https", "www.olx.ua", specification.path or "/", urlencode(pairs), ""))

    async def fetch_search_page(self, specification: OlxSearchSpec, page: int = 1) -> SourceResult:
        if not self.http_enabled:
            return SourceResult(
                state=SourceState.NOT_CONFIGURED,
                error_code="source_http_disabled",
                message="Сетевой источник OLX не включён и не проверен.",
            )
        return SourceResult(
            state=SourceState.UNAVAILABLE,
            error_code="source_adapter_pending",
            message="Сетевой адаптер ещё не реализован.",
        )

    async def fetch_listing(self, url: str) -> SourceResult:
        self._validate_url(url)
        if not self.http_enabled:
            return SourceResult(
                state=SourceState.NOT_CONFIGURED,
                error_code="source_http_disabled",
                message="Сетевой источник OLX не включён и не проверен.",
            )
        return SourceResult(state=SourceState.UNAVAILABLE, error_code="source_adapter_pending")

    async def check_availability(self, url: str) -> SourceResult:
        self._validate_url(url)
        if not self.http_enabled:
            return SourceResult(
                state=SourceState.NOT_CONFIGURED,
                error_code="source_http_disabled",
                message="Сетевая проверка актуальности не включена.",
            )
        return SourceResult(state=SourceState.UNAVAILABLE, error_code="source_adapter_pending")
