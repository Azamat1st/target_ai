from target_ai.fakemeta import errors as fe
from target_ai.llm.errors import LLMRequestError
from target_ai.services.admin import ConnectionCheck


def texts(report):
    return "\n".join(line.text for line in report.lines)


async def test_healthy_setup_allows_test_and_live(env):
    report = await env.checker().run()

    assert report.can_test and report.can_live
    assert not report.blockers
    assert "можно переходить и в тестовый, и в боевой" in report.verdict


async def test_missing_meta_token_blocks_everything_with_instruction(env):
    report = await env.checker(meta_access_token=None).run()

    assert not report.can_test and not report.can_live
    assert "META_ACCESS_TOKEN" in texts(report) and "Что сделать" in texts(report)


async def test_expired_token_is_reported_as_expired(env):
    env.fake.state.data["token"]["expires_at"] = 1_700_000_000  # ноябрь 2023
    report = await env.checker().run()

    assert not report.can_test
    assert "Токен Meta истёк" in texts(report)


async def test_invalid_token_is_reported(env):
    env.fake.set_token_valid(False)
    report = await env.checker().run()

    assert not report.can_test
    assert "Токен Meta не принят" in texts(report)


async def test_missing_pages_manage_ads_names_the_permission(env):
    env.set_scopes("ads_management", "ads_read", "pages_show_list", "pages_read_engagement",
                   "pages_read_user_content", "instagram_basic", "instagram_manage_insights")
    report = await env.checker().run()

    assert report.can_test and not report.can_live
    assert "pages_manage_ads" in texts(report)
    assert "в тестовый уровень можно" not in report.verdict
    assert "боевой" in report.verdict and "тестовому они не мешают" in report.verdict


async def test_unsettled_account_is_reported_as_payment_problem(env):
    env.fake.state.data["accounts"]["act_100000000000001"]["account_status"] = 3
    report = await env.checker().run()

    assert not report.can_live
    assert "проблема с оплатой" in texts(report)


async def test_account_without_funding_source_is_reported(env):
    env.fake.state.data["accounts"]["act_100000000000001"]["funding_source"] = None
    report = await env.checker().run()

    assert "нет способа оплаты" in texts(report) and not report.can_live


async def test_page_without_instagram_is_reported(env):
    page = next(iter(env.fake.state.data["pages"].values()))
    page.pop("instagram_business_account")
    report = await env.checker().run()

    assert "Instagram-аккаунт не привязан" in texts(report) and not report.can_live


async def test_ad_account_without_instagram_access_is_reported(env):
    env.fake.inject_error(r"POST act_\d+/adcreatives", fe.ig_access_denied(), times=5)
    report = await env.checker().run()

    assert "нет доступа к Instagram-аккаунту" in texts(report)
    assert not report.can_live


async def test_instagram_access_is_ok_when_probe_passes(env):
    report = await env.checker().run()

    assert "доступ есть" in texts(report)


async def test_missing_probe_gives_warning_not_false_green(env):
    report = await ConnectionCheck(env.settings(), lambda v: env.client, env.clock).run()

    assert "не проверен" in texts(report) and "доступ есть" not in texts(report)


async def test_missing_claude_key_blocks_live_only(env):
    report = await env.checker(anthropic_api_key=None).run()

    assert report.can_test and not report.can_live
    assert "ANTHROPIC_API_KEY" in texts(report)


async def test_rejected_claude_key_is_reported(env):
    async def rejected():
        raise LLMRequestError(401)

    report = await env.checker(rejected).run()

    assert "Ключ Claude не принят" in texts(report) and not report.can_live


async def test_unreachable_claude_is_a_warning_not_a_blocker(env):
    async def offline():
        raise ConnectionError("нет сети")

    report = await env.checker(offline).run()

    assert report.can_live
    assert "проверить его сейчас не удалось" in texts(report)


async def test_token_check_error_blocks_test_level_too_and_verdict_does_not_claim_otherwise(env):
    env.fake.inject_error(r"GET .*", fe.permission_denied(), times=1)  # ошибка Meta на проверке самого токена

    report = await env.checker().run()

    assert not report.can_test and not report.can_live
    assert "тестовому они не мешают" not in report.verdict and "мешают и тестовому" in report.verdict
