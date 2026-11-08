"""Общий контекст сервисов «после запуска»: зависимости, вывод сообщений, время аккаунта."""

from __future__ import annotations

import asyncio
import re
import logging
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from target_ai.clock import Clock, to_account_time
from target_ai.money import Money, format_ru
from target_ai.storage import Company, Database
from target_ai.config.runtime import DEFAULT_TZ, SETTING_THRESHOLDS, Thresholds

log = logging.getLogger(__name__)


class OpsContext:
    """Зависимости сервисов. `quiet()` собирает сообщения в outbox вместо отправки (перемотка демо)."""

    def __init__(self, db: Database, clock: Clock, executor, gateway_factory: Callable[[bool], Any],
                 notifier, llm) -> None:
        self.db, self.clock, self.executor = db, clock, executor
        self.gateway_factory, self.notifier, self.llm = gateway_factory, notifier, llm
        self.outbox: list[tuple[str, list]] | None = None

    def gateway(self):
        return self.gateway_factory(False)

    @staticmethod
    async def io(fn, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def emit(self, text: str, buttons: list | None = None) -> None:
        text = strip_tags(text)
        if self.outbox is not None:
            self.outbox.append((text, buttons or []))
            return
        try:
            await self.notifier.send(None, text, buttons or [])
        except Exception:  # сообщение не должно ронять задачу
            log.exception("Не удалось отправить сообщение")

    @contextmanager
    def quiet(self):
        previous, self.outbox = self.outbox, []
        try:
            yield self.outbox
        finally:
            self.outbox = previous

    def thresholds(self) -> Thresholds:
        saved = self.db.settings.get(SETTING_THRESHOLDS) or {}
        defaults = Thresholds()
        values = {k: (Decimal(str(v)) if isinstance(getattr(defaults, k), Decimal) else int(v))
                  for k, v in saved.items() if hasattr(defaults, k)}
        return Thresholds(**{**defaults.__dict__, **values})

    def account_now(self, company: Company) -> datetime:
        return to_account_time(self.clock.now(), company.timezone or DEFAULT_TZ)

    def account_today(self, company: Company) -> date:
        return self.account_now(company).date()

    async def active_companies(self) -> list[Company]:
        rows = await self.io(self.db.companies.list)
        return [c for c in rows if c.status == "active" and c.ad_account_id]

    async def company(self, company_id: int) -> Company:
        found = await self.io(self.db.companies.get, company_id)
        if found is None:
            raise LookupError(f"Компания {company_id} не найдена.")
        return found


TAG_RE = re.compile(r"\s*\[ta:[^\]]*\]")


def strip_tags(text: str) -> str:
    """Убирает служебный тег `[ta:…]` из имён объектов в текстах пользователю (единственное место)."""
    return TAG_RE.sub("", text)


def fmt(value: Money | None) -> str:
    return "—" if value is None else format_ru(value)


def comma(value: Decimal | float) -> str:
    return f"{value:.1f}".replace(".", ",")
