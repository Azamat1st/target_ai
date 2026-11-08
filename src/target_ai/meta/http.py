"""HTTP-слой Graph API: авторизация, ограничитель, повторы, разбор ошибок."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from collections.abc import Mapping
from typing import Any

import httpx

from target_ai.clock import Clock
from target_ai.meta import errors
from target_ai.meta.limiter import READ_COST, WRITE_COST, RequestLimiter, Sleeper, usage_pause_seconds

logger = logging.getLogger(__name__)

BASE_URL = "https://graph.facebook.com"
IDEMPOTENCY_HEADER = "X-Client-Idempotent-Key"
MAX_RATE_ATTEMPTS = 5
MAX_TRANSIENT_ATTEMPTS = 3
RATE_BACKOFF_SECONDS = 30.0
TRANSIENT_BACKOFF_SECONDS = 2.0
TIMEOUT_SECONDS = 30.0


def encode_params(params: Mapping[str, Any] | None) -> dict[str, str]:
    """Сложные значения Graph API передаёт JSON-строкой."""
    out: dict[str, str] = {}
    for key, value in (params or {}).items():
        if value is None:
            continue
        out[key] = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return out


def account_key(path: str) -> str | None:
    """Ключ ограничителя: act_<id> из пути, иначе None (запрос вне рекламного аккаунта)."""
    head = path.strip("/").split("/", 1)[0]
    return head if head.startswith("act_") else None


class GraphHttp:
    def __init__(
        self,
        *,
        token: str,
        version: str,
        transport: httpx.AsyncBaseTransport | None,
        app_secret: str | None,
        clock: Clock,
        sleeper: Sleeper | None,
        user_agent: str,
    ) -> None:
        self._token = token
        self._app_secret = app_secret
        self._sleeper: Sleeper = sleeper or asyncio.sleep
        self._limiter = RequestLimiter(clock, self._sleeper)
        self._client = httpx.AsyncClient(
            base_url=f"{BASE_URL}/{version}/",
            transport=transport,
            timeout=TIMEOUT_SECONDS,
            headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _proof(self) -> dict[str, str]:
        if not self._app_secret:
            return {}
        digest = hmac.new(self._app_secret.encode(), self._token.encode(), hashlib.sha256).hexdigest()
        return {"appsecret_proof": digest}

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
        files: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        creating: bool = False,
        limiter_key: str | None = None,
    ) -> dict[str, Any]:
        """Один запрос с повторами. creating=True — создающий запрос: после сбоя с неизвестным исходом
        не повторяется (повтор внутри клиента обходит сверку по тегу), а только при лимите запросов."""
        key = limiter_key or account_key(path)
        cost = READ_COST if method == "GET" else WRITE_COST
        headers = {IDEMPOTENCY_HEADER: idempotency_key} if idempotency_key else {}
        query = {**encode_params(params), **self._proof()}
        rate_attempt = transient_attempt = 0
        while True:
            await self._limiter.acquire(key, cost)
            try:
                response = await self._client.request(
                    method, path.lstrip("/"), params=query, json=body if files is None else None,
                    data=encode_params(body) if files is not None else None, files=files, headers=headers,
                )
                result = self._handle(response, key)
            except httpx.TransportError as exc:
                result = errors.MetaError(errors.NETWORK, errors._MESSAGES[errors.NETWORK], is_transient=True)
                logger.warning("Meta: сбой соединения %s %s: %s", method, path, type(exc).__name__)
            if not isinstance(result, errors.MetaError):
                return result
            if result.kind == errors.RATE_LIMIT:
                rate_attempt += 1
                wait = result.retry_after or RATE_BACKOFF_SECONDS * 2 ** (rate_attempt - 1)
                self._limiter.block(key, wait)
                if rate_attempt >= MAX_RATE_ATTEMPTS:
                    raise self._final_rate_error(result, wait)
                continue
            if result.is_transient and creating:
                result.outcome_unknown = True
            elif result.is_transient:
                transient_attempt += 1
                if transient_attempt < MAX_TRANSIENT_ATTEMPTS:
                    await self._sleeper(TRANSIENT_BACKOFF_SECONDS * 2 ** (transient_attempt - 1))
                    continue
            raise result

    @staticmethod
    def _final_rate_error(error: errors.MetaError, wait: float) -> errors.MetaError:
        return errors.MetaError(
            errors.RATE_LIMIT, errors.rate_limit_message(wait), code=error.code, subcode=error.subcode,
            retry_after=wait, fbtrace_id=error.fbtrace_id, http_status=error.http_status,
        )

    def _handle(self, response: httpx.Response, key: str | None) -> dict[str, Any] | errors.MetaError:
        headers = {name.lower(): value for name, value in response.headers.items()}
        warning = headers.get("x-ad-api-version-warning")
        if warning:
            logger.warning("Meta: предупреждение о версии API: %s", warning)
        pause = usage_pause_seconds(headers)
        if pause:
            self._limiter.block(key, pause)
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if response.is_success and not (isinstance(payload, dict) and "error" in payload):
            return payload if isinstance(payload, dict) else {}
        error = errors.parse_error(payload, response.status_code, retry_after=pause)
        logger.warning(
            "Meta: ошибка %s/%s kind=%s fbtrace_id=%s blame=%s", error.code, error.subcode, error.kind,
            error.fbtrace_id, ",".join(error.blame_fields) or "-",
        )
        return error
