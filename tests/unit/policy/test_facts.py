import pytest

from target_ai.policy import check_facts

FACTS = [
    "Цена консультации 1 500 руб.",
    "Скидка 20% на первое занятие",
    "Работаем с 10:00 до 20:00",
    "Гарантия 12 месяцев",
    "Открылись 15.03.2026",
]


def test_text_without_numbers_or_promises_is_clean():
    assert check_facts("Приходите на консультацию, всё расскажем", FACTS) == []


@pytest.mark.parametrize(
    "text",
    [
        "Консультация 1500 ₽",
        "Консультация 1 500 руб",
        "Консультация 1 500 рублей",
        "Скидка 20% новичкам",
        "Скидка 20 % новичкам",
        "Гарантия качества",
        "Работаем с 10:00",
        "Мы с 15.03.2026",
        "Гарантия 12 месяцев",
    ],
)
def test_claims_present_in_facts_pass(text):
    assert check_facts(text, FACTS) == []


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("Консультация 2000 ₽", "2000"),
        ("Консультация всего $15", "15"),
        ("Скидка 30%", "30%"),
        ("Работаем с 9:00", "9"),
        ("Открылись 20.03.2026", "20.03.2026"),
        ("5 звёзд на картах", "5"),
        ("1 500 $ за консультацию", "1500"),
    ],
)
def test_numbers_prices_percents_and_dates_not_in_facts_are_reported(text, fragment):
    violations = check_facts(text, FACTS)
    assert len(violations) == 1
    assert fragment in violations[0].replace(" ", "")


def test_currency_mismatch_is_named_as_such():
    (violation,) = check_facts("1 500 $ за консультацию", FACTS)
    assert "валют" in violation


def test_decimal_comma_and_dot_are_the_same_number():
    assert check_facts("Рейтинг 4,8", ["Рейтинг 4.8 на картах"]) == []


@pytest.mark.parametrize(
    "text",
    [
        "Бесплатно для всех",
        "Бесплатная доставка",
        "Лучший сервис в городе",
        "Лучшие цены",
        "Мы №1 в городе",
        "Номер 1 по версии клиентов",
        "100% результат",
        "Free delivery",
        "The best coffee",
        "Money back guarantee",
        "Скидки на всё",
    ],
)
def test_promise_words_without_support_in_facts_are_reported(text):
    facts = ["Гарантия 12 месяцев"] if "guarantee" in text else ["Работаем без выходных"]
    violations = check_facts(text, facts)
    assert violations, text


def test_one_violation_per_distinct_claim():
    assert len(check_facts("Скидка 70%! Скидка 70% только сегодня", ["Работаем"])) == 2  # слово + процент


def test_promise_word_supported_by_a_fact_passes():
    assert check_facts("Бесплатная доставка", ["Бесплатная доставка по городу"]) == []


def test_percent_100_requires_percent_in_facts():
    assert check_facts("100% натуральный состав", ["100% натуральный состав"]) == []
    assert check_facts("100% натуральный состав", ["Состав натуральный, 100 видов"]) != []


def test_empty_facts_flag_any_number():
    assert check_facts("Открыто с 9", []) != []


def test_accepts_objects_with_text_attribute():
    class Fact:
        text = "Цена 500 руб."

    assert check_facts("Всего 500 ₽", [Fact()]) == []


def test_messages_are_russian_and_quote_the_fragment():
    (violation,) = check_facts("Консультация 2000 ₽", FACTS)
    assert violation.startswith("«") and "нет в фактах" in violation
