from target_ai.services.admin import LevelSwitch


def switch(env, **overrides):
    return LevelSwitch(env.db, env.clock, env.settings(**overrides), env.checker(**overrides), env.executor())


async def test_default_level_is_demo(env):
    assert await switch(env).current() == "demo"


async def test_demo_to_test_requires_meta_token(env):
    reply = await switch(env, meta_access_token=None).request("test")

    assert reply.status == "denied" and "META_ACCESS_TOKEN" in reply.text
    assert env.db.settings.get("level", "demo") == "demo"


async def test_demo_to_test_switches_and_pauses_demo_companies_in_simulator(env):
    demo = env.company(demo=True)
    campaign = env.own_campaign(demo)

    reply = await switch(env).request("test")

    assert reply.status == "done"
    assert env.db.settings.get("level") == "test"
    assert env.fake.object(campaign)["status"] == "PAUSED"
    assert "демо" in reply.text


async def test_live_is_denied_while_connection_has_blockers(env):
    reply = await switch(env, anthropic_api_key=None).request("live")

    assert reply.status == "denied" and "ANTHROPIC_API_KEY" in reply.text
    assert env.db.settings.get("level", "demo") == "demo"


async def test_live_needs_button_and_explains_what_changes(env):
    levels = switch(env)

    ask = await levels.request("live")

    assert ask.status == "needs_approval" and "Что изменится" in ask.text
    assert env.db.settings.get("level", "demo") == "demo"
    done = await levels.confirm(ask.approval_id)
    assert done.status == "done" and env.db.settings.get("level") == "live"


async def test_confirmation_is_single_use(env):
    levels = switch(env)
    ask = await levels.request("live")
    await levels.confirm(ask.approval_id)

    again = await levels.confirm(ask.approval_id)

    assert again.status == "error"


async def test_live_confirmation_rechecks_connection(env):
    levels = switch(env)
    ask = await levels.request("live")
    env.fake.state.data["accounts"]["act_100000000000001"]["account_status"] = 2

    reply = await levels.confirm(ask.approval_id)

    assert reply.status == "denied" and env.db.settings.get("level", "demo") == "demo"


async def test_leaving_live_asks_confirmation_and_pauses_live_campaigns(env):
    env.db.settings.set("level", "live")
    live = env.company()
    demo = env.company(name="Демо", demo=True)
    live_campaign, demo_campaign = env.own_campaign(live), env.own_campaign(demo)
    levels = switch(env)

    ask = await levels.request("demo")
    assert ask.status == "needs_approval"
    assert env.fake.object(live_campaign)["status"] == "ACTIVE"
    done = await levels.confirm(ask.approval_id)

    assert done.status == "done" and env.db.settings.get("level") == "demo"
    assert env.fake.object(live_campaign)["status"] == "PAUSED"
    assert env.fake.object(demo_campaign)["status"] == "ACTIVE"


async def test_level_stays_live_when_a_campaign_cannot_be_paused(env):
    env.db.settings.set("level", "live")
    company = env.company()
    campaign = env.own_campaign(company)
    from target_ai.fakemeta.errors import server_error
    env.fake.inject_error(rf"POST {campaign}", server_error(), times=20)
    levels = switch(env)
    ask = await levels.request("test")

    reply = await levels.confirm(ask.approval_id)

    assert reply.status == "error" and "Стоп-кран" in reply.text
    assert env.db.settings.get("level") == "live"


async def test_level_change_is_written_to_journal(env):
    await switch(env).request("test")

    assert any(e.action == "level_change" for e in env.db.audit.list())
