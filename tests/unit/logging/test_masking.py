import logging

import pytest

from target_ai.logging_setup import setup_logging

BOT_TOKEN = "123456789:" + "A" * 35
CASES = {
    "meta": "EAAGm0PX4ZCpsBAKexampleTokenValue123",
    "anthropic": "sk-ant-api03-abcDEF_123-xyz",
    "bot": BOT_TOKEN,
    "access_token": "access_token=abc123secret",
    "proof": "appsecret_proof=0f1e2d3c4b5a",
}


@pytest.fixture
def root():
    logger = logging.getLogger()
    saved_handlers, saved_level = list(logger.handlers), logger.level
    yield logger
    logger.handlers[:] = saved_handlers
    logger.setLevel(saved_level)


def emit(root, capsys, message, *args):
    setup_logging("INFO")
    logging.getLogger("target_ai.test").info(message, *args)
    for handler in root.handlers:
        handler.flush()
    captured = capsys.readouterr()
    return captured.err + captured.out


@pytest.mark.parametrize("name", list(CASES))
def test_secret_shapes_are_masked_in_message(root, capsys, name):
    secret = CASES[name]
    out = emit(root, capsys, f"вызов с {secret} завершён")
    assert "завершён" in out
    assert secret.split("=")[-1] not in out
    assert "***" in out


def test_secret_in_format_args_is_masked(root, capsys):
    out = emit(root, capsys, "токен %s", CASES["meta"])
    assert CASES["meta"] not in out


def test_secret_in_exception_traceback_is_masked(root, capsys):
    setup_logging("INFO")
    try:
        raise RuntimeError(f"плохой {CASES['anthropic']}")
    except RuntimeError:
        logging.getLogger("target_ai.test").exception("сбой")
    captured = capsys.readouterr()
    assert CASES["anthropic"] not in captured.err + captured.out


def test_repeated_setup_does_not_duplicate_output(root, capsys):
    setup_logging("INFO")
    out = emit(root, capsys, "один раз")
    assert out.count("один раз") == 1


def test_httpx_url_logging_is_disabled(root):
    setup_logging("DEBUG")
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


def test_invalid_level_raises(root):
    with pytest.raises(ValueError):
        setup_logging("LOUD")


def log_with_stack(message):
    logging.getLogger("target_ai.test").info(message, stack_info=True)


def test_stack_info_is_masked(root, capsys):
    setup_logging("INFO")
    # Секрет стоит буквальной строкой в этой строке исходника — её печатает стек.
    log_with_stack("вызов") or "EAAGm0PX4ZCpsBAKexampleTokenValue123"
    for handler in root.handlers:
        handler.flush()
    captured = capsys.readouterr()
    out = captured.err + captured.out
    assert "Stack (most recent call last)" in out
    assert "вызов" in out
    assert CASES["meta"] not in out and "***" in out
