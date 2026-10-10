from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, Field, model_validator

from app.api.errors import AppError
from app.sources.base import ListingSource, SourceResult, SourceState
from app.sources.parser import parse_listing_html, parse_search_html

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

    @model_validator(mode="after")
    def validate_price_range(self) -> OlxSearchSpec:
        if self.price_from is not None and self.price_to is not None:
            if self.price_from > self.price_to:
                raise ValueError("price_from must not exceed price_to")
        return self


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

    def __init__(
        self,
        *,
        http_enabled: bool = False,
        browser_enabled: bool = False,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.http_enabled = http_enabled
        self.browser_enabled = browser_enabled
        self.client = client

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
        pairs = parse_qsl(urlsplit(specification.current_url).query, keep_blank_values=True)
        pairs = [(key, value) for key, value in pairs if key != "page"]
        if page > 1:
            pairs.append(("page", str(page)))
        parsed = urlsplit(specification.current_url)
        url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(pairs), ""))
        response = await self._fetch_html(url)
        if response.state is not SourceState.COMPLETE:
            return response
        return SourceResult(
            state=SourceState.COMPLETE,
            data=[item.model_dump(mode="json") for item in parse_search_html(response.data)],
            metadata={"pages_checked": 1},
        )

    async def fetch_listing(self, url: str) -> SourceResult:
        self._validate_url(url)
        if not self.http_enabled:
            return SourceResult(
                state=SourceState.NOT_CONFIGURED,
                error_code="source_http_disabled",
                message="Сетевой источник OLX не включён и не проверен.",
            )
        response = await self._fetch_html(url)
        if response.state is not SourceState.COMPLETE:
            return response
        try:
            listing = parse_listing_html(response.data, url)
        except Exception:
            return SourceResult(
                state=SourceState.PARTIAL,
                error_code="listing_structure_changed",
                message="Структура карточки OLX не распознана полностью.",
            )
        return SourceResult(state=SourceState.COMPLETE, data=listing.model_dump(mode="json"))

    async def check_availability(self, url: str) -> SourceResult:
        self._validate_url(url)
        if not self.http_enabled:
            return SourceResult(
                state=SourceState.NOT_CONFIGURED,
                error_code="source_http_disabled",
                message="Сетевая проверка актуальности не включена.",
            )
        return await self._fetch_html(url, availability_only=True)

    async def _fetch_html(self, url: str, *, availability_only: bool = False) -> SourceResult:
        if self.browser_enabled:
            return await self._fetch_html_browser(url, availability_only=availability_only)
        return await self._fetch_html_http(url, availability_only=availability_only)

    async def _fetch_html_browser(
        self, url: str, *, availability_only: bool = False
    ) -> SourceResult:
        """Fetch OLX through Chromium when direct HTTP is blocked by CloudFront."""
        try:
            from playwright.async_api import TimeoutError as PlaywrightTimeoutError
            from playwright.async_api import async_playwright

            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox"])
                try:
                    context = await browser.new_context(
                        locale="uk-UA",
                        user_agent=(
                            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                        ),
                    )
                    page = await context.new_page()
                    response = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                    await page.wait_for_timeout(1_000)
                    status = response.status if response is not None else 200
                    if status in {404, 410}:
                        return SourceResult(
                            state=SourceState.UNAVAILABLE,
                            error_code="listing_removed" if availability_only else "page_not_found",
                            metadata={"confirmed": availability_only},
                        )
                    if status in {403, 429}:
                        return SourceResult(
                            state=SourceState.BLOCKED, error_code=f"source_browser_{status}"
                        )
                    body = await page.content()
                    marker = body.casefold()
                    if any(
                        value in marker
                        for value in ("captcha", "access denied", "verify you are human")
                    ):
                        return SourceResult(
                            state=SourceState.BLOCKED, error_code="source_browser_challenge"
                        )
                    if availability_only:
                        return SourceResult(state=SourceState.COMPLETE)
                    if len(body.encode()) > 5_000_000:
                        return SourceResult(
                            state=SourceState.PARTIAL, error_code="source_body_too_large"
                        )
                    return SourceResult(state=SourceState.COMPLETE, data=body)
                finally:
                    await browser.close()
        except PlaywrightTimeoutError:
            return SourceResult(state=SourceState.PARTIAL, error_code="source_browser_timeout")
        except Exception as exc:
            if exc.__class__.__name__ in {"Error", "TargetClosedError"}:
                return SourceResult(
                    state=SourceState.PARTIAL, error_code="source_browser_unavailable"
                )
            return SourceResult(state=SourceState.PARTIAL, error_code="source_browser_error")

    async def _fetch_html_http(self, url: str, *, availability_only: bool = False) -> SourceResult:
        current = url
        own_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5))
        try:
            for _ in range(4):
                self._validate_url(current)
                response = await client.get(
                    current,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                            "Chrome/131 Safari/537.36"
                        ),
                        "Accept-Language": "uk-UA,uk;q=0.9,ru;q=0.8",
                    },
                    follow_redirects=False,
                )
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        return SourceResult(
                            state=SourceState.PARTIAL, error_code="redirect_without_location"
                        )
                    current = str(response.url.join(location))
                    continue
                if response.status_code in {404, 410}:
                    return SourceResult(
                        state=SourceState.UNAVAILABLE,
                        error_code="listing_removed" if availability_only else "page_not_found",
                        metadata={"confirmed": availability_only},
                    )
                if response.status_code in {403, 429}:
                    return SourceResult(
                        state=SourceState.BLOCKED,
                        error_code=f"source_http_{response.status_code}",
                    )
                if response.status_code >= 500:
                    return SourceResult(
                        state=SourceState.PARTIAL,
                        error_code="source_server_error",
                    )
                response.raise_for_status()
                if availability_only:
                    return SourceResult(state=SourceState.COMPLETE)
                content_type = response.headers.get("content-type", "")
                if "text/html" not in content_type:
                    return SourceResult(
                        state=SourceState.PARTIAL,
                        error_code="unexpected_content_type",
                    )
                body = response.content
                if len(body) > 5_000_000:
                    return SourceResult(
                        state=SourceState.PARTIAL,
                        error_code="source_body_too_large",
                    )
                return SourceResult(state=SourceState.COMPLETE, data=response.text)
            return SourceResult(state=SourceState.PARTIAL, error_code="too_many_redirects")
        except (httpx.TimeoutException, httpx.NetworkError):
            return SourceResult(state=SourceState.PARTIAL, error_code="source_network_error")
        finally:
            if own_client:
                await client.aclose()
