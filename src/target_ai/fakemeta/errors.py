"""Ошибки фейкового Graph API: форма настоящего ответа Meta и готовые конструкторы."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

FAKE_TRACE_ID = "AFakeTrace0001"


@dataclass(frozen=True, kw_only=True)
class FakeError:
    status: int = 400
    code: int = 100
    subcode: int | None = None
    message: str = "Fake Graph API error"
    user_message: str | None = None
    is_transient: bool = False
    blame_fields: tuple[str, ...] = ()
    headers: dict[str, str] = field(default_factory=dict)
    drop_connection: bool = False
    after_execute: bool = False  # сначала исполнить запрос, потом отдать ошибку/оборвать (исход неизвестен)

    def body(self) -> dict[str, Any]:
        error: dict[str, Any] = {
            "message": self.message, "type": "OAuthException", "code": self.code, "fbtrace_id": FAKE_TRACE_ID,
        }
        if self.subcode is not None:
            error["error_subcode"] = self.subcode
        if self.user_message:
            error["error_user_title"] = "Ошибка"
            error["error_user_msg"] = self.user_message
        if self.is_transient:
            error["is_transient"] = True
        if self.blame_fields:
            error["error_data"] = {"blame_field_specs": [[name] for name in self.blame_fields]}
        return {"error": error}


def token_expired() -> FakeError:
    return FakeError(status=401, code=190, subcode=463, message="Error validating access token: Session has expired")


def token_revoked() -> FakeError:
    return FakeError(status=401, code=190, subcode=467, message="Error validating access token: The session is invalid")


def permission_denied(code: int = 200, subcode: int | None = None) -> FakeError:
    return FakeError(status=403, code=code, subcode=subcode, message="(#200) Requires ads_management permission")


def rate_limited(code: int = 17, minutes: int = 1) -> FakeError:
    usage = {"1000": [{"type": "ads_management", "call_count": 100, "total_cputime": 5, "total_time": 5,
                       "estimated_time_to_regain_access": minutes}]}
    return FakeError(
        status=400, code=code, message="(#17) User request limit reached",
        headers={"X-Business-Use-Case-Usage": json.dumps(usage)},
    )


def invalid_parameter(*fields: str) -> FakeError:
    return FakeError(code=100, message="Invalid parameter", user_message="Неверный параметр", blame_fields=fields)


def api_version_deprecated() -> FakeError:
    return FakeError(code=2635, message="You are calling a deprecated version of the Ads API")


def ig_access_denied() -> FakeError:
    return FakeError(code=100, subcode=1815199, message="The ad account has no access to the Instagram account")


def budget_too_low() -> FakeError:
    return FakeError(code=100, subcode=1885272, message="Budget is too low", blame_fields=("daily_budget",))


def spend_cap_too_low() -> FakeError:
    return FakeError(code=100, subcode=2446307, message="Spend cap is less than minimum", blame_fields=("spend_cap",))


def ad_disapproved() -> FakeError:
    return FakeError(code=100, subcode=2490427, message="A disapproved ad cannot be enabled")


def audience_terms() -> FakeError:
    return FakeError(code=200, subcode=1870034, message="Terms of service for custom audiences not accepted")


def eu_dsa_required() -> FakeError:
    return FakeError(code=100, subcode=3858152, message="DSA beneficiary and payor are required for EU targeting",
                     blame_fields=("dsa_beneficiary", "dsa_payor"))


def transient() -> FakeError:
    return FakeError(status=500, code=2, message="Service temporarily unavailable", is_transient=True)


def server_error() -> FakeError:
    return FakeError(status=503, code=1, message="An unknown error occurred")


def connection_dropped() -> FakeError:
    return FakeError(drop_connection=True)
