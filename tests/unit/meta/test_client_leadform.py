import logging

import pytest

from target_ai.meta.errors import MetaError

FORM = {
    "name": "Форма [ta:abcd1234:L1:leadform]",
    "questions": [{"type": "FULL_NAME"}, {"type": "PHONE"}],
    "privacy_policy": {"url": "https://shop.example/privacy", "link_text": "Политика конфиденциальности"},
}


@pytest.fixture
def page(fake):
    return next(iter(fake.state.data["pages"].values()))


async def test_page_token_is_read_from_page_and_never_logged(client, fake, page, caplog):
    caplog.set_level(logging.DEBUG)

    token = await client.get_page_token(page["id"])

    assert token == page["access_token"]
    request = fake.requests_matching(rf"GET {page['id']}$")[-1]
    assert request.params["fields"] == "access_token"
    assert token not in caplog.text


async def test_leadgen_form_is_created_with_the_page_token_not_the_user_token(client, fake, page):
    page_token = await client.get_page_token(page["id"])

    form_id = await client.create_leadgen_form(page["id"], page_token, FORM, idempotency_key="k-1")

    request = fake.requests_matching(rf"POST {page['id']}/leadgen_forms")[-1]
    assert request.headers["authorization"] == f"Bearer {page_token}"
    assert request.headers["x-client-idempotent-key"] == "k-1"
    assert request.body["privacy_policy"]["url"] == "https://shop.example/privacy"
    assert fake.object(form_id)["kind"] == "leadform"


async def test_leadgen_form_with_the_user_token_is_a_permission_error(client, page):
    with pytest.raises(MetaError) as caught:
        await client.create_leadgen_form(page["id"], "tok-secret", FORM)

    assert caught.value.kind == "permission"


async def test_leadgen_form_without_privacy_url_is_rejected_with_blame_field(client, page):
    token = await client.get_page_token(page["id"])
    bad = {**FORM, "privacy_policy": {"link_text": "x"}}

    with pytest.raises(MetaError) as caught:
        await client.create_leadgen_form(page["id"], token, bad)

    assert caught.value.code == 100 and "privacy_policy.url" in caught.value.blame_fields


async def test_validate_only_client_refuses_leadgen_form_before_any_request(fake, make_client, page):
    before = len(fake.requests)

    with pytest.raises(MetaError) as caught:
        await make_client(validate_only=True).create_leadgen_form(page["id"], "t", FORM)

    assert caught.value.kind == "validate_only_unsupported" and len(fake.requests) == before


async def test_list_leadgen_forms_returns_created_forms_with_time(client, page):
    token = await client.get_page_token(page["id"])
    form_id = await client.create_leadgen_form(page["id"], token, FORM)

    rows = await client.list_leadgen_forms(page["id"], token)

    assert [(r["id"], r["name"]) for r in rows] == [(form_id, FORM["name"])] and rows[0]["created_time"]
