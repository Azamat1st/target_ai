from target_ai.money import Money
from target_ai.services.admin import Companies
from target_ai.storage import DailyStat, MetaObject


def service(env):
    return Companies(env.db, env.clock, env.settings(), env.executor())


class StubExecutor:
    """Шов к Исполнителю: возвращает заданные результаты и запоминает вызовы."""

    def __init__(self, env, result):
        self.env, self.result, self.calls = env, result, []
        self.real = env.executor()

    async def set_monthly_limit(self, company_id, limit, approval_id=None, *, actor="user"):
        self.calls.append(("limit", limit, approval_id))
        return self.result(approval_id)

    async def resume_company(self, company_id, approval_id=None, *, actor="user"):
        self.calls.append(("resume", approval_id))
        if approval_id:  # как настоящий Исполнитель: подтверждение потребляется
            self.env.db.approvals.consume(approval_id, self.env.clock.now())
        return self.result(approval_id)

    def create_approval(self, needs, *, company_id, ttl=None, intent=None):
        return self.real.create_approval(needs, company_id=company_id, intent=intent)


def needs():
    from target_ai.policy import NeedsApproval
    return NeedsApproval(kind="spend_increase", summary_ru="Расход вырастет.", key="k")


async def test_list_shows_name_status_mode_spend_limit_and_level(env):
    company = env.company()
    env.own_campaign(company)
    adset = env.db.objects.create(MetaObject(company_id=company.id, meta_id="as1", kind="adset", name="g",
                                             created_at=env.clock.now()))
    env.db.stats.upsert(DailyStat(meta_id=adset.meta_id, company_id=company.id, date="2026-11-05",
                                  spend=Money(2_500, "USD")))
    env.db.stats.upsert(DailyStat(meta_id=adset.meta_id, company_id=company.id, date="2026-10-30",
                                  spend=Money(9_900, "USD")))

    view = await service(env).list()

    assert "Уровень работы: Демо" in view.text
    assert "Кофейня · Работает · С подтверждением · 25,00" in view.text and "1 000,00" in view.text
    assert view.buttons == [[("Кофейня", f"cmp:card:{company.id}")]]


async def test_demo_company_is_marked_and_card_has_actions(env):
    company = env.company(demo=True)

    view = await service(env).list()
    card = await service(env).card(company.id)

    assert "демо" in view.text
    callbacks = [cb for row in card.buttons for _, cb in row]
    assert f"cmp:mode:{company.id}:autonomous" in callbacks and f"cmp:resume:{company.id}" in callbacks


async def test_pause_is_immediate_through_executor(env):
    company = env.company()
    campaign = env.own_campaign(company)

    reply = await service(env).pause(company.id)

    assert reply.status == "done"
    assert env.fake.object(campaign)["status"] == "PAUSED"


async def test_resume_asks_for_button_then_runs_with_approval(env):
    from target_ai.executor.results import ChangeResult
    stub = StubExecutor(env, lambda aid: ChangeResult("done", "Возобновлено.") if aid
                        else ChangeResult("needs_approval", "Расход вырастет.", needs()))
    companies = Companies(env.db, env.clock, env.settings(), stub)
    company = env.company()

    ask = await companies.resume(company.id)
    assert ask.status == "needs_approval" and ask.buttons[0][0][1] == f"cmp:ok:{ask.approval_id}"
    done = await companies.confirm(ask.approval_id)

    assert done.status == "done"
    assert stub.calls == [("resume", None), ("resume", ask.approval_id)]
    assert (await companies.confirm(ask.approval_id)).status == "error"


async def test_limit_raise_confirms_amount_then_asks_money_permission(env):
    from target_ai.executor.results import ChangeResult
    stub = StubExecutor(env, lambda aid: ChangeResult("done", "Лимит изменён.") if aid
                        else ChangeResult("needs_approval", "Расход вырастет.", needs()))
    companies = Companies(env.db, env.clock, env.settings(), stub)
    company = env.company()

    amount = await companies.set_limit(company.id, "1 500,50")
    assert "1 500,50" in amount.text and "USD" in amount.text and amount.buttons[0][0][0] == "✅ Всё верно"
    assert stub.calls == []
    growth = await companies.confirm(amount.approval_id)
    assert growth.status == "needs_approval"
    done = await companies.confirm(growth.approval_id)

    assert done.status == "done"
    assert stub.calls == [("limit", Money(150_050, "USD"), None), ("limit", Money(150_050, "USD"), growth.approval_id)]


async def test_limit_decrease_needs_only_amount_confirmation(env):
    env.db.settings.set("level", "live")
    company = env.company()
    companies = service(env)

    ask = await companies.set_limit(company.id, "500")
    assert ask.status == "needs_approval"
    assert env.db.companies.get(company.id).monthly_limit == Money(100_000, "USD")
    reply = await companies.confirm(ask.approval_id)

    assert reply.status == "done", reply.text
    assert env.db.companies.get(company.id).monthly_limit == Money(50_000, "USD")


async def test_limit_rejects_garbage_foreign_currency_and_zero(env):
    company = env.company()
    companies = service(env)

    for text in ("много", "100 евро", "0"):
        assert (await companies.set_limit(company.id, text)).status == "error"
    assert env.db.companies.get(company.id).monthly_limit == Money(100_000, "USD")


async def test_autonomous_needs_warning_and_confirmation_but_off_is_immediate(env):
    company = env.company()
    companies = service(env)

    ask = await companies.set_mode(company.id, "autonomous")
    assert ask.status == "needs_approval" and "сам" in ask.text
    assert env.db.companies.get(company.id).mode == "confirm"
    await companies.confirm(ask.approval_id)
    assert env.db.companies.get(company.id).mode == "autonomous"

    off = await companies.set_mode(company.id, "confirm")
    assert off.status == "done" and env.db.companies.get(company.id).mode == "confirm"
    assert [e.action for e in env.db.audit.list()] == ["mode_change", "mode_change"]


async def test_cancelled_confirmation_changes_nothing(env):
    company = env.company()
    companies = service(env)
    ask = await companies.set_mode(company.id, "autonomous")

    await companies.cancel(ask.approval_id)

    assert (await companies.confirm(ask.approval_id)).status == "error"
    assert env.db.companies.get(company.id).mode == "confirm"


async def test_confirmation_intent_lives_in_approval_payload_not_settings(env):
    company = env.company()
    ask = await service(env).set_mode(company.id, "autonomous")

    assert env.db.approvals.get(ask.approval_id).payload["intent"]["mode"] == "autonomous"
    assert not [k for k in env.db.settings.list() if k.startswith("admin_pending")]


async def test_card_has_new_media_plan_button(env):
    company = env.company()

    card = await service(env).card(company.id)

    assert ("🧭 Новый медиаплан", f"cmp:plan:{company.id}") in [b for row in card.buttons for b in row]


async def test_card_after_month_limit_is_spent_says_paused_by_limit_not_working(env):
    company = env.company()  # лимит 1 000,00 $
    env.own_campaign(company)
    adset = env.db.objects.create(MetaObject(company_id=company.id, meta_id="as1", kind="adset", name="g",
                                             created_at=env.clock.now()))
    env.db.stats.upsert(DailyStat(meta_id=adset.meta_id, company_id=company.id, date="2026-11-05",
                                  spend=Money(120_000, "USD")))

    card = await service(env).card(company.id)

    assert "Статус: на паузе — лимит месяца исчерпан" in card.text
    assert "Работает" not in card.text


def test_raw_meta_object_ids_are_hidden_from_user_text():
    from target_ai.services.admin.common import hide_meta_ids

    text = hide_meta_ids("120200000000001: защитный лимит 5,00 $")

    assert "1202000" not in text and "защитный лимит кампании" in text.lower()
