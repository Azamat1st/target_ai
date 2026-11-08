"""Сверка README с кодом: переменные из .env.example, команды бота, файлы Docker."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
README = (ROOT / "README.md").read_text(encoding="utf-8")


def _env_names(text: str) -> set[str]:
    return set(re.findall(r"^\s*#?\s*([A-Z][A-Z0-9_]+)=", text, re.M))


def test_readme_env_keys_exist_in_env_example() -> None:
    in_readme = set(re.findall(r"`([A-Z][A-Z0-9_]{3,})=", README))
    known = _env_names((ROOT / ".env.example").read_text(encoding="utf-8"))
    assert in_readme, "в README не нашлось ни одной переменной"
    assert in_readme <= known, f"нет в .env.example: {sorted(in_readme - known)}"


def test_every_env_example_key_is_explained_in_readme() -> None:
    known = _env_names((ROOT / ".env.example").read_text(encoding="utf-8"))
    missing = {name for name in known if f"`{name}=" not in README}
    assert not missing, f"не описаны в README: {sorted(missing)}"


def test_readme_commands_exist_in_bot_code() -> None:
    code = (ROOT / "src/target_ai/bot/commands.py").read_text(encoding="utf-8")
    registered = set(re.findall(r'Command\("([a-z_]+)"\)', code))
    botfather = {"newbot", "revoke"}  # команды чужого бота @BotFather, не нашего
    in_readme = set(re.findall(r"`/([a-z_]+)", README)) - botfather
    assert registered, "команды бота не найдены в коде"
    assert in_readme <= registered, f"нет в боте: {sorted(in_readme - registered)}"
    assert registered <= in_readme, f"не описаны в README: {sorted(registered - in_readme)}"


def _src_text() -> str:
    return "\n".join(f.read_text(encoding="utf-8") for f in (ROOT / "src/target_ai").rglob("*.py"))


def test_readme_bot_buttons_exist_in_code() -> None:
    """Каждая подпись кнопки бота в «…» (с эмодзи в начале) есть в коде дословно."""
    code = _src_text()
    quoted = set(re.findall(r"«([^»]+)»", README))
    with_emoji = {q for q in quoted if ord(q[0]) > 0x2000}
    assert {"➕ Компания", "🔌 Проверить подключение", "🛑 Стоп-кран", "⏩ +1 день", "✅ Включить боевой"} <= with_emoji
    missing = {q for q in with_emoji if q not in code}
    assert not missing, f"кнопок нет в коде: {sorted(missing)}"


def test_readme_plain_button_names_exist_in_code() -> None:
    code = _src_text()
    for name in ("Всё верно", "Запустить", "С подтверждением", "Режим →", "Возобновить", "Автономный"):
        assert f"«{name}" in README or name in README
        assert name in code, name


def test_readme_shell_commands_match_project_files() -> None:
    assert "./start.sh" in README and (ROOT / "start.sh").exists()
    assert "docker compose up -d" in README
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "env_file: .env" in compose and "./data:/app/data" in compose and "unless-stopped" in compose


def test_docker_files_keep_secrets_and_data_out_of_image() -> None:
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    for entry in (".env", ".venv/", "data/", ".autopilot/"):
        assert entry in ignored
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "uv sync --frozen --no-dev" in dockerfile and "USER bot" in dockerfile


def test_readme_quotes_the_refusal_text_the_bot_sends_to_strangers() -> None:
    from target_ai.bot.middleware import DENIED_TEXT

    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert f"«{DENIED_TEXT}»" in readme and "Остальным он не отвечает" not in readme
