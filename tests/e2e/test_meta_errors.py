"""Ошибки Meta сквозь бота: пользователь получает понятное русское сообщение, а деньги не тратятся."""

from __future__ import annotations

import re

from target_ai.fakemeta import errors

from .conftest import OWNER, own_objects, reach_plan

CYRILLIC = re.compile(r"[а-яА-ЯёЁ]{4,}")


def tail_text(e, count: int = 3) -> str:
    return "\n".join(m.text for m in e.tg.to(OWNER)[-count:])


async def launch_with(e, error, path=r"POST .*/campaigns", times=1):
    await reach_plan(e, "EUR", 3)
    e.fake.inject_error(path, error, times=times)
    await e.tg.press_button(OWNER, "Запустить")


async def test_expired_token_tells_the_user_to_renew_it_and_nothing_is_live(make_e2e):
    e = make_e2e()

    await launch_with(e, errors.token_expired())

    text = tail_text(e)
    assert "Токен Meta истёк" in text and "META_ACCESS_TOKEN" in text
    assert not [o for o in own_objects(e, "campaign") if o["status"] == "ACTIVE"]


async def test_missing_permission_is_explained_in_russian(make_e2e):
    e = make_e2e()

    await launch_with(e, errors.permission_denied())

    text = tail_text(e)
    assert CYRILLIC.search(text) and ("прав" in text or "доступ" in text), text
    assert not [o for o in own_objects(e, "campaign") if o["status"] == "ACTIVE"]


async def test_rate_limit_is_retried_and_the_launch_succeeds(make_e2e):
    e = make_e2e()

    await launch_with(e, errors.rate_limited(), times=2)

    assert "Кампания создана и включена" in tail_text(e)
    assert len(own_objects(e, "campaign")) == 1
    assert len(e.fake.requests_matching(r"POST .*/campaigns")) >= 3  # два отказа по лимиту и успешная попытка


async def test_disapproved_ad_reaches_the_user_with_the_reason(make_e2e):
    e = make_e2e()
    await reach_plan(e, "EUR", 3)
    await e.tg.press_button(OWNER, "Запустить")
    ad = own_objects(e, "ad")[0]

    e.fake.disapprove_ad(ad["id"], "Текст нарушает правила", issue_summary="Запрещённое утверждение")
    await e.tg.say(OWNER, "⏩ +1 день")

    text = e.tg.all_text(OWNER)
    assert "отклон" in text.lower(), text[-800:]
    assert "Запрещённое утверждение" in text or "Текст нарушает правила" in text, text[-800:]
