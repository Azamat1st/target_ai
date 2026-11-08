"""Ошибки Meta: разбор только по code/error_subcode, русский текст для пользователя."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Виды ошибок (MetaError.kind)
TOKEN_EXPIRED = "token_expired"
TOKEN_INVALID = "token_invalid"
PERMISSION = "permission"
RATE_LIMIT = "rate_limit"
INVALID_PARAMETER = "invalid_parameter"
API_VERSION = "api_version"
IG_ACCESS = "ig_access"
AUDIENCE_TERMS = "audience_terms"
SPEND_CAP_TOO_LOW = "spend_cap_too_low"
BUDGET_TOO_LOW = "budget_too_low"
AD_DISAPPROVED = "ad_disapproved"
EU_DSA = "eu_dsa"
TRANSIENT = "transient"
NETWORK = "network"
CURRENCY_MISMATCH = "currency_mismatch"
VALIDATE_ONLY_UNSUPPORTED = "validate_only_unsupported"
UNKNOWN = "unknown"

RATE_LIMIT_CODES = frozenset({4, 17, 32, 613, *range(80000, 80015)})
_REVOKED_SUBCODES = frozenset({458, 460, 467})
_AUDIENCE_TERMS_SUBCODES = frozenset({1870034, 1870090, 1870092})
_BUDGET_SUBCODES = frozenset({1885272, 1885650, 1487901})
_DISAPPROVED_SUBCODES = frozenset({2490427, 2490468})

_MESSAGES = {
    TOKEN_EXPIRED: "Токен Meta истёк. Выпустите новый токен системного пользователя и обновите META_ACCESS_TOKEN (см. README).",
    TOKEN_INVALID: "Токен Meta отозван или недействителен. Выпустите новый токен и обновите META_ACCESS_TOKEN (см. README).",
    PERMISSION: "Токену Meta не хватает разрешений. Выдайте системному пользователю доступ к рекламному аккаунту и Странице "
    "и нужные разрешения (ads_management, ads_read, pages_show_list, instagram_basic).",
    IG_ACCESS: "У рекламного аккаунта нет доступа к Instagram-аккаунту. Привяжите его в Business Manager: "
    "Настройки бизнеса - Аккаунты - Instagram-аккаунты - Добавить активы.",
    AUDIENCE_TERMS: "Не приняты условия пользовательских аудиторий. Примите их в Ads Manager и повторите.",
    SPEND_CAP_TOO_LOW: "Лимит расходов кампании меньше минимума, который допускает Meta.",
    BUDGET_TOO_LOW: "Дневной бюджет меньше минимума, который допускает Meta.",
    AD_DISAPPROVED: "Отклонённое объявление включить нельзя. Создаю новое объявление взамен.",
    EU_DSA: "Для показа в странах ЕС нужны получатель и плательщик рекламы.",
    API_VERSION: "Версия API Meta устарела. Обновите META_API_VERSION.",
    TRANSIENT: "Временная ошибка Meta.",
    RATE_LIMIT: "Достигнут лимит запросов Meta. Повторю чуть позже.",
    NETWORK: "Не удалось связаться с Meta (обрыв соединения).",
}


class MetaError(Exception):
    """Типизированная ошибка Graph API. Текст для пользователя — только русский."""

    def __init__(
        self,
        kind: str,
        user_message_ru: str,
        *,
        code: int | None = None,
        subcode: int | None = None,
        retry_after: float | None = None,
        fbtrace_id: str | None = None,
        blame_fields: tuple[str, ...] = (),
        http_status: int | None = None,
        is_transient: bool = False,
        outcome_unknown: bool = False,
    ) -> None:
        super().__init__(f"{kind}: {user_message_ru} (code={code}, subcode={subcode})")
        self.kind = kind
        self.user_message_ru = user_message_ru
        self.code = code
        self.subcode = subcode
        self.retry_after = retry_after
        self.fbtrace_id = fbtrace_id
        self.blame_fields = blame_fields
        self.http_status = http_status
        self.is_transient = is_transient
        # True: создающий запрос мог исполниться на стороне Meta; вызывающий обязан сверить результат по тегу
        self.outcome_unknown = outcome_unknown


def _blame_fields(error: Mapping[str, Any]) -> tuple[str, ...]:
    data = error.get("error_data")
    specs = data.get("blame_field_specs") if isinstance(data, Mapping) else None
    if not isinstance(specs, list):
        return ()
    names: list[str] = []
    for spec in specs:
        names.extend(str(part) for part in (spec if isinstance(spec, list) else [spec]))
    return tuple(names)


def _kind_for(code: int | None, subcode: int | None, status: int, is_transient: bool) -> str:
    if code == 190:
        return TOKEN_EXPIRED if subcode == 463 else TOKEN_INVALID
    if code == 200 and subcode in _AUDIENCE_TERMS_SUBCODES:
        return AUDIENCE_TERMS
    if subcode == 1815199:
        return IG_ACCESS
    if code in RATE_LIMIT_CODES:
        return RATE_LIMIT
    if code == 10 or code == 294 or (code is not None and 200 <= code <= 299):
        return PERMISSION
    if subcode == 2446307:
        return SPEND_CAP_TOO_LOW
    if subcode in _BUDGET_SUBCODES:
        return BUDGET_TOO_LOW
    if subcode in _DISAPPROVED_SUBCODES:
        return AD_DISAPPROVED
    if subcode == 3858152:
        return EU_DSA
    if code == 2635:
        return API_VERSION
    if code == 100:
        return INVALID_PARAMETER
    if is_transient or status >= 500 or code in (1, 2):
        return TRANSIENT
    return UNKNOWN


def _message_for(kind: str, error: Mapping[str, Any], blame: tuple[str, ...], code: int | None, subcode: int | None) -> str:
    if kind == INVALID_PARAMETER:
        fields = ", ".join(blame) if blame else "не определён"
        return f"Meta отклонила параметр запроса: {fields}. Подробности в журнале."
    if kind == UNKNOWN:
        detail = error.get("error_user_msg") or error.get("message") or "без описания"
        return f"Meta вернула ошибку: {detail} (код {code}/{subcode})"
    return _MESSAGES[kind]


def parse_error(payload: Any, status: int, retry_after: float | None = None) -> MetaError:
    """Превращает тело ответа Graph API в MetaError. Не бросает исключений на мусорном теле."""
    error = payload.get("error") if isinstance(payload, Mapping) else None
    if not isinstance(error, Mapping):
        kind = TRANSIENT if status >= 500 else UNKNOWN
        text = _MESSAGES[TRANSIENT] if kind == TRANSIENT else f"Meta вернула неожиданный ответ (HTTP {status})."
        return MetaError(kind, text, http_status=status, retry_after=retry_after, is_transient=kind == TRANSIENT)
    code = error.get("code") if isinstance(error.get("code"), int) else None
    subcode = error.get("error_subcode") if isinstance(error.get("error_subcode"), int) else None
    is_transient = bool(error.get("is_transient"))
    kind = _kind_for(code, subcode, status, is_transient)
    blame = _blame_fields(error)
    return MetaError(
        kind,
        _message_for(kind, error, blame, code, subcode),
        code=code,
        subcode=subcode,
        retry_after=retry_after,
        fbtrace_id=error.get("fbtrace_id"),
        blame_fields=blame,
        http_status=status,
        is_transient=is_transient or kind == TRANSIENT,
    )


def rate_limit_message(wait_seconds: float) -> str:
    minutes = max(1, round(wait_seconds / 60))
    return f"Достигнут лимит запросов Meta. Повторю через {minutes} мин."
