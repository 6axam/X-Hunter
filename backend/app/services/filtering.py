from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.sources.types import ListingCandidate

ACCESSORY_WORDS = {"чехол", "кабель", "зарядка", "стекло", "запчасти", "аксессуар"}


def normalize(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "").casefold().replace("ё", "е")
    return " ".join(re.findall(r"[\w]+", text, flags=re.UNICODE))


def _scoped_text(candidate: ListingCandidate, scope: str) -> str:
    if scope == "title":
        return normalize(candidate.title)
    if scope == "description":
        return normalize(candidate.description)
    return normalize(f"{candidate.title} {candidate.description or ''}")


@dataclass(frozen=True)
class FilterDecision:
    decision: str
    reasons: list[dict[str, Any]]


def evaluate(candidate: ListingCandidate, specification: dict, rules: dict) -> FilterDecision:
    reasons: list[dict[str, Any]] = []
    price_from = specification.get("price_from")
    price_to = specification.get("price_to")
    if price_from is not None and candidate.price is not None:
        if candidate.price < Decimal(str(price_from)):
            return FilterDecision("reject", [{"code": "price_below_min"}])
    if price_to is not None and candidate.price is not None:
        if candidate.price > Decimal(str(price_to)):
            return FilterDecision("reject", [{"code": "price_above_max"}])

    for group in rules.get("required_groups", []):
        scope = group.get("scope", "both")
        text = _scoped_text(candidate, scope)
        options = [normalize(value) for value in group.get("values", []) if normalize(value)]
        if options and not any(option in text for option in options):
            return FilterDecision(
                "reject",
                [{"code": "required_group_missing", "values": group.get("values", [])}],
            )
        if options:
            reasons.append({"code": "required_group_match", "values": group.get("values", [])})

    for rule in rules.get("excluded", []):
        phrase = normalize(rule.get("value"))
        text = _scoped_text(candidate, rule.get("scope", "both"))
        if phrase and phrase in text:
            return FilterDecision("reject", [{"code": "excluded_phrase", "value": phrase}])

    if rules.get("reject_accessories"):
        title_words = set(normalize(candidate.title).split())
        if title_words.intersection(ACCESSORY_WORDS):
            return FilterDecision("reject", [{"code": "accessory_only"}])

    return FilterDecision("match", reasons or [{"code": "rules_passed"}])
