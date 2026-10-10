from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from urllib.parse import urljoin, urlsplit, urlunsplit

from selectolax.parser import HTMLParser, Node

from app.sources.types import ListingCandidate

_ID_PATTERN = re.compile(r"(?:-|/)(ID[a-zA-Z0-9]+)(?:\.html|[/?#]|$)")
_NUMBER_PATTERN = re.compile(r"[^0-9,.]")
_LOCATION_DATE_PATTERN = re.compile(r"\s+[-–—]\s+.+\d{4}\s*(?:р\.)?$", re.IGNORECASE)
_PUBLISHED_PATTERN = re.compile(
    r"(?:опубліковано|опубликовано|published)\s+(\d{1,2})\s+([^\s]+)\s+(\d{4})",
    re.IGNORECASE,
)
_MONTHS = {
    "січня": 1,
    "января": 1,
    "january": 1,
    "лютого": 2,
    "февраля": 2,
    "february": 2,
    "березня": 3,
    "марта": 3,
    "march": 3,
    "квітня": 4,
    "апреля": 4,
    "april": 4,
    "травня": 5,
    "мая": 5,
    "may": 5,
    "червня": 6,
    "июня": 6,
    "june": 6,
    "липня": 7,
    "июля": 7,
    "july": 7,
    "серпня": 8,
    "августа": 8,
    "august": 8,
    "вересня": 9,
    "сентября": 9,
    "september": 9,
    "жовтня": 10,
    "октября": 10,
    "october": 10,
    "листопада": 11,
    "ноября": 11,
    "november": 11,
    "грудня": 12,
    "декабря": 12,
    "december": 12,
}


def _canonical_url(value: str) -> str:
    absolute = urljoin("https://www.olx.ua", value)
    parsed = urlsplit(absolute)
    return urlunsplit(("https", "www.olx.ua", parsed.path, "", ""))


def _source_id(url: str) -> str:
    match = _ID_PATTERN.search(url)
    if match:
        return match.group(1)
    return sha256(url.encode()).hexdigest()[:24]


def _text(node: Node | None) -> str | None:
    if node is None:
        return None
    value = " ".join(node.text(separator=" ", strip=True).split())
    return value or None


def _price(raw: str | None) -> Decimal | None:
    if not raw:
        return None
    normalized = _NUMBER_PATTERN.sub("", raw).replace(" ", "")
    if normalized.count(",") == 1 and "." not in normalized:
        normalized = normalized.replace(",", ".")
    else:
        normalized = normalized.replace(",", "")
    try:
        return Decimal(normalized) if normalized else None
    except InvalidOperation:
        return None


def _currency(raw: str | None) -> str | None:
    if not raw:
        return None
    value = raw.casefold()
    if "грн" in value or "uah" in value or "₴" in value:
        return "UAH"
    if "$" in value or "usd" in value:
        return "USD"
    if "€" in value or "eur" in value:
        return "EUR"
    return None


def _first_text(node: Node, selectors: tuple[str, ...]) -> str | None:
    for selector in selectors:
        value = _text(node.css_first(selector))
        if value:
            return value
    return None


def _image_urls(node: Node) -> list[str]:
    urls: list[str] = []
    for image in node.css("img[src]"):
        value = image.attributes.get("src")
        if not value:
            continue
        url = urljoin("https://www.olx.ua", value)
        if url.startswith(("https://", "http://")) and url not in urls:
            urls.append(url)
    return urls


def _city_from_location(value: str | None) -> str | None:
    if not value:
        return None
    city = _LOCATION_DATE_PATTERN.sub("", value).strip()
    return city or None


def _published_at(value: str | None) -> datetime | None:
    if not value:
        return None
    match = _PUBLISHED_PATTERN.search(value)
    if not match:
        return None
    month = _MONTHS.get(match.group(2).rstrip(".,").casefold())
    if month is None:
        return None
    try:
        return datetime(int(match.group(3)), month, int(match.group(1)), tzinfo=UTC)
    except ValueError:
        return None


def _listing_attributes(tree: HTMLParser) -> tuple[str | None, dict[str, str]]:
    """Extract stable, visible OLX properties without depending on CSS class names."""
    attributes: dict[str, str] = {}
    condition: str | None = None
    container = tree.css_first("[data-testid='ad-parameters-container']") or tree.css_first(
        "[data-testid='ad-parameters']"
    )
    if container is None:
        return condition, attributes

    for entry in container.css("p"):
        value = _text(entry)
        if not value:
            continue
        if ":" in value:
            label, item = (part.strip() for part in value.split(":", 1))
            if not label or not item:
                continue
            if label.casefold() in {"стан", "состояние", "condition"}:
                condition = item
            else:
                attributes[label] = item
        elif value.casefold() in {"приватна особа", "частное лицо", "бізнес", "бизнес"}:
            attributes["seller_type"] = value
    return condition, attributes


def _seller_attributes(tree: HTMLParser, attributes: dict[str, str]) -> None:
    seller = tree.css_first("[data-testid='seller_card']")
    if seller is None:
        return
    name = _text(seller.css_first("[data-testid='user-profile-user-name']"))
    if name:
        attributes["seller_name"] = name
    profile = seller.css_first("a[data-testid='user-profile-link'][href]")
    if profile and profile.attributes.get("href"):
        attributes["seller_profile_url"] = urljoin("https://www.olx.ua", profile.attributes["href"])
    score = _text(seller.css_first("[data-testid='score-widget']"))
    if score:
        attributes["seller_rating"] = score
    member_since = _text(seller.css_first("[data-testid='member-since']"))
    if member_since:
        attributes["seller_member_since"] = member_since
    last_seen = _text(seller.css_first("[data-testid='lastSeenBox']"))
    if last_seen:
        attributes["seller_last_seen"] = last_seen


def parse_search_html(html: str) -> list[ListingCandidate]:
    """Parse product cards while ignoring banners and de-duplicating promoted copies."""
    tree = HTMLParser(html)
    found: dict[str, ListingCandidate] = {}
    cards = tree.css("[data-cy='l-card'], [data-testid='l-card'], article[data-listing-id]")
    seen_cards: set[int] = set()
    for card in cards:
        # Current OLX marks every result with both data-cy and data-testid.
        # selectolax returns it twice for the combined selector.
        if id(card) in seen_cards:
            continue
        seen_cards.add(id(card))
        if card.attributes.get("data-ad-banner") == "true" or card.css_first("[data-banner]"):
            continue
        link = card.css_first("a[data-testid='card-title-link'][href]") or card.css_first(
            "a[href*='/d/'], a[href*='obyavlenie']"
        )
        href = link.attributes.get("href") if link else None
        if not href:
            continue
        canonical = _canonical_url(href)
        listing_id = card.attributes.get("data-listing-id") or _source_id(canonical)
        title = _first_text(
            card,
            (
                "[data-testid='ad-card-title'] h4",
                "[data-cy='ad-card-title'] h4",
                "[data-testid='card-title-link']",
                "h2",
                "h4",
            ),
        )
        if not title:
            title = _text(link)
        if not title:
            continue
        raw_price = _first_text(card, ("[data-testid='ad-price']", "[data-testid='price']"))
        location = _text(card.css_first("[data-testid='location-date']"))
        condition = _first_text(
            card,
            ("[data-testid='ad-condition']", "[data-nx-name='NexusBadge']"),
        )
        candidate = ListingCandidate(
            source_listing_id=listing_id,
            canonical_url=canonical,
            title=title,
            price=_price(raw_price),
            currency=_currency(raw_price),
            raw_price=raw_price,
            city=_city_from_location(location),
            condition=condition,
            image_urls=_image_urls(card),
            promoted=bool(card.css_first("[data-testid='ad-promoted']")),
        )
        existing = found.get(listing_id)
        if existing is None or (existing.promoted and not candidate.promoted):
            found[listing_id] = candidate
    return list(found.values())


def _json_ld_product(tree: HTMLParser) -> dict | None:
    for node in tree.css("script[type='application/ld+json']"):
        try:
            value = json.loads(node.text())
        except (json.JSONDecodeError, TypeError):
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, dict) and item.get("@type") in {"Product", "Offer"}:
                return item
    return None


def parse_listing_html(html: str, url: str) -> ListingCandidate:
    tree = HTMLParser(html)
    product = _json_ld_product(tree) or {}
    offers = product.get("offers") if isinstance(product.get("offers"), dict) else {}
    title = str(
        product.get("name")
        or _first_text(tree, ("[data-testid='offer_title']", "[data-cy='offer_title']", "h1"))
        or ""
    ).strip()
    description = str(
        product.get("description")
        or _first_text(
            tree,
            (
                "[data-cy='ad_description']",
                "[data-testid='ad_description']",
                "[data-testid='ad-description']",
            ),
        )
        or ""
    ).strip()
    raw_price = str(
        offers.get("price")
        or _first_text(tree, ("[data-testid='ad-price-container']", "[data-testid='ad-price']"))
        or ""
    )
    images = product.get("image") or []
    if isinstance(images, str):
        images = [images]
    if not images:
        images = _image_urls(tree.css_first("[data-testid='image-galery-container']") or tree)
    condition, attributes = _listing_attributes(tree)
    _seller_attributes(tree, attributes)
    location = _first_text(
        tree,
        ("[data-testid='map-aside-section'] section p", "[data-testid='location-date']"),
    )
    published_text = _first_text(tree, ("[data-testid='ad-posted-at']", "[data-cy='ad-posted-at']"))
    product_properties = product.get("additionalProperty", [])
    if not isinstance(product_properties, list):
        product_properties = []
    for item in product_properties:
        if isinstance(item, dict) and item.get("name") and item.get("value"):
            attributes.setdefault(str(item["name"]), str(item["value"]))
    canonical = _canonical_url(url)
    return ListingCandidate(
        source_listing_id=_source_id(canonical),
        canonical_url=canonical,
        title=title,
        description=description or None,
        price=_price(raw_price),
        currency=offers.get("priceCurrency") or _currency(raw_price),
        raw_price=raw_price or None,
        price_type="fixed" if _price(raw_price) is not None else "unknown",
        city=_city_from_location(location),
        condition=condition,
        attributes=attributes,
        published_at=_published_at(published_text),
        image_urls=[str(item) for item in images if str(item).startswith(("http://", "https://"))],
    )
