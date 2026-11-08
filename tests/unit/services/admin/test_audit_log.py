from datetime import UTC, datetime

from target_ai.services.admin import AuditLog
from target_ai.storage import AuditEntry


def add(env, n, *, actor="user", action="pause", company_id=None, **data):
    env.db.audit.create(AuditEntry(actor=actor, action=action, company_id=company_id,
                                   data={"outcome": "ok", **data},
                                   created_at=datetime(2026, 11, 12, 14, 3, tzinfo=UTC)))


async def test_line_format_in_russian(env):
    company = env.company()
    add(env, 1, actor="optimizer", action="set_budget", company_id=company.id, summary="Интересы: −20% бюджета")

    page = await AuditLog(env.db, env.settings()).page()

    assert page.lines == ("12.11 14:03 · Оптимизатор · Кофейня · Бюджет: Интересы: −20% бюджета",)


async def test_pages_of_ten_newest_first(env):
    for i in range(25):
        add(env, i, message=f"запись {i}")
    log = AuditLog(env.db, env.settings())

    first, second, third = await log.page(0), await log.page(1), await log.page(2)

    assert len(first.lines) == 10 and first.has_next and "запись 24" in first.lines[0]
    assert len(second.lines) == 10 and second.has_next
    assert len(third.lines) == 5 and not third.has_next
    assert ("← Новее", "log:page:1:0") in third.buttons[0]


async def test_filter_by_company(env):
    first, second = env.company(), env.company(name="Пекарня")
    add(env, 1, company_id=first.id)
    add(env, 2, company_id=second.id)

    page = await AuditLog(env.db, env.settings()).page(company_id=second.id)

    assert len(page.lines) == 1 and "Пекарня" in page.lines[0]


async def test_failed_action_is_marked_and_empty_log_says_so(env):
    log = AuditLog(env.db, env.settings())
    assert (await log.page()).text == "Журнал пока пуст."
    env.db.audit.create(AuditEntry(actor="system", action="halt_all", data={"outcome": "error"},
                                   created_at=env.clock.now()))

    assert (await log.page()).lines[0].endswith("Стоп-кран — ошибка")
