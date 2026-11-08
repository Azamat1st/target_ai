"""Страны ЕС: для рекламы в них Meta требует DSA-поля (получатель и плательщик рекламы)."""

from __future__ import annotations

from collections.abc import Iterable

EU_COUNTRIES = frozenset({
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE",
    "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
})


def includes_eu(countries: Iterable[str]) -> bool:
    return any(str(code).upper() in EU_COUNTRIES for code in countries)
