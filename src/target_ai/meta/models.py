"""Типизированные ответы клиента Meta."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from target_ai.money import Money, MoneyError

ACCOUNT_ACTIVE = 1


@dataclass(frozen=True, kw_only=True)
class AdAccount:
    id: str  # "act_<число>"
    name: str
    currency: str
    timezone_name: str
    account_status: int
    disable_reason: int
    funding_source: str | None
    min_campaign_group_spend_cap: Money | None

    @property
    def is_active(self) -> bool:
        return self.account_status == ACCOUNT_ACTIVE

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> AdAccount:
        currency = str(raw.get("currency", ""))
        return cls(
            id=str(raw["id"]),
            name=str(raw.get("name", "")),
            currency=currency,
            timezone_name=str(raw.get("timezone_name", "UTC")),
            account_status=int(raw.get("account_status", 0)),
            disable_reason=int(raw.get("disable_reason", 0) or 0),
            funding_source=str(raw["funding_source"]) if raw.get("funding_source") else None,
            min_campaign_group_spend_cap=_minor(raw.get("min_campaign_group_spend_cap"), currency),
        )


def _minor(value: Any, currency: str) -> Money | None:
    if value in (None, ""):
        return None
    try:
        return Money(int(value), currency)
    except (ValueError, MoneyError):
        return None


def money_from_minor(value: Any, currency: str) -> Money | None:
    return _minor(value, currency)


@dataclass(frozen=True, kw_only=True)
class TokenInfo:
    is_valid: bool
    expires_at: datetime | None  # None — бессрочный или срок неизвестен
    expiry_known: bool
    scopes: tuple[str, ...] = ()
    data_access_expires_at: datetime | None = None

    @classmethod
    def from_debug(cls, data: dict[str, Any]) -> TokenInfo:
        expires = int(data.get("expires_at", 0) or 0)
        access = int(data.get("data_access_expires_at", 0) or 0)
        return cls(
            is_valid=bool(data.get("is_valid")),
            expires_at=datetime.fromtimestamp(expires, UTC) if expires else None,
            expiry_known=True,
            scopes=tuple(data.get("scopes") or ()),
            data_access_expires_at=datetime.fromtimestamp(access, UTC) if access else None,
        )
