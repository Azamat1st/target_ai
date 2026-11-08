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


async def test_started_and_finished_collapse_into_one_human_line_without_internal_marks(env):
    company = env.company()
    env.db.audit.create(AuditEntry(actor="optimizer", action="decision:set_budget", company_id=company.id,
                                   data={"outcome": "intent", "reason": "Группа [ta:ab12:L3:adset:1] −20%"},
                                   created_at=env.clock.now()))
    env.db.audit.create(AuditEntry(actor="optimizer", action="decision:set_budget", company_id=company.id,
                                   data={"outcome": "ok", "reason": "Группа [ta:ab12:L3:adset:1] −20%"},
                                   created_at=env.clock.now()))
    env.db.audit.create(AuditEntry(actor="system", action="launch:start", company_id=company.id,
                                   data={"outcome": "ok", "launch_id": 3}, created_at=env.clock.now()))

    page = await AuditLog(env.db, env.settings()).page()
    text = "\n".join(page.lines)

    assert "decision:" not in text and "launch:" not in text and "[ta:" not in text
    assert "начато" not in text and text.count("−20%") == 1


async def test_unknown_action_shows_object_kind_and_untagged_name_never_a_raw_id_or_bare_word(env):
    from target_ai.storage import MetaObject
    company = env.company()
    env.db.objects.create(MetaObject(company_id=company.id, meta_id="120000000000046", kind="campaign",
                                     name="Кофейня [ta:ab12:L1:campaign]"))
    add(env, 1, action="activate", company_id=company.id, object="120000000000046", object_kind="campaign")
    add(env, 2, action="spend_cap", company_id=company.id, campaign="120000000000046", cap=49541)
    add(env, 3, action="create:adset:2", company_id=company.id, step="adset:2", launch_id=1)
    add(env, 4, action="mystery:thing", company_id=company.id, object="999")

    text = "\n".join((await AuditLog(env.db, env.settings()).page()).lines)

    assert "120000000000046" not in text and "[ta:" not in text and "999" not in text
    assert "Включение: кампания «Кофейня»" in text and "Создана группа" in text
    assert "Кофейня · Действие" not in text and "Служебная операция" in text
