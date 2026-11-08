"""MetaClient: клиент Graph API (v27) поверх GraphHttp. Реализует протокол MetaGateway."""

from __future__ import annotations

import base64
import logging
from importlib.metadata import PackageNotFoundError, version
from collections.abc import Mapping
from datetime import date
from typing import Any

import httpx

from target_ai.clock import Clock, RealClock
from target_ai.meta import errors
from target_ai.meta.http import GraphHttp
from target_ai.meta.limiter import Sleeper
from target_ai.meta.models import AdAccount, TokenInfo, money_from_minor
from target_ai.money import Money

logger = logging.getLogger(__name__)

DEFAULT_VERSION = "v27.0"


def _app_version() -> str:
    try:
        return version("target-ai")
    except PackageNotFoundError:
        return "0"


ACCOUNT_FIELDS = "id,name,currency,timezone_name,account_status,disable_reason,funding_source,min_campaign_group_spend_cap"
INSIGHT_FIELDS = (
    "spend", "impressions", "reach", "frequency", "clicks", "inline_link_clicks", "ctr",
    "actions", "cost_per_action_type", "results", "cost_per_result",
)
IG_MEDIA_FIELDS = (
    "id,caption,like_count,comments_count,media_type,media_product_type,permalink,timestamp,"
    "media_url,thumbnail_url,boost_eligibility_info"
)
IG_MEDIA_METRICS = ("reach", "saved", "shares", "total_interactions", "views")
BUSINESS_DISCOVERY_FIELDS = (
    "followers_count,media_count,biography,website,"
    "media{caption,like_count,comments_count,media_type,permalink,timestamp}"
)
PAGE_SIZE = 100
MONEY_KEYS = ("daily_budget", "lifetime_budget", "spend_cap", "bid_amount")
VALIDATE_ONLY = "validate_only"
SYNC_REVIEW = "synchronous_ad_review"
BUDGET_EDGES = {"imp": "min_daily_budget_imp", "high_freq": "min_daily_budget_high_freq", "low_freq": "min_daily_budget_low_freq"}


class MetaClient:
    def __init__(
        self,
        token: str,
        version: str = DEFAULT_VERSION,
        transport: httpx.AsyncBaseTransport | None = None,
        validate_only: bool = False,
        app_secret: str | None = None,
        clock: Clock | None = None,
        *,
        app_id: str | None = None,
        sleeper: Sleeper | None = None,
        model_name: str = "",
    ) -> None:
        self._validate_only = validate_only
        self._app_id = app_id
        self._app_secret = app_secret
        self._token = token
        agent = f"TargetAI/{_app_version()} ({model_name or 'n/a'}) httpx"
        self._http = GraphHttp(
            token=token, version=version, transport=transport, app_secret=app_secret,
            clock=clock or RealClock(), sleeper=sleeper, user_agent=agent,
        )
        self._currencies: dict[str, str] = {}
        self._object_accounts: dict[str, str] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    @property
    def validate_only(self) -> bool:
        return self._validate_only

    # ------------------------------------------------------------------ чтение

    async def _pages(self, path: str, params: Mapping[str, Any], limit: int | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        query: dict[str, Any] = {"limit": min(limit or PAGE_SIZE, PAGE_SIZE), **params}
        while True:
            payload = await self._http.request("GET", path, params=query)
            items.extend(payload.get("data") or [])
            after = (payload.get("paging") or {}).get("cursors", {}).get("after")
            has_next = bool((payload.get("paging") or {}).get("next")) and after
            if not has_next or (limit is not None and len(items) >= limit):
                return items[:limit] if limit is not None else items
            query = {**query, "after": after}

    async def list_ad_accounts(self) -> list[AdAccount]:
        rows = await self._pages("me/adaccounts", {"fields": ACCOUNT_FIELDS})
        accounts = [AdAccount.from_json(row) for row in rows]
        self._currencies.update({a.id: a.currency for a in accounts})
        return accounts

    async def get_ad_account(self, account_id: str) -> AdAccount:
        account = AdAccount.from_json(await self._http.request("GET", account_id, params={"fields": ACCOUNT_FIELDS}))
        self._currencies[account.id] = account.currency
        return account

    async def get_minimum_budgets(self, account_id: str) -> dict[str, Money]:
        currency = await self._currency(account_id)
        payload = await self._http.request("GET", f"{account_id}/minimum_budgets")
        row = (payload.get("data") or [{}])[0]
        if row.get("currency") != currency:
            raise errors.MetaError(
                errors.CURRENCY_MISMATCH,
                f"Meta вернула минимальные бюджеты в валюте {row.get('currency')}, а валюта аккаунта — {currency}.",
            )
        result = {name: money_from_minor(row.get(field), currency) for name, field in BUDGET_EDGES.items()}
        return {name: money for name, money in result.items() if money is not None}

    async def list_pages(self) -> list[dict[str, Any]]:
        return await self._pages("me/accounts", {"fields": "id,name,instagram_business_account"})

    async def get_page_instagram(self, page_id: str) -> str | None:
        payload = await self._http.request("GET", page_id, params={"fields": "instagram_business_account"})
        return (payload.get("instagram_business_account") or {}).get("id")

    async def search_interests(self, query: str, limit: int = 25) -> list[dict[str, Any]]:
        payload = await self._http.request(
            "GET", "search", params={"type": "adinterest", "q": query, "limit": limit}
        )
        return list(payload.get("data") or [])

    async def estimate_audience(
        self, account_id: str, targeting_spec: Mapping[str, Any], optimization_goal: str,
        promoted_object: Mapping[str, Any] | None = None,
    ) -> tuple[int, int] | None:
        params = {"targeting_spec": targeting_spec, "optimization_goal": optimization_goal, "promoted_object": promoted_object}
        try:
            payload = await self._http.request("GET", f"{account_id}/delivery_estimate", params=params)
            row = (payload.get("data") or [{}])[0]
            bounds = (row.get("estimate_mau_lower_bound", -1), row.get("estimate_mau_upper_bound", -1))
        except errors.MetaError as exc:
            if exc.kind not in (errors.INVALID_PARAMETER, errors.UNKNOWN):
                raise
            logger.info("delivery_estimate недоступен (%s), пробую reachestimate", exc.kind)
            payload = await self._http.request(
                "GET", f"{account_id}/reachestimate", params={"targeting_spec": targeting_spec}
            )
            row = payload.get("data") or {}
            bounds = (row.get("users_lower_bound", -1), row.get("users_upper_bound", -1))
        return None if min(bounds) < 0 else (int(bounds[0]), int(bounds[1]))

    async def get_insights(
        self, object_id: str, *, level: str, since: date, until: date, fields: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        params = {
            "level": level, "time_increment": 1, "use_unified_attribution_setting": "true",
            "time_range": {"since": since.isoformat(), "until": until.isoformat()},
            "fields": ",".join(fields or INSIGHT_FIELDS),
        }
        return await self._pages(f"{object_id}/insights", params)

    async def get_ad_status(self, ad_id: str) -> dict[str, Any]:
        fields = "effective_status,status,ad_review_feedback,issues_info,name"
        return await self._http.request("GET", ad_id, params={"fields": fields})

    async def find_by_name_tag(self, parent_id: str, edge: str, tag: str) -> list[dict[str, Any]]:
        rows = await self._pages(f"{parent_id}/{edge}", {"fields": "id,name,status,effective_status"})
        return [row for row in rows if tag in str(row.get("name", ""))]

    async def get_ig_profile(self, ig_user_id: str) -> dict[str, Any]:
        fields = "username,name,biography,website,followers_count,media_count"
        return await self._http.request("GET", ig_user_id, params={"fields": fields})

    async def get_ig_media(self, ig_user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return await self._pages(f"{ig_user_id}/media", {"fields": IG_MEDIA_FIELDS}, limit=limit)

    async def get_ig_media_insights(self, media_id: str) -> dict[str, int]:
        """Метрики по одной: сбой одной метрики не роняет остальные (токен и лимиты — роняют)."""
        result: dict[str, int] = {}
        for metric in IG_MEDIA_METRICS:
            try:
                payload = await self._http.request("GET", f"{media_id}/insights", params={"metric": metric})
            except errors.MetaError as exc:
                if exc.kind in (errors.TOKEN_EXPIRED, errors.TOKEN_INVALID, errors.RATE_LIMIT):
                    raise
                logger.info("Метрика %s недоступна для %s: %s", metric, media_id, exc.kind)
                continue
            for item in payload.get("data") or []:
                values = item.get("values") or [{}]
                result[item.get("name", metric)] = int(values[0].get("value", 0))
        return result

    async def business_discovery(self, ig_user_id: str, username: str) -> dict[str, Any] | None:
        fields = f"business_discovery.username({username}){{{BUSINESS_DISCOVERY_FIELDS}}}"
        try:
            payload = await self._http.request("GET", ig_user_id, params={"fields": fields})
        except errors.MetaError as exc:
            if exc.kind != errors.INVALID_PARAMETER:
                raise
            return None  # не бизнес-аккаунт или ник не найден
        return payload.get("business_discovery")

    async def debug_token(self) -> TokenInfo:
        if not (self._app_id and self._app_secret):
            await self._http.request("GET", "me", params={"fields": "id"})
            return TokenInfo(is_valid=True, expires_at=None, expiry_known=False)
        params = {"input_token": self._token, "access_token": f"{self._app_id}|{self._app_secret}"}
        payload = await self._http.request("GET", "debug_token", params=params)
        return TokenInfo.from_debug(payload.get("data") or {})

    async def get_engagement_audience_size(self, audience_id: str) -> int | None:
        fields = "approximate_count_lower_bound,approximate_count_upper_bound"
        row = await self._http.request("GET", audience_id, params={"fields": fields})
        low = row.get("approximate_count_lower_bound", -1)
        return None if low is None or low < 0 else int(low)

    # --------------------------------------------------------------- изменение

    async def _currency(self, account_id: str) -> str:
        if account_id not in self._currencies:
            await self.get_ad_account(account_id)
        return self._currencies[account_id]

    def _refuse_in_validate_only(self, action: str) -> None:
        if self._validate_only:
            raise errors.MetaError(
                errors.VALIDATE_ONLY_UNSUPPORTED,
                f"В режиме проверки действие «{action}» недоступно: у Meta для него нет проверочного режима.",
            )

    async def _account_of(self, object_id: str) -> str:
        """Рекламный аккаунт самого объекта (кэшируется): по нему сверяются валюта и лимит запросов."""
        if object_id not in self._object_accounts:
            row = await self._http.request("GET", object_id, params={"fields": "account_id"})
            if not row.get("account_id"):
                raise errors.MetaError(errors.UNKNOWN, "Meta не вернула рекламный аккаунт объекта.")
            self._object_accounts[object_id] = f"act_{row['account_id']}"
        return self._object_accounts[object_id]

    async def _money_wire(self, account_id: str, params: Mapping[str, Any]) -> dict[str, Any]:
        """Деньги в параметрах — только Money в валюте аккаунта; на проводе — строка минимальных единиц."""
        wire = dict(params)
        for key in MONEY_KEYS:
            if key not in wire:
                continue
            await self._check_money(account_id, key, wire[key])
            wire[key] = str(wire[key].amount_minor)
        return wire

    @staticmethod
    def _require_money(key: str, value: object) -> None:
        if not isinstance(value, Money):
            raise TypeError(f"{key}: деньги передаются только как Money")

    async def _check_money(self, account_id: str, key: str, value: object) -> None:
        self._require_money(key, value)
        currency = await self._currency(account_id)
        if value.currency != currency:
            raise errors.MetaError(
                errors.CURRENCY_MISMATCH,
                f"Сумма указана в {value.currency}, а валюта рекламного аккаунта — {currency}. Укажите сумму в {currency}.",
            )

    async def _create(
        self, account_id: str, edge: str, params: Mapping[str, Any], key: str | None, *, review: bool = False
    ) -> str | None:
        body = await self._money_wire(account_id, params)
        if self._validate_only:
            body["execution_options"] = [VALIDATE_ONLY, SYNC_REVIEW] if review else [VALIDATE_ONLY]
            key = None
        payload = await self._http.request(
            "POST", f"{account_id}/{edge}", body=body, idempotency_key=key, creating=not self._validate_only,
        )
        return None if self._validate_only else str(payload["id"])

    async def create_campaign(self, account_id: str, params: Mapping[str, Any], *, idempotency_key: str | None = None) -> str | None:
        return await self._create(account_id, "campaigns", params, idempotency_key)

    async def create_adset(self, account_id: str, params: Mapping[str, Any], *, idempotency_key: str | None = None) -> str | None:
        return await self._create(account_id, "adsets", params, idempotency_key)

    async def create_creative(self, account_id: str, params: Mapping[str, Any], *, idempotency_key: str | None = None) -> str | None:
        return await self._create(account_id, "adcreatives", params, idempotency_key)

    async def create_ad(self, account_id: str, params: Mapping[str, Any], *, idempotency_key: str | None = None) -> str | None:
        return await self._create(account_id, "ads", params, idempotency_key, review=True)

    async def create_audience(self, account_id: str, params: Mapping[str, Any]) -> str:
        self._refuse_in_validate_only("создание аудитории")
        payload = await self._http.request("POST", f"{account_id}/customaudiences", body=dict(params), creating=True)
        return str(payload["id"])

    async def upload_image(self, account_id: str, data: bytes, filename: str = "image.jpg") -> str:
        self._refuse_in_validate_only("загрузка изображения")
        body = {"bytes": base64.b64encode(data).decode(), "name": filename}
        payload = await self._http.request("POST", f"{account_id}/adimages", body=body, creating=True)
        image = next(iter((payload.get("images") or {}).values()), None)
        if not image or "hash" not in image:
            raise errors.MetaError(errors.UNKNOWN, "Meta не вернула хэш загруженного изображения.")
        return str(image["hash"])

    async def upload_video(self, account_id: str, data: bytes, filename: str = "video.mp4") -> str:
        self._refuse_in_validate_only("загрузка видео")
        files = {"source": (filename, data, "application/octet-stream")}
        payload = await self._http.request(
            "POST", f"{account_id}/advideos", body={"name": filename}, files=files, creating=True
        )
        return str(payload["id"])

    async def set_status(self, object_id: str, status: str) -> None:
        self._refuse_in_validate_only("изменение статуса")
        account = await self._account_of(object_id)
        await self._http.request("POST", object_id, body={"status": status}, limiter_key=account)

    async def set_daily_budget(self, object_id: str, budget: Money) -> None:
        self._refuse_in_validate_only("изменение бюджета")
        self._require_money("daily_budget", budget)
        account = await self._account_of(object_id)
        await self._check_money(account, "daily_budget", budget)
        await self._http.request("POST", object_id, body={"daily_budget": str(budget.amount_minor)}, limiter_key=account)

    async def set_spend_cap(self, campaign_id: str, cap: Money) -> None:
        self._refuse_in_validate_only("изменение лимита расходов")
        self._require_money("spend_cap", cap)
        account = await self._account_of(campaign_id)
        await self._check_money(account, "spend_cap", cap)
        await self._http.request("POST", campaign_id, body={"spend_cap": str(cap.amount_minor)}, limiter_key=account)

    async def delete(self, object_id: str) -> None:
        self._refuse_in_validate_only("удаление")
        account = await self._account_of(object_id)
        await self._http.request("DELETE", object_id, limiter_key=account)

