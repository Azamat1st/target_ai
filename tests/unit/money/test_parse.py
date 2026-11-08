"""Строгая грамматика сумм: однозначное разбирается, неоднозначное — ValueError, не догадка."""

import random
from decimal import Decimal

import pytest

from target_ai.money import UnknownCurrency, parse_amount_ru
from target_ai.money.currencies import HUNDRED_CURRENCIES, ZERO_DECIMAL_CURRENCIES

D = Decimal


@pytest.mark.parametrize(
    "text,amount,cur",
    [
        ("500к", D("500000"), None),
        ("1,5k $", D("1500"), "USD"),
        ("2K", D("2000"), None),
        ("2 тыс в день", D("2000"), None),
        ("2тыс.", D("2000"), None),
        ("300 тысяч", D("300000"), None),
        ("2 тысячи", D("2000"), None),
        ("2 млн", D("2000000"), None),
        ("1,5 млн", D("1500000"), None),
        ("1 200,50 ₽", D("1200.50"), "RUB"),
        ("2500$", D("2500"), "USD"),
        ("$2500", D("2500"), "USD"),
        ("500 usd", D("500"), "USD"),
        ("10 евро", D("10"), "EUR"),
        ("300 000", D("300000"), None),
        ("300 000", D("300000"), None),
        ("1,5 тыс", D("1500"), None),
        ("Бюджет 300\n14 дней", D("300"), None),
        ("по 300 7 дней", D("300"), None),
        ("Бюджет 300 на 14 дней", D("300"), None),
        ("300 на 20%", D("300"), None),
        ("100 000 иен", D("100000"), "JPY"),
        ("5 лир", D("5"), "TRY"),
        ("2 000 гривен", D("2000"), "UAH"),
        ("500 грн", D("500"), "UAH"),
        ("2000 ₴", D("2000"), "UAH"),
        ("500 uah", D("500"), "UAH"),
        ("300 £", D("300"), "GBP"),
        ("300 фунтов", D("300"), "GBP"),
        ("300 gbp", D("300"), "GBP"),
        ("300 юаней", D("300"), "CNY"),
        ("300 cny", D("300"), "CNY"),
        ("500 usd, то есть 500$", D("500"), "USD"),
        ("Бюджет в долларах: 700", D("700"), "USD"),
        ("500 в месяц $", D("500"), "USD"),
        ("бюджет 1500 евро в месяц", D("1500"), "EUR"),
        ("Лимит 2 тыс $", D("2000"), "USD"),
        ("500 на рекламу", D("500"), None),
        ("500.", D("500"), None),
        ("(500)", D("500"), None),
        ("500 в день, валюта KRW", D("500"), "KRW"),
    ],
)
def test_unambiguous_phrases_are_parsed(text, amount, cur):
    assert parse_amount_ru(text) == (amount, cur)


@pytest.mark.parametrize(
    "text",
    [
        "1.500 ₽",
        "$1,500",
        "0,500 $",
        "1,500 тыс",
        "2,500 млн",
        "0.250 тыс",
        "1.500.000",
        "много",
        "много денег",
        "Лимит 50 000 ₽ в месяц и 2 тыс в день",
        "300 или 400",
        "14 дней",
        "500$ AUD",
        "500 долларов евро",
    ],
)
def test_ambiguous_input_raises_value_error(text):
    with pytest.raises(ValueError) as info:
        parse_amount_ru(text)
    assert not isinstance(info.value, UnknownCurrency)


def test_thousands_hint_is_in_russian():
    with pytest.raises(ValueError, match="без точек и запятых"):
        parse_amount_ru("1.500 ₽")


def test_several_amounts_hint_is_in_russian():
    with pytest.raises(ValueError, match="несколько сумм"):
        parse_amount_ru("300 или 400")


@pytest.mark.parametrize(
    "text",
    [
        "50 000 тенге", "50 000 ₸", "500 kzt", "3 000 000 сум",
        "500 uzs", "300 манат", "500 byn", "тенге 500",
        "Бюджет в тенге: 50 000",
    ],
)
def test_currency_outside_table_is_never_dropped(text):
    with pytest.raises(UnknownCurrency, match="Meta не поддерживает эту валюту"):
        parse_amount_ru(text)


def test_amount_never_exceeds_written_number():
    rng = random.Random(20261108)
    symbols = {"$": "USD", "€": "EUR", "₽": "RUB", "¥": "JPY", "₺": "TRY"}
    for _ in range(300):
        n = rng.randint(1, 10**7)
        for symbol, code in symbols.items():
            spaced = f"{n:,}".replace(",", " ")
            for text in (f"{n} {symbol}", f"{spaced} {symbol}", f"{symbol}{spaced}", f"{n}{symbol}"):
                assert parse_amount_ru(text) == (D(n), code), text


@pytest.mark.parametrize(
    "text,token",
    [
        ("50 000 тг", "тг"),
        ("3 000 000 сумов", "сумов"),
        ("5 000 сомов", "сомов"),
        ("100 000 вон", "вон"),
        ("100 000 ₩", "₩"),
        ("10 000 ₹", "₹"),
        ("1 000 000 донгов", "донгов"),
        ("10 000 рупий", "рупий"),
        ("20 000 песо", "песо"),
        ("500 злотых", "злотых"),
        ("1 000 дирхамов", "дирхамов"),
        ("500 белорусских рублей", "белорусских"),
        ("500 канадских долларов", "канадских"),
        ("2 тыс гонконгских долларов", "гонконгских"),
        ("500 R$", "r"),
        ("R$500", "r$"),
        ("US$ 500", "s$"),
        ("A$500", "a$"),
        ("C$ 500", "c$"),
        ("₱500", "₱"),
        ("потратить 500", "потратить"),
        ("500abc", "abc"),
        ("500$ к пятнице", "пятнице"),
        ("50 000 в месяц тг", "тг"),
        ("бюджет 100 000 в месяц (тг)", "тг"),
        ("100 000 в месяц, в вонах", "вонах"),
        ("500 в месяц сумов", "сумов"),
        ("канадских долларов 500", "канадских"),
        ("сингапурских долларов 500", "сингапурских"),
        ("500 долларов Гонконга", "гонконга"),
        ("500 долл. Канады", "канады"),
        ("500 ₪", "₪"),
        ("500 ₫", "₫"),
        ("₩ 500 в месяц", "₩"),
    ],
)
def test_unknown_neighbour_of_number_is_unknown_currency(text, token):
    with pytest.raises(UnknownCurrency) as info:
        parse_amount_ru(text)
    message = str(info.value)
    assert "Не понял „" in message and token in message
    assert "укажите сумму в валюте рекламного аккаунта" in message


_SERVICE_WORDS = {
    "в", "на", "за", "до", "по", "с", "и", "а", "или", "месяц", "мес", "день", "дн", "неделю",
    "ежемесячно", "ежедневно", "бюджет", "лимит", "рекламу", "примерно", "около", "максимум",
    "не", "больше", "меньше", "к", "то", "есть", "тыс", "млн", "раз", "шт", "час",
    "евро", "руб", "лир", "иен", "иена", "юань", "лира", "руб",
}


def test_random_cyrillic_word_after_number_is_never_silently_dropped():
    rng = random.Random(8)
    alphabet = "абвгдежзиклмнопрстуфхцчшщыэюя"
    for _ in range(500):
        word = "".join(rng.choice(alphabet) for _ in range(rng.randint(3, 8)))
        if word in _SERVICE_WORDS:
            continue
        n = rng.randint(1, 10**7)
        try:
            result = parse_amount_ru(f"{n} {word}")
        except ValueError:
            continue
        assert result[1] is not None, f"{n} {word}"


def test_fragment_longer_than_sixty_chars_is_rejected_with_hint():
    text = "Мой месячный бюджет на рекламу в целом составляет примерно 1500 евро"
    assert len(text) > 60
    with pytest.raises(ValueError, match="Напишите только сумму, например: 1500 € в месяц"):
        parse_amount_ru(text)


def test_random_cyrillic_word_in_any_position_is_never_silently_dropped():
    rng = random.Random(11)
    alphabet = "абвгдежзиклмнопрстуфхцчшщыэюя"
    base = ["500", "$", "в", "месяц"]
    for _ in range(400):
        word = "".join(rng.choice(alphabet) for _ in range(rng.randint(3, 8)))
        if word in _SERVICE_WORDS:
            continue
        position = rng.randint(0, len(base))
        text = " ".join([*base[:position], word, *base[position:]])
        with pytest.raises(ValueError):
            parse_amount_ru(text)


def test_amount_with_any_table_currency_code_is_exact():
    rng = random.Random(5)
    codes = sorted(HUNDRED_CURRENCIES | ZERO_DECIMAL_CURRENCIES)
    for _ in range(300):
        n = rng.randint(1, 10**7)
        code = rng.choice(codes)
        spaced = f"{n:,}".replace(",", " ")
        for text in (f"{n} {code}", f"{spaced} {code.lower()}", f"{n} {code} в месяц"):
            assert parse_amount_ru(text) == (D(n), code), text
