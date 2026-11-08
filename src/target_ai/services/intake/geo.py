"""Гео из текста: страна/город -> коды стран ISO для таргетинга (радиусов и точных городов анкета не ведёт)."""

from __future__ import annotations

import re
from dataclasses import dataclass

EU_COUNTRIES = frozenset(
    "AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE".split()
)


@dataclass(frozen=True)
class Place:
    name: str
    countries: tuple[str, ...]

    @property
    def in_eu(self) -> bool:
        return any(c in EU_COUNTRIES for c in self.countries)

    @property
    def is_country(self) -> bool:
        return self.name in COUNTRY_NAMES.values()

    @property
    def country_name(self) -> str:
        return COUNTRY_NAMES.get(self.countries[0], self.countries[0])

    @property
    def summary(self) -> str:
        """Как показ будет устроен на самом деле: по стране, радиуса пока нет."""
        if self.is_country:
            return f"по всей стране {self.country_name}"
        return f"город {self.name} → показ по стране {self.country_name}: точный радиус пока не поддерживается"

    @property
    def label(self) -> str:
        return f"{self.name} ({', '.join(self.countries)})"


COUNTRY_NAMES = {
    "DE": "Германия", "KZ": "Казахстан", "RU": "Россия", "UZ": "Узбекистан", "UA": "Украина", "BY": "Беларусь",
    "GE": "Грузия", "AM": "Армения", "AZ": "Азербайджан", "KG": "Киргизия", "AE": "ОАЭ", "TR": "Турция",
    "GB": "Великобритания", "FR": "Франция", "ES": "Испания", "IT": "Италия", "PL": "Польша", "CZ": "Чехия",
    "AT": "Австрия", "NL": "Нидерланды", "PT": "Португалия", "GR": "Греция", "LV": "Латвия", "LT": "Литва",
    "EE": "Эстония", "JP": "Япония", "US": "США", "CA": "Канада",
}
_ENDINGS = "(?:а|я|у|ю|е|и|ы|ом|ем|ой|ей|ою|ию|ия|ии|ией|ов|ами|ах)?"  # только известные окончания
# (основа слова, название, страна) — города и страны, самые частые для клиентов.
_RAW = (
    ("берлин", "Берлин", "DE"), ("мюнхен", "Мюнхен", "DE"), ("гамбург", "Гамбург", "DE"), ("германи", "Германия", "DE"),
    ("алматы", "Алматы", "KZ"), ("астан", "Астана", "KZ"), ("казахстан", "Казахстан", "KZ"),
    ("москв", "Москва", "RU"), ("петербург", "Санкт-Петербург", "RU"), ("питер", "Санкт-Петербург", "RU"),
    ("росси", "Россия", "RU"), ("ташкент", "Ташкент", "UZ"), ("самарканд", "Самарканд", "UZ"),
    ("узбекистан", "Узбекистан", "UZ"), ("киев", "Киев", "UA"), ("украин", "Украина", "UA"),
    ("минск", "Минск", "BY"), ("беларус", "Беларусь", "BY"), ("тбилиси", "Тбилиси", "GE"), ("грузи", "Грузия", "GE"),
    ("ереван", "Ереван", "AM"), ("армени", "Армения", "AM"), ("баку", "Баку", "AZ"), ("азербайджан", "Азербайджан", "AZ"),
    ("бишкек", "Бишкек", "KG"), ("киргиз", "Киргизия", "KG"), ("дубай", "Дубай", "AE"), ("эмират", "ОАЭ", "AE"),
    ("стамбул", "Стамбул", "TR"), ("турци", "Турция", "TR"), ("лондон", "Лондон", "GB"), ("великобритани", "Великобритания", "GB"),
    ("париж", "Париж", "FR"), ("франци", "Франция", "FR"), ("мадрид", "Мадрид", "ES"), ("барселон", "Барселона", "ES"),
    ("испани", "Испания", "ES"), ("рим", "Рим", "IT"), ("милан", "Милан", "IT"), ("итали", "Италия", "IT"),
    ("варшав", "Варшава", "PL"), ("польш", "Польша", "PL"), ("прага", "Прага", "CZ"), ("праге", "Прага", "CZ"),
    ("вена", "Вена", "AT"), ("вене", "Вена", "AT"), ("амстердам", "Амстердам", "NL"), ("нидерланд", "Нидерланды", "NL"),
    ("лиссабон", "Лиссабон", "PT"), ("афин", "Афины", "GR"), ("рига", "Рига", "LV"), ("риге", "Рига", "LV"),
    ("вильнюс", "Вильнюс", "LT"), ("таллин", "Таллин", "EE"), ("токио", "Токио", "JP"), ("япони", "Япония", "JP"),
    ("нью-йорк", "Нью-Йорк", "US"), ("сша", "США", "US"), ("америк", "США", "US"), ("лос-анджелес", "Лос-Анджелес", "US"),
    ("торонто", "Торонто", "CA"), ("канад", "Канада", "CA"),
)
_PATTERNS = tuple(
    (re.compile(rf"(?<!\w){re.escape(stem)}{_ENDINGS}(?!\w)", re.IGNORECASE), Place(name, (code,)))
    for stem, name, code in _RAW
)


def find_place(text: str) -> Place | None:
    """Первое найденное в тексте известное место или None; «DE» — код страны."""
    code = text.strip().upper()
    if code in COUNTRY_NAMES:
        return Place(COUNTRY_NAMES[code], (code,))
    best: tuple[int, Place] | None = None
    for pattern, place in _PATTERNS:
        match = pattern.search(text)
        if match and (best is None or match.start() < best[0]):
            best = (match.start(), place)
    return best[1] if best else None
