from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from urllib.parse import urljoin, urlsplit, urlunsplit

from selectolax.parser import HTMLParser, Node

from app.sources.types import ListingCandidate

_ID_PATTERN = re.compile(r"(?:-|/)(ID[a-zA-Z0-9]+)(?:\.html|[/?#]|$)")
_NUMBER_PATTERN = re.compile(r"[^0-9,.]")


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


def parse_search_html(html: str) -> list[ListingCandidate]:
    """Parse product cards while ignoring banners and de-duplicating promoted copies."""
    tree = HTMLParser(html)
    found: dict[str, ListingCandidate] = {}
    selectors = "[data-cy='l-card'], [data-testid='l-card'], article[data-listing-id]"
    for card in tree.css(selectors):
        if card.attributes.get("data-ad-banner") == "true" or card.css_first("[data-banner]"):
            continue
        link = card.css_first("a[href*='/d/'], a[href*='obyavlenie']")
        href = link.attributes.get("href") if link else None
        if not href:
            continue
        canonical = _canonical_url(href)
        listing_id = card.attributes.get("data-listing-id") or _source_id(canonical)
        title = _text(card.css_first("[data-cy='ad-card-title'], [data-testid='ad-title'], h2"))
        if not title:
            title = _text(link)
        if not title:
            continue
        raw_price = _text(card.css_first("[data-testid='ad-price'], [data-testid='price']"))
        candidate = ListingCandidate(
            source_listing_id=listing_id,
            canonical_url=canonical,
            title=title,
            price=_price(raw_price),
            currency=(
                "UAH"
                if raw_price and ("грн" in raw_price.lower() or "uah" in raw_price.lower())
                else None
            ),
            raw_price=raw_price,
            city=_text(card.css_first("[data-testid='location-date']")),
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
    title = str(product.get("name") or _text(tree.css_first("h1")) or "").strip()
    description = str(
        product.get("description")
        or _text(tree.css_first("[data-cy='ad_description'], [data-testid='ad-description']"))
        or ""
    ).strip()
    raw_price = str(offers.get("price") or _text(tree.css_first("[data-testid='ad-price']")) or "")
    images = product.get("image") or []
    if isinstance(images, str):
        images = [images]
    if not images:
        images = [
            node.attributes["src"] for node in tree.css("img[data-testid='gallery-image'][src]")
        ]
    canonical = _canonical_url(url)
    return ListingCandidate(
        source_listing_id=_source_id(canonical),
        canonical_url=canonical,
        title=title,
        description=description or None,
        price=_price(raw_price),
        currency=offers.get("priceCurrency") or None,
        raw_price=raw_price or None,
        price_type="fixed" if _price(raw_price) is not None else "unknown",
        city=_text(tree.css_first("[data-testid='location-date']")),
        condition=_text(tree.css_first("[data-testid='ad-parameters']")),
        image_urls=[str(item) for item in images if str(item).startswith(("http://", "https://"))],
    )
