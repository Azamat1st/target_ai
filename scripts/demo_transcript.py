"""Прогон демо-цикла на OfflineLLM и запись диалога «я -> бот» в docs/demo-transcript.md.

Без сети и без ключей: фейковый Telegram, фейковый Graph API, симулированные часы.
Запуск: scripts/demo-transcript.sh
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from pydantic import SecretStr  # noqa: E402
from support.fake_telegram import FakeTelegram  # noqa: E402

from target_ai.app import App  # noqa: E402
from target_ai.config import Settings  # noqa: E402
from target_ai.llm import OfflineLLM  # noqa: E402

OWNER = 111
OUT = ROOT / "docs" / "demo-transcript.md"
ADD_TEXT = "добавь кофейню @coffee_x, Берлин, 1500 евро в месяц, нужны сообщения, цена сообщения до 9 евро"
HEADER = """# Стенограмма демо-прогона

Сгенерировано `scripts/demo-transcript.sh`. Демо-уровень, без ключей и сети: модель — `OfflineLLM`,
Meta — фейковый Graph API, время — симулированные часы. Так выглядит цикл
«компания → паспорт → медиаплан → запуск → перемотка на 7 дней → оптимизатор и отчёт».
Строки «Я» — то, что пользователь пишет или нажимает; «Бот» — ответы; в квадратных скобках — кнопки под сообщением.
"""


def render_bot(messages) -> str:
    blocks = []
    for message in messages:
        buttons = " ".join(f"[{label}]" for label in message.labels)
        quoted = "\n".join(f"> {line}" if line else ">" for line in message.text.splitlines())
        blocks.append(f"**Бот:**\n\n{quoted}\n" + (f"\nКнопки: {buttons}\n" if buttons else ""))
    return "\n".join(blocks)


async def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="demo-transcript-"))
    settings = Settings(_env_file=None, telegram_bot_token=SecretStr("123456:DEMO"), telegram_allowed_ids=(OWNER,),
                        database_path=work / "demo.sqlite3")
    tg = FakeTelegram()

    async def no_sleep(_seconds):
        return None

    app = App.create(settings, overrides={"bot_session": tg.session, "llm": OfflineLLM(), "meta_sleeper": no_sleep,
                                          "tick_seconds": 3600, "media_dir": work / "media"})
    tg.bind(app)
    parts = [HEADER]
    step = 0

    async def act(title: str, action) -> None:
        nonlocal step
        step += 1
        seen = len(tg.sent)
        await action()
        parts.append(f"## Шаг {step}\n\n**Я:** {title}\n\n{render_bot(tg.sent[seen:])}")

    async def press(label: str) -> None:
        await act(f"нажимаю «{label}»", lambda: tg.press_button(OWNER, label))

    await act(f"«{ADD_TEXT}»", lambda: tg.say(OWNER, ADD_TEXT))
    for label in ("Демо Германия", "Studio Yoga", "Instagram, связанный", "Всё верно", "Пропустить", "Составить медиаплан"):
        await press(label)
    for answer in ("Кофейня GmbH", "Кофейня GmbH"):  # ЕС: получатель и плательщик рекламы
        if "🚀 Запустить" not in tg.last(OWNER).labels:
            await act(f"«{answer}»", lambda answer=answer: tg.say(OWNER, answer))
    await press("Запустить")
    await act("«⏩ +7 дней»", lambda: tg.say(OWNER, "⏩ +7 дней"))
    await act("«📊 Отчёт»", lambda: tg.say(OWNER, "📊 Отчёт"))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(parts).rstrip() + "\n", encoding="utf-8")
    app.db.close()
    sys.stdout.write(f"Записано: {OUT}\n")


if __name__ == "__main__":
    asyncio.run(main())
