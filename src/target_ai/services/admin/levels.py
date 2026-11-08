"""Уровни работы: демо → тест → боевой (истории 82–85, 87, 88)."""

from __future__ import annotations

import logging

from target_ai.clock import Clock
from target_ai.config import Settings
from target_ai.storage import Company, Database

from .common import (
    ACTOR_USER, DENIED, DONE, ERROR, LEVELS, NEEDS_APPROVAL, UNCHANGED, Journal, Reply, intent_of, confirm_buttons,
    SETTING_LEVEL, io, level_title,
)
from .connection import ConnectionCheck
from target_ai.config.runtime import DEFAULT_LEVEL, SETTING_LEVEL

log = logging.getLogger(__name__)

CALLBACK = "lvl"  # lvl:show · lvl:set:<уровень> · lvl:ok:<подтверждение> · lvl:no:<подтверждение>
APPROVAL_KIND = "level_switch"
LEVEL_NOTES = {
    "demo": "Демо: без Meta, на симуляторе. Деньги не тратятся.",
    "test": "Тест: Meta читается по-настоящему, а создание проверяется пробными запросами — ничего не создаётся.",
    "live": "Боевой: реальные запуски в Meta, реальные деньги — в пределах месячного лимита и только с вашими кнопками.",
}
LIVE_ENTER_TEXT = (
    "Включить боевой уровень?\n\nЧто изменится:\n"
    "• бот начнёт создавать настоящие кампании в вашем рекламном аккаунте Meta и тратить настоящие деньги;\n"
    "• расходы ограничены месячным лимитом каждой компании, а увеличение расхода в режиме «С подтверждением» "
    "по-прежнему идёт только после вашей кнопки;\n"
    "• «🛑 Стоп-кран» и пауза компании остаются под рукой."
)
LIVE_EXIT_TEXT = (
    "Выйти из боевого уровня в «{target}»?\n\nЧто изменится:\n"
    "• все боевые кампании будут поставлены на паузу — иначе они продолжили бы тратить деньги без управления;\n"
    "• новые запуски в Meta станут недоступны, пока вы снова не включите боевой уровень.\n"
    "Возобновить каждую кампанию потом можно будет в карточке компании."
)


class LevelSwitch:
    def __init__(self, db: Database, clock: Clock, settings: Settings, checker: ConnectionCheck, executor) -> None:
        self._db, self._clock, self._settings = db, clock, settings
        self._checker, self._executor = checker, executor
        self._journal = Journal(db, clock)

    # ------------------------------------------------------------------ чтение
    async def current(self) -> str:
        return str(await io(self._db.settings.get, SETTING_LEVEL, DEFAULT_LEVEL))

    async def view(self) -> Reply:
        level = await self.current()
        lines = [f"Уровень работы: {level_title(level)}", "", *(LEVEL_NOTES[name] for name in LEVELS)]
        buttons = [[(f"Перейти: {level_title(name)}", f"{CALLBACK}:set:{name}")] for name in LEVELS if name != level]
        return Reply(DONE, "\n".join(lines), buttons)

    # ------------------------------------------------------------------ переключение
    async def request(self, target: str, *, actor: str = ACTOR_USER) -> Reply:
        if target not in LEVELS:
            return Reply(ERROR, "Такого уровня нет: выберите демо, тест или боевой.")
        current = await self.current()
        if target == current:
            return Reply(UNCHANGED, f"Уровень уже «{level_title(current)}».")
        denial = await self._precheck(target)
        if denial is not None:
            return denial
        if target == "live" or current == "live":
            return await self._ask(target, current, actor)
        return await self._apply(target, current, actor)

    async def _precheck(self, target: str) -> Reply | None:
        if target == "demo":
            return None
        if self._settings.meta_access_token is None:
            return Reply(DENIED, f"Перейти в «{level_title(target)}» нельзя: не задан токен Meta.\n"
                                 "Что сделать: добавьте META_ACCESS_TOKEN в файл .env (см. README) и перезапустите бота.")
        if target == "live":
            report = await self._checker.run()
            if not report.can_live:
                problems = "\n".join(line.text for line in report.blockers)
                return Reply(DENIED, "В боевой уровень пока нельзя — есть блокирующие проблемы:\n\n" + problems)
        return None

    async def _ask(self, target: str, current: str, actor: str) -> Reply:
        text = LIVE_ENTER_TEXT if target == "live" else LIVE_EXIT_TEXT.format(target=level_title(target))
        approval = await io(self._db.approvals.create, kind=APPROVAL_KIND, now=self._clock.now(),
                            payload={"intent": {"to": target, "actor": actor}})
        yes = "✅ Включить боевой" if target == "live" else "✅ Выйти и поставить на паузу"
        return Reply(NEEDS_APPROVAL, text, confirm_buttons(CALLBACK, approval.id, yes), approval.id)

    async def confirm(self, approval_id: str) -> Reply:
        intent = await io(intent_of, self._db, approval_id)
        if intent is None:
            return Reply(ERROR, "Это подтверждение уже использовано или отменено. Запросите переключение заново.")
        target, actor = intent["to"], intent.get("actor", ACTOR_USER)
        if target == "live":  # состояние могло измениться, пока кнопка ждала
            denial = await self._precheck("live")
            if denial is not None:
                await io(self._db.approvals.cancel, approval_id)
                return denial
        if await io(self._db.approvals.consume, approval_id, self._clock.now()) is None:
            return Reply(ERROR, "Подтверждение просрочено. Запросите переключение заново.")
        return await self._apply(target, await self.current(), actor)

    async def cancel(self, approval_id: str) -> Reply:
        await io(self._db.approvals.cancel, approval_id)
        return Reply(DONE, "Хорошо, уровень не меняю.")

    # ------------------------------------------------------------------ применение
    async def _apply(self, target: str, current: str, actor: str) -> Reply:
        stopped, failed = await self._pause_for_exit(current, target, actor)
        if failed and current == "live":  # боевые кампании остались бы без управления
            await self._journal.write(actor, "level_change", None, "error", to=target, not_paused=list(failed))
            return Reply(ERROR, "Уровень не изменён: не удалось поставить на паузу кампании "
                                f"({'; '.join(failed)}).\nЧто сделать: повторите попытку или нажмите «🛑 Стоп-кран».")
        await io(self._db.settings.set, SETTING_LEVEL, target)
        await self._journal.write(actor, "level_change", None, "ok", summary=f"{level_title(current)} → "
                                  f"{level_title(target)}", to=target, paused=list(stopped))
        extra = f"\nКомпании поставлены на паузу: {', '.join(stopped)}." if stopped else ""
        if failed:
            extra += f"\nНе удалось остановить в симуляторе: {'; '.join(failed)}."
        if current == "demo":
            extra += "\nДемо-компании остаются в списке с пометкой «демо»."
        return Reply(DONE, f"Уровень работы: {level_title(target)}.{extra}")

    async def _pause_for_exit(self, current: str, target: str, actor: str) -> tuple[list[str], list[str]]:
        """Выход из боевого — пауза боевых компаний; выход из демо — пауза демо-компаний (в симуляторе)."""
        if current == "live":
            wanted = False
        elif current == "demo":
            wanted = True
        else:
            return [], []
        companies = [c for c in await io(self._db.companies.list) if bool(c.profile.get("demo")) is wanted]
        stopped: list[str] = []
        failed: list[str] = []
        for company in companies:
            await self._pause(company, actor, stopped, failed)
        return stopped, failed

    async def _pause(self, company: Company, actor: str, stopped: list[str], failed: list[str]) -> None:
        campaigns = await io(self._db.objects.list, company_id=company.id, kind="campaign")
        if not campaigns:
            return
        try:
            report = await self._executor.pause_company(company.id, actor=actor)
        except Exception as exc:  # одна компания не должна мешать остальным
            log.exception("Пауза компании %s при смене уровня", company.id)
            failed.append(f"{company.name}: {getattr(exc, 'message_ru', exc)}")
            return
        (failed if report.failed else stopped).append(
            f"{company.name}: {'; '.join(reason for _, reason in report.failed)}" if report.failed else company.name)
