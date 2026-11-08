from target_ai.llm import OfflineLLM
from target_ai.services.intake import MAX_FILE_BYTES, callback_data, parse_callback, split_message

from .conftest import CHAT


def test_split_message_keeps_every_part_under_telegram_limit():
    blocks = ["Раздел один\n" + "строка\n" * 300, "x" * 9000, "короткий"]

    parts = split_message(blocks)

    assert len(parts) > 3 and all(len(p) <= 4096 for p in parts)
    assert "".join(parts).count("x") == 9000


def test_callback_roundtrip_and_foreign_format():
    data = callback_data("a" * 32, "go")

    assert parse_callback(data) == ("a" * 32, "go")
    assert parse_callback("launch:continue:5") is None and parse_callback("ap::go") is None


async def test_file_over_20mb_is_refused_with_request_to_send_smaller(env):
    env.company()
    intake = env.intake(OfflineLLM())

    await intake.handle_media(CHAT, "video", None, "big.mp4", size=MAX_FILE_BYTES + 1)

    assert "20 МБ" in env.last() and env.db.media.list() == []


async def test_photo_is_attached_to_the_only_company(env):
    company = env.company()
    intake = env.intake(OfflineLLM())

    await intake.handle_media(CHAT, "image", b"jpegdata", "a.jpg", caption="латте")

    asset = env.db.media.list(company_id=company.id)[0]
    assert (asset.kind, asset.caption) == ("image", "латте")
    assert open(asset.path, "rb").read() == b"jpegdata"


async def test_photo_with_two_companies_asks_which_one(env):
    first, second = env.company(), env.company("USD")
    intake = env.intake(OfflineLLM())

    await intake.handle_media(CHAT, "image", b"jpegdata", "a.jpg")
    assert "К какой компании" in env.last() and env.db.media.list() == []
    await env.press(intake, first.name)

    assert [m.company_id for m in env.db.media.list()] == [first.id] and second.id != first.id
