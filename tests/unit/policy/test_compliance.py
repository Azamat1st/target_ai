import pytest

from target_ai.policy import SpecialCategory, classify_special_category, controller_check, is_forbidden


def rules(report) -> set[int]:
    return {finding.rule for finding in report.findings}


@pytest.mark.parametrize(
    "text",
    [
        "Вы страдаете от акне? Мы поможем",
        "Устали от долгов? Решение есть",
        "У вас лишний вес? Запишитесь",
        "Ты одинок? Приходи на встречу",
        "Вам уже за 50? Вот что важно",
        "Ваш диагноз не приговор",
        "Вы бедны, а хотите больше?",
        "Are you suffering from back pain?",
    ],
)
def test_text_that_points_at_personal_traits_breaks_rule_1(text):
    assert 1 in rules(controller_check(text))


@pytest.mark.parametrize(
    "text",
    ["Мы помогаем похудеть без стресса", "Вы можете записаться онлайн", "У нас есть кофе и десерты"],
)
def test_neutral_text_passes_rule_1(text):
    assert controller_check(text).findings == ()


def test_caps_share_over_thirty_percent_breaks_rule_5():
    assert 5 in rules(controller_check("АААА бббббб"))      # 40%
    assert controller_check("ААА ббббббб").findings == ()    # ровно 30% — допустимо


def test_very_short_all_caps_text_is_not_flagged():
    assert controller_check("SALE").findings == ()


@pytest.mark.parametrize("text", ["Только сегодня!!!", "Невероятно?!?", "Ура!!!!!"])
def test_repeated_exclamations_break_rule_5(text):
    assert 5 in rules(controller_check(text))


def test_two_exclamation_marks_are_fine():
    assert controller_check("Ждём вас!!").findings == ()


def test_length_limits_are_text_2200_and_headline_40():
    assert controller_check("а" * 2200).findings == ()
    assert 8 in rules(controller_check("а" * 2201))
    assert controller_check("ok text", headline="б" * 40).findings == ()
    assert 8 in rules(controller_check("ok text", headline="б" * 41))


def test_language_must_match_voice_script():
    assert 9 in rules(controller_check("Fresh coffee every morning in town", language="ru"))
    assert 9 in rules(controller_check("Свежий кофе каждое утро", language="en"))
    assert controller_check("Свежий кофе каждое утро", language="ru").findings == ()
    assert controller_check("Nike кроссовки со скидкой для вас", language="ru").findings == ()


def test_language_check_is_skipped_when_unknown_or_no_letters():
    assert controller_check("Fresh coffee", language=None).findings == ()
    assert controller_check("Fresh coffee", language="ja").findings == ()
    assert controller_check("12345 !!", language="ru").findings == ()


@pytest.mark.parametrize("text", ["Крафтовое пиво в баре", "Знакомства для взрослых", "БАДы для сна"])
def test_restricted_content_requires_adult_audience(text):
    assert controller_check(text).requires_adult is True


def test_regular_content_does_not_require_adult_audience():
    assert controller_check("Свежие булочки").requires_adult is False


def test_findings_are_russian_sentences():
    (finding,) = controller_check("Только сегодня!!!").findings
    assert finding.message_ru and finding.message_ru[0].isupper()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Ипотека от банка", SpecialCategory.FINANCIAL_PRODUCTS_SERVICES),
        ("Кредит наличными за час", SpecialCategory.FINANCIAL_PRODUCTS_SERVICES),
        ("Аренда квартир посуточно", SpecialCategory.HOUSING),
        ("Новостройки от застройщика", SpecialCategory.HOUSING),
        ("Открыта вакансия менеджера", SpecialCategory.EMPLOYMENT),
        ("Ищем сотрудников в команду", SpecialCategory.EMPLOYMENT),
        ("Подработка для студентов", SpecialCategory.EMPLOYMENT),
        ("Выборы в совет дома", SpecialCategory.ISSUES_ELECTIONS_POLITICS),
        ("Голосуйте за кандидата в депутаты", SpecialCategory.ISSUES_ELECTIONS_POLITICS),
        ("Казино онлайн", SpecialCategory.ONLINE_GAMBLING_AND_GAMING),
        ("Ставки на спорт без комиссии", SpecialCategory.ONLINE_GAMBLING_AND_GAMING),
    ],
)
def test_keywords_define_the_special_category(text, expected):
    assert classify_special_category([text]) is expected


@pytest.mark.parametrize(
    "text",
    ["Кофе и десерты", "Большой выбор кофе", "Партия товара уже в магазине", "Наша работа — вкусно"],
)
def test_ordinary_text_has_no_special_category(text):
    assert classify_special_category([text]) is None


def test_forbidden_category_wins_over_restricted_one():
    result = classify_special_category(["Кредит на ставки", "Казино онлайн"])
    assert result is SpecialCategory.ONLINE_GAMBLING_AND_GAMING


def test_only_politics_and_gambling_are_forbidden():
    forbidden = {c for c in SpecialCategory if is_forbidden(c)}
    assert forbidden == {SpecialCategory.ISSUES_ELECTIONS_POLITICS, SpecialCategory.ONLINE_GAMBLING_AND_GAMING}
