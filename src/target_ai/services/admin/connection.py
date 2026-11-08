"""«Проверить подключение» (спека §9): простыми словами, что есть, чего не хватает и что сделать."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

import anthropic

from target_ai.clock import Clock
from target_ai.config import Settings
from target_ai.llm.errors import LLMRequestError
from target_ai.meta.errors import MetaError

log = logging.getLogger(__name__)

OK, WARN, FAIL = "ok", "warn", "fail"
ICONS = {OK: "✅", WARN: "⚠️", FAIL: "❌"}
REQUIRED_SCOPES = ("ads_management", "ads_read", "pages_show_list", "pages_read_engagement",
                   "pages_manage_ads", "pages_read_user_content", "instagram_basic", "instagram_manage_insights")
OPTIONAL_SCOPES = ("business_management",)
TOKEN_WARN_DAYS = 7
REJECTED_STATUSES = (401, 403)
README_TOKEN = "см. README, раздел «Токен Meta»"
ACCOUNT_STATUS_RU = {
    2: ("отключён (аккаунт ограничен)", "Откройте Ads Manager и узнайте причину ограничения; пока оно не снято, реклама невозможна."),
    3: ("проблема с оплатой (есть неоплаченный долг)", "Погасите задолженность в Ads Manager → «Оплата»."),
    8: ("проблема с оплатой", "Проверьте способ оплаты в Ads Manager → «Оплата»."),
    9: ("проблема с оплатой (льготный период)", "Оплатите задолженность в Ads Manager, пока аккаунт не отключили."),
    7: ("на проверке рисков", "Дождитесь окончания проверки Meta; при необходимости подтвердите личность в Ads Manager."),
    100: ("закрывается", "Аккаунт скоро закроется: используйте другой рекламный аккаунт."),
    101: ("закрыт", "Используйте другой рекламный аккаунт или откройте новый."),
}

ClaudeProbe = Callable[[], Awaitable[None]]
# (id рекламного аккаунта, id Страницы, id IG-аккаунта); при отказе Meta (1815199) бросает MetaError(kind="ig_access").
# Реализует Исполнитель: пробное создание креатива с instagram_user_id в режиме validate_only.
IgAccessProbe = Callable[[str, str, str], Awaitable[None]]


@dataclass(frozen=True)
class CheckLine:
    status: str
    title: str
    advice: str = ""
    blocks_test: bool = False  # проблема мешает и тестовому уровню (иначе только боевому)

    @property
    def text(self) -> str:
        head = f"{ICONS[self.status]} {self.title}"
        return f"{head}\n    Что сделать: {self.advice}" if self.advice and self.status != OK else head


@dataclass(frozen=True)
class ConnectionReport:
    lines: tuple[CheckLine, ...]

    @property
    def blockers(self) -> tuple[CheckLine, ...]:
        return tuple(line for line in self.lines if line.status == FAIL)

    @property
    def can_test(self) -> bool:
        return not any(line.status == FAIL and line.blocks_test for line in self.lines)

    @property
    def can_live(self) -> bool:
        return not self.blockers

    @property
    def verdict(self) -> str:
        if self.can_live:
            return "Итог: можно переходить и в тестовый, и в боевой уровень."
        if self.can_test:
            return "Итог: боевой уровень закрыт, пока не исправлены пункты с ❌; тестовому они не мешают."
        return "Итог: пункты с ❌ мешают и тестовому, и боевому уровню — исправьте их."

    @property
    def text(self) -> str:
        return "Проверка подключения\n\n" + "\n".join(line.text for line in self.lines) + "\n\n" + self.verdict


class ClaudeKeyProbe:
    """Дешёвая проверка ключа Claude: список моделей. Отказ ключа — `LLMRequestError(401/403)`."""

    def __init__(self, settings: Settings, client: anthropic.AsyncAnthropic | None = None) -> None:
        if client is None and settings.anthropic_api_key is not None:
            client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value(), max_retries=0,
                                              timeout=20.0)
        self._client = client

    async def __call__(self) -> None:
        if self._client is None:
            raise LLMRequestError(401)
        try:
            await self._client.models.list(limit=1)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise LLMRequestError(getattr(exc, "status_code", 401)) from exc


class ConnectionCheck:
    def __init__(self, settings: Settings, gateway_factory, clock: Clock, claude_probe: ClaudeProbe | None = None,
                 ig_access_probe: IgAccessProbe | None = None):
        self._settings, self._factory, self._clock = settings, gateway_factory, clock
        self._probe, self._ig_probe = claude_probe, ig_access_probe

    async def run(self) -> ConnectionReport:
        lines = [self._bot_line(), await self._claude_line()]
        lines += await self._meta_lines()
        return ConnectionReport(tuple(lines))

    # ------------------------------------------------------------------ бот и Claude
    def _bot_line(self) -> CheckLine:
        if self._settings.telegram_bot_token is None:
            return CheckLine(FAIL, "Токен Telegram-бота не задан.", "Добавьте TELEGRAM_BOT_TOKEN в файл .env.", True)
        return CheckLine(OK, "Telegram-бот: токен задан.")

    async def _claude_line(self) -> CheckLine:
        if self._settings.anthropic_api_key is None:
            return CheckLine(FAIL, "Ключ Claude не задан: демо работает на шаблонных ответах, но для боевого уровня "
                                   "ключ обязателен.", "Добавьте ANTHROPIC_API_KEY в файл .env.")
        if self._probe is None:
            return CheckLine(OK, "Ключ Claude задан.")
        try:
            await self._probe()
        except LLMRequestError as exc:
            if exc.status in REJECTED_STATUSES:
                return CheckLine(FAIL, "Ключ Claude не принят.", "Проверьте ANTHROPIC_API_KEY в файле .env: "
                                 "возможно, ключ отозван или скопирован не полностью.")
        except Exception:  # сеть и сбои чужого сервиса не должны ронять проверку
            log.exception("Не удалось проверить ключ Claude")
        else:
            return CheckLine(OK, "Ключ Claude принят.")
        return CheckLine(WARN, "Ключ Claude задан, но проверить его сейчас не удалось.", "Повторите проверку позже.")

    # ------------------------------------------------------------------ Meta
    async def _meta_lines(self) -> list[CheckLine]:
        if self._settings.meta_access_token is None:
            return [CheckLine(FAIL, "Токен Meta (META_ACCESS_TOKEN) не задан.",
                              f"Выпустите токен системного пользователя и добавьте его в .env ({README_TOKEN}).", True)]
        gateway = self._factory(False)
        token_lines, usable = await self._token_lines(gateway)
        if not usable:
            return [*token_lines, CheckLine(WARN, "Остальные проверки Meta пропущены: без рабочего токена они "
                                                  "невозможны.")]
        lines = [*token_lines]
        accounts = await self._fetch(gateway.list_ad_accounts(), "Рекламные аккаунты", lines)
        if accounts is not None:
            lines += self._account_lines(accounts)
        pages = await self._fetch(gateway.list_pages(), "Страницы и Instagram", lines)
        if pages is not None:
            lines += self._page_lines(pages)
        if accounts and pages:
            lines += await self._ig_access_lines(accounts, pages)
        return lines

    async def _fetch(self, work, title: str, lines: list[CheckLine]):
        try:
            return await work
        except MetaError as exc:
            lines.append(self._meta_failure(title, exc))
            return None

    @staticmethod
    def _meta_failure(title: str, exc: MetaError, *, blocks_test: bool = False) -> CheckLine:
        token_trouble = exc.kind in ("token_expired", "token_invalid")
        head = "Токен Meta не принят." if token_trouble else f"{title}: Meta вернула ошибку."
        return CheckLine(FAIL, head, exc.user_message_ru, token_trouble or blocks_test)

    async def _token_lines(self, gateway) -> tuple[list[CheckLine], bool]:
        try:
            info = await gateway.debug_token()
        except MetaError as exc:
            return [self._meta_failure("Токен Meta", exc, blocks_test=True)], False  # без токена нет и теста
        if not info.is_valid:
            return [CheckLine(FAIL, "Токен Meta недействителен.",
                              f"Выпустите новый токен и обновите META_ACCESS_TOKEN ({README_TOKEN}).", True)], False
        lines = [self._expiry_line(info)]
        if info.expiry_known:
            lines += self._scope_lines(info.scopes)
        else:
            lines.append(CheckLine(WARN, "Разрешения токена проверить не удалось.",
                                   "Добавьте META_APP_ID и META_APP_SECRET в .env: без них Meta не говорит, "
                                   "какие разрешения выданы."))
        return lines, True

    def _expiry_line(self, info) -> CheckLine:
        if not info.expiry_known:
            return CheckLine(WARN, "Токен Meta действителен, но срок его действия неизвестен.",
                             "Добавьте META_APP_ID и META_APP_SECRET в .env, чтобы бот предупреждал об истечении.")
        if info.expires_at is None:
            return CheckLine(OK, "Токен Meta действителен, бессрочный.")
        left = info.expires_at - self._clock.now()
        if left <= timedelta(0):
            return CheckLine(FAIL, "Токен Meta истёк.", f"Выпустите новый токен и обновите META_ACCESS_TOKEN "
                             f"({README_TOKEN}).", True)
        days = left.days
        if days < TOKEN_WARN_DAYS:
            return CheckLine(WARN, f"Токен Meta действителен, но истекает через {days} дн.",
                             f"Заранее выпустите новый токен ({README_TOKEN}).")
        return CheckLine(OK, f"Токен Meta действителен, истекает через {days} дн.")

    @staticmethod
    def _scope_lines(scopes: tuple[str, ...]) -> list[CheckLine]:
        have = set(scopes)
        missing = [s for s in REQUIRED_SCOPES if s not in have]
        lines = []
        if missing:
            lines.append(CheckLine(FAIL, "Токену не хватает разрешений: " + ", ".join(missing) + ".",
                                   "Выдайте их системному пользователю при создании токена в Business Manager "
                                   f"({README_TOKEN})."))
        else:
            lines.append(CheckLine(OK, "Все нужные разрешения токена выданы."))
        absent = [s for s in OPTIONAL_SCOPES if s not in have]
        if absent:
            lines.append(CheckLine(WARN, "Желательное разрешение не выдано: " + ", ".join(absent) + ".",
                                   "Без него часть данных о бизнесе может быть недоступна; на работу это обычно "
                                   "не влияет."))
        return lines

    @staticmethod
    def _account_lines(accounts) -> list[CheckLine]:
        if not accounts:
            return [CheckLine(FAIL, "Токену не доступен ни один рекламный аккаунт.",
                              "Назначьте системному пользователю рекламный аккаунт в Business Manager "
                              "(Настройки бизнеса → Системные пользователи → Добавить активы).")]
        return [ConnectionCheck._account_line(account) for account in accounts]

    @staticmethod
    def _account_line(account) -> CheckLine:
        name = f"Рекламный аккаунт «{account.name}»"
        if not account.is_active:
            reason, advice = ACCOUNT_STATUS_RU.get(account.account_status, ("недоступен для рекламы",
                                                   "Откройте Ads Manager и посмотрите причину."))
            return CheckLine(FAIL, f"{name}: {reason}.", advice)
        if not account.funding_source:
            return CheckLine(FAIL, f"{name}: нет способа оплаты, объявления не будут показываться.",
                             "Добавьте карту или другой способ оплаты в Ads Manager → «Оплата».")
        return CheckLine(OK, f"{name}: активен, способ оплаты есть ({account.currency}).")

    @staticmethod
    def _page_lines(pages) -> list[CheckLine]:
        if not pages:
            return [CheckLine(FAIL, "Токену не доступна ни одна Страница Facebook.",
                              "Назначьте системному пользователю Страницу в Business Manager с задачей "
                              "«Рекламировать» (ADVERTISE).")]
        lines = []
        for page in pages:
            name = f"Страница «{page.get('name', page.get('id'))}»"
            if (page.get("instagram_business_account") or {}).get("id"):
                lines.append(CheckLine(OK, f"{name}: Instagram-аккаунт привязан."))
            else:
                lines.append(CheckLine(FAIL, f"{name}: Instagram-аккаунт не привязан.",
                                       "В Instagram: Настройки → Аккаунт → Переключиться на профессиональный; затем "
                                       "привяжите его к этой Странице (Настройки Страницы → Связанные аккаунты)."))
        return lines

    async def _ig_access_lines(self, accounts, pages) -> list[CheckLine]:
        """1815199 приходит при создании креатива от имени рекламного аккаунта, а не при чтении профиля."""
        if self._ig_probe is None:
            return [CheckLine(WARN, "Доступ рекламных аккаунтов к Instagram не проверен.",
                              "Эта проверка недоступна в текущей сборке; ошибка появится при первом запуске.")]
        lines: list[CheckLine] = []
        for account in accounts:
            for page in pages:
                ig_id = (page.get("instagram_business_account") or {}).get("id")
                if ig_id:
                    lines.append(await self._ig_access_line(account, page, ig_id))
        return lines

    async def _ig_access_line(self, account, page, ig_id: str) -> CheckLine:
        name = f"Рекламный аккаунт «{account.name}» и Instagram Страницы «{page.get('name', page.get('id'))}»"
        try:
            await self._ig_probe(account.id, str(page["id"]), ig_id)
        except MetaError as exc:
            if exc.kind == "ig_access":
                return CheckLine(FAIL, f"{name}: у рекламного аккаунта нет доступа к Instagram-аккаунту.",
                                 "В Business Manager: Настройки бизнеса → Аккаунты → Instagram-аккаунты → "
                                 "Добавить активы → дайте доступ рекламному аккаунту.")
            return self._meta_failure(name, exc)
        return CheckLine(OK, f"{name}: доступ есть.")
