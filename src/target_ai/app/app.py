"""Сборка приложения: настройки → база → часы и шлюзы по уровню → сервисы → бот → планировщик."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.client.session.base import BaseSession

from target_ai.clock import RealClock, SimClock
from target_ai.config import Settings
from target_ai.executor import Executor
from target_ai.fakemeta import FakeGraphAPI, Simulator
from target_ai.llm import ClaudeLLM, OfflineLLM
from target_ai.notify import TelegramNotifier
from target_ai.scheduler import Scheduler
from target_ai.services.admin import (
    AuditLog, ClaudeKeyProbe, Companies, ConnectionCheck, SettingsService, SystemControl,
)
from target_ai.services.admin.common import level_title
from target_ai.services.intake import Intake
from target_ai.services.ops import DemoTime, Ops, OpsContext
from target_ai.storage import Database

from .clock import LevelClock
from .gateways import LevelGateways
from .levels import ClockAwareLevelSwitch
from .scheduling import DEFAULT_TICK_SECONDS, LockedScheduler, Ticker
from target_ai.config.runtime import DEFAULT_LEVEL, SETTING_LEVEL

log = logging.getLogger(__name__)

OVERRIDE_KEYS = frozenset({"llm", "meta_transport", "fake_meta", "clock", "base_clock", "bot_session",
                           "notifier", "claude_probe", "meta_sleeper", "media_dir", "tick_seconds"})
POLLING_TIMEOUT_SECONDS = 10


class AppConfigError(RuntimeError):
    """Не хватает настроек для запуска; текст — по-русски, что вписать в `.env`."""


class App:
    """Все зависимости приложения. Создавать через `App.create`; запускать `await app.run()`."""

    settings: Settings
    db: Database
    clock: Any
    bot: Bot
    dispatcher: Any
    notifier: Any
    telegram: TelegramNotifier | None
    fake_meta: FakeGraphAPI
    gateways: LevelGateways
    llm: Any
    executor: Executor
    intake: Intake
    ops: Ops
    scheduler: LockedScheduler
    demo: DemoTime
    connection: ConnectionCheck
    levels: ClockAwareLevelSwitch
    settings_service: SettingsService
    companies: Companies
    audit_log: AuditLog
    system: SystemControl

    @classmethod
    def create(cls, settings: Settings, overrides: Mapping[str, Any] | None = None) -> App:
        """`overrides`: llm, meta_transport, fake_meta, clock, base_clock, bot_session, notifier, claude_probe,
        meta_sleeper, media_dir, tick_seconds — подмены для тестов (шов №1).

        `base_clock` — источник «настоящего» времени вместо `RealClock` (поверх него идёт перемотка демо);
        `clock` подменяет часы приложения целиком."""
        ov = dict(overrides or {})
        unknown = set(ov) - OVERRIDE_KEYS
        if unknown:
            raise ValueError(f"Неизвестные подмены приложения: {', '.join(sorted(unknown))}.")
        if settings.telegram_bot_token is None:
            raise AppConfigError("Не задан токен бота. Впишите TELEGRAM_BOT_TOKEN в файл .env "
                                 "(токен выдаёт @BotFather в Telegram).")
        app = cls()
        app._build(settings, ov)
        return app

    # ------------------------------------------------------------------ сборка
    def _build(self, settings: Settings, ov: dict[str, Any]) -> None:
        from target_ai.bot import build_dispatcher

        self.settings = settings
        settings.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = Database(settings.database_path)
        base = ov.get("base_clock") or RealClock()
        self.clock = ov.get("clock") or LevelClock(SimClock(self.db.sim, base), base, self.db)
        fake = ov.get("fake_meta") or FakeGraphAPI(self.db.fake_meta, self.clock)
        self.fake_meta = fake
        self.gateways = LevelGateways(self.db, settings, self.clock, fake, transport=ov.get("meta_transport"),
                                      sleeper=ov.get("meta_sleeper"))
        self.llm = ov.get("llm") or (ClaudeLLM(settings) if settings.anthropic_api_key else OfflineLLM())
        self._build_bot(settings, ov)
        self._build_services(settings, ov, fake)
        self.tick_seconds = float(ov.get("tick_seconds", DEFAULT_TICK_SECONDS))
        self.dispatcher = build_dispatcher(self)
        self._ticker = Ticker(self.scheduler, self.tick_seconds)

    def _build_bot(self, settings: Settings, ov: dict[str, Any]) -> None:
        session: BaseSession | None = ov.get("bot_session")
        self.bot = Bot(settings.telegram_bot_token.get_secret_value(), session=session)
        self.telegram = None
        if "notifier" not in ov:
            self.telegram = TelegramNotifier(self.bot, settings.telegram_allowed_ids)
        self.notifier = ov.get("notifier") or self.telegram

    def _build_services(self, settings: Settings, ov: dict[str, Any], fake: FakeGraphAPI) -> None:
        db, clock, notifier, gateways = self.db, self.clock, self.notifier, self.gateways
        self.executor = Executor(db, gateways, None, clock, notifier)
        probe = ov.get("claude_probe") or (ClaudeKeyProbe(settings) if settings.anthropic_api_key else None)
        self.connection = ConnectionCheck(settings, gateways.real, clock, probe,
                                          ig_access_probe=self._probe_real_ig)
        media_dir = Path(ov.get("media_dir") or settings.database_path.parent / "media")
        self.intake = Intake(db, gateways, self.llm, clock, notifier, self.executor, media_dir=media_dir,
                             check_connection=self._send_connection_report)
        ctx = OpsContext(db, clock, self.executor, gateways, notifier, self.llm)
        self.ops = Ops(ctx)
        self.scheduler = LockedScheduler(Scheduler(db, clock, self.ops.handlers(), notifier))
        self.demo = DemoTime(ctx, self.scheduler, Simulator(fake, clock), self.ops.reporting)
        self.levels = ClockAwareLevelSwitch(db, clock, settings, self.connection, self.executor,
                                            scheduler=self.scheduler)
        self.settings_service = SettingsService(db, clock, settings)
        self.companies = Companies(db, clock, settings, self.executor)
        self.audit_log = AuditLog(db, settings)
        self.system = SystemControl(db, clock, self.executor)

    async def _probe_real_ig(self, account_id: str, page_id: str, ig_user_id: str) -> None:
        """Проба доступа к Instagram на любом уровне идёт в настоящую Meta проверочным клиентом."""
        await self.executor.probe_ig_access(account_id, page_id, ig_user_id,
                                            gateway=self.gateways.real(validate_only=True))

    async def _send_connection_report(self, chat_id: int) -> None:
        report = await self.connection.run()
        await self.notifier.send(chat_id, report.text, [])

    # ------------------------------------------------------------------ жизненный цикл
    async def startup(self) -> None:
        """Webhook снимается ДО polling; прерванные запуски сверяются с Meta; затем планировщик."""
        await self.bot.delete_webhook()
        await self._recover()
        await self._announce_start()
        self._ticker.start()

    async def shutdown(self) -> None:
        await self._ticker.stop()
        await self.gateways.aclose()
        await self.bot.session.close()
        self.db.close()

    async def run(self) -> None:
        try:  # сбой старта (например, Telegram не принял токен) тоже закрывает сессию бота
            await self.startup()
            await self.dispatcher.start_polling(
                self.bot, polling_timeout=POLLING_TIMEOUT_SECONDS, handle_signals=True, close_bot_session=False,
                allowed_updates=self.dispatcher.resolve_used_update_types())
        finally:
            await self.shutdown()

    async def _recover(self) -> None:
        try:
            items = await self.executor.recover_incomplete()
        except Exception:  # старт не должен падать из-за сбоя Meta: пользователь получит сообщение
            log.exception("Не удалось сверить прерванные запуски")
            await self._tell("Не удалось сверить прерванные запуски с Meta при старте. "
                             "Проверьте подключение: «🔌 Проверить подключение».")
            return
        if items:
            log.info("Найдено прерванных запусков: %s", len(items))

    async def _announce_start(self) -> None:
        from target_ai.bot.menu import main_menu

        level = str(self.db.settings.get(SETTING_LEVEL, DEFAULT_LEVEL))
        text = f"Бот запущен. Уровень работы: {level_title(level)}."
        if self.telegram is None:
            await self._tell(text)
            return
        for chat_id in self.settings.telegram_allowed_ids:
            try:
                await self.telegram.send_with_menu(chat_id, text, main_menu(level))
            except TelegramAPIError:
                log.exception("Не удалось отправить приветствие в чат %s", chat_id)

    async def _tell(self, text: str) -> None:
        try:
            await self.notifier.send(None, text, [])
        except Exception:
            log.exception("Не удалось отправить сообщение")


__all__ = ["App", "AppConfigError"]
