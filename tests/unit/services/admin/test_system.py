from target_ai.services.admin import SystemControl


async def test_stop_then_resume_requires_button(env):
    company = env.company()
    campaign = env.own_campaign(company)
    system = SystemControl(env.db, env.clock, env.executor())

    stop = await system.stop()
    assert env.fake.object(campaign)["status"] == "PAUSED" and env.db.settings.get("halted") is True
    ask = await system.request_resume()
    assert ask.status == "needs_approval" and env.db.settings.get("halted") is True
    done = await system.confirm_resume(ask.approval_id)

    assert stop.status == "done" and done.status == "done"
    assert env.db.settings.get("halted") is False
    assert env.fake.object(campaign)["status"] == "PAUSED"
    assert (await system.confirm_resume(ask.approval_id)).status == "error"


async def test_resume_when_not_halted_is_noop(env):
    reply = await SystemControl(env.db, env.clock, env.executor()).request_resume()

    assert reply.status == "done" and reply.approval_id is None
