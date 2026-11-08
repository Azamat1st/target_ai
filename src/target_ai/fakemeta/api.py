"""FakeGraphAPI: httpx.MockTransport-обработчик, повторяющий форму ответов и ошибок Graph API."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from target_ai.clock import Clock
from target_ai.fakemeta import errors, insights, rules
from target_ai.fakemeta.data import INTERESTS
from target_ai.fakemeta.errors import FakeError
from target_ai.fakemeta.seed import seed_demo
from target_ai.fakemeta.state import FakeState, KeyValueStore, decode_query, paginate, project

IDEMPOTENCY_HEADER = "x-client-idempotent-key"
CREATE_EDGES = {"campaigns": "campaign", "adsets": "adset", "adcreatives": "creative", "ads": "ad"}
VERSION_RE = re.compile(r"^v\d+\.\d+$")
DISCOVERY_RE = re.compile(r"business_discovery\.username\(([^)]+)\)(\{.*\})?")
WRITE_COST = 3
READ_COST = 1


@dataclass(frozen=True, kw_only=True)
class RecordedRequest:
    method: str
    url: str  # полный адрес запроса
    path: str  # без версии и ведущего слэша, например "act_1/campaigns"
    params: dict[str, Any]
    body: dict[str, Any]
    headers: dict[str, str]  # имена в нижнем регистре
    status: int
    response: dict[str, Any]


@dataclass
class _Injection:
    pattern: re.Pattern[str]
    error: FakeError
    remaining: int


class _Fail(Exception):
    def __init__(self, error: FakeError) -> None:
        self.error = error


@dataclass
class _Bucket:
    points: int
    window_seconds: int
    spent: dict[str, list[tuple[Any, int]]] = field(default_factory=lambda: defaultdict(list))


class FakeGraphAPI:
    def __init__(self, store: KeyValueStore | None = None, clock: Clock | None = None) -> None:
        self.state = FakeState(store, clock)
        self._requests: list[RecordedRequest] = []
        self._injections: list[_Injection] = []
        self._bucket: _Bucket | None = None
        self.ignore_idempotency_keys = False
        self.transport = httpx.MockTransport(self._handle)

    # ------------------------------------------------------------ публичное для тестов
    def seed_demo(self) -> None:
        seed_demo(self.state)

    @property
    def requests(self) -> tuple[RecordedRequest, ...]:
        return tuple(self._requests)

    def requests_matching(self, pattern: str) -> tuple[RecordedRequest, ...]:
        regex = re.compile(pattern)
        return tuple(r for r in self._requests if regex.search(f"{r.method} {r.path}"))

    def clear_requests(self) -> None:
        self._requests.clear()

    def inject_error(self, match: str, error: FakeError, times: int = 1) -> None:
        """Следующие `times` запросов, у которых «METHOD path» подходит под regex `match`, получат `error`."""
        self._injections.append(_Injection(re.compile(match), error, times))

    def enable_rate_limit(self, points: int = 60, window_seconds: int = 300) -> None:
        self._bucket = _Bucket(points, window_seconds)

    def set_token_valid(self, valid: bool) -> None:
        self.state.data["token"]["is_valid"] = valid
        self.state.save()

    def disapprove_ad(self, ad_id: str, reason: str = "Нарушение политики Meta", *, issue_summary: str = "Запрещённый контент") -> None:
        ad = self._must(ad_id)
        feedback = {"global": {reason: "Объявление нарушает рекламные политики."}, "placement_specific": {"instagram": {reason: ""}}}
        ad.update(review="DISAPPROVED", review_feedback=feedback,
                  issues_info=[{"level": "AD", "error_code": 1487301, "error_summary": issue_summary,
                                "error_message": reason, "error_type": "AD_REVIEW_DISAPPROVED"}])
        self.state.save()

    def approve_ad(self, ad_id: str) -> None:
        self._must(ad_id).update(review="APPROVED", review_feedback=None, issues_info=[])
        self.state.save()

    def object(self, object_id: str) -> dict[str, Any] | None:
        return self.state.obj(object_id)

    def objects(self, kind: str | None = None, account_id: str | None = None, **kw: Any) -> list[dict[str, Any]]:
        return self.state.objects(kind, account_id, **kw)

    def _must(self, object_id: str) -> dict[str, Any]:
        obj = self.state.obj(object_id)
        if obj is None:
            raise KeyError(object_id)
        return obj

    # ------------------------------------------------------------ транспорт
    def _handle(self, request: httpx.Request) -> httpx.Response:
        method = request.method
        segs = [s for s in request.url.path.split("/") if s]
        if segs and VERSION_RE.match(segs[0]):
            segs = segs[1:]
        path = "/".join(segs)
        params = decode_query(dict(request.url.params))
        headers = {k.lower(): v for k, v in request.headers.items()}
        body = _body(request)
        extra_headers: dict[str, str] = {}
        try:
            late = self._inject(method, path)
            self._authorize(headers)
            self._throttle(segs, method)
            status, payload = 200, self._route(method, segs, params, body, headers)
            if late is not None:
                self.state.save()
                raise _Fail(late)
        except _Fail as fail:
            if fail.error.drop_connection:
                self._record(request, path, params, body, headers, 0, {"error": "connection dropped"})
                raise httpx.ConnectError("fake connection dropped", request=request) from None
            status, payload, extra_headers = fail.error.status, fail.error.body(), fail.error.headers
        if method != "GET" and status == 200:
            self.state.save()
        self._record(request, path, params, body, headers, status, payload)
        return httpx.Response(status, json=payload, headers=extra_headers)

    def _record(self, request: httpx.Request, path: str, params: dict, body: dict, headers: dict, status: int, response: dict) -> None:
        self._requests.append(RecordedRequest(method=request.method, url=str(request.url), path=path, params=params, body=body,
                                              headers=headers, status=status, response=response))

    def _inject(self, method: str, path: str) -> FakeError | None:
        """Ошибку отдаёт сразу; если она «после исполнения» — возвращает её, чтобы выдать после маршрута."""
        for item in self._injections:
            if item.remaining > 0 and item.pattern.search(f"{method} {path}"):
                item.remaining -= 1
                if item.error.after_execute:
                    return item.error
                raise _Fail(item.error)
        return None

    def _authorize(self, headers: dict[str, str]) -> None:
        if not headers.get("authorization", "").startswith("Bearer "):
            raise _Fail(errors.token_revoked())
        if not self.state.data["token"]["is_valid"]:
            raise _Fail(errors.token_expired())

    def _throttle(self, segs: list[str], method: str) -> None:
        bucket = self._bucket
        key = self._throttle_key(segs, method)
        if bucket is None or key is None:
            return
        now = self.state.clock.now()
        window = [(t, c) for t, c in bucket.spent[key] if (now - t).total_seconds() < bucket.window_seconds]
        cost = READ_COST if method == "GET" else WRITE_COST
        if window and sum(c for _, c in window) + cost > bucket.points:
            wait = bucket.window_seconds - (now - window[0][0]).total_seconds()
            bucket.spent[key] = window
            raise _Fail(errors.rate_limited(80004, minutes=max(1, int(wait // 60) + 1)))
        bucket.spent[key] = [*window, (now, cost)]

    def _throttle_key(self, segs: list[str], method: str) -> str | None:
        """Аккаунт для корзины: act_* из пути, а запись в объект по id — на его аккаунт."""
        if not segs:
            return None
        if segs[0].startswith("act_"):
            return segs[0]
        obj = self.state.obj(segs[0])
        return obj["account_id"] if obj is not None and method != "GET" else None

    # ------------------------------------------------------------ маршрутизация
    def _route(self, method: str, segs: list[str], params: dict, body: dict, headers: dict) -> dict[str, Any]:
        path = "/".join(segs)
        match segs:
            case ["me", "adaccounts"]:
                return self._list(list(self.state.data["accounts"].values()), params, path)
            case ["me", "accounts"]:
                pages = [{k: v for k, v in p.items() if k != "access_token"} for p in self.state.data["pages"].values()]
                return self._list(pages, params, path)
            case ["me"]:
                return {"id": "900000000000001"}
            case ["debug_token"]:
                return {"data": {"app_id": "1", "type": "SYSTEM_USER", **self.state.data["token"]}}
            case ["search"]:
                return self._search(params, path)
            case [account, *rest] if account.startswith("act_"):
                return self._account(method, account, rest, params, body, headers, path)
            case [object_id, *rest]:
                return self._object(method, object_id, rest, params, body, path, headers)
        raise _Fail(errors.invalid_parameter())

    def _list(self, rows: list[dict], params: dict, path: str) -> dict[str, Any]:
        fields = params.get("fields")
        return paginate([project(r, fields) if fields else r for r in rows], params, path)

    def _search(self, params: dict, path: str) -> dict[str, Any]:
        query = str(params.get("q", "")).lower()
        rows = [
            {"id": str(6003000000000 + i), "name": name, "path": [section, name], "audience_size_lower_bound": size // 2,
             "audience_size_upper_bound": size}
            for i, (name, section, size) in enumerate(INTERESTS) if query in name.lower()
        ]
        return paginate(rows, params, path)

    def _account(self, method: str, account_id: str, rest: list[str], params: dict, body: dict, headers: dict, path: str) -> dict[str, Any]:
        account = self.state.data["accounts"].get(account_id)
        if account is None:
            raise _Fail(FakeError(code=100, subcode=33, message="Unsupported get request: object does not exist"))
        match method, rest:
            case "GET", []:
                return project({**account, "id": account["id"]}, params.get("fields") or "id,name")
            case "GET", ["minimum_budgets"]:
                return {"data": [{"currency": account.get("budgets_currency", account["currency"]), **account["minimum_budgets"]}]}
            case "GET", [("campaigns" | "adsets" | "ads" | "adcreatives") as edge]:
                kind = CREATE_EDGES[edge]
                rows = [self.state.view(o) for o in self.state.objects(kind, account_id)]
                return self._list(rows, params, path)
            case "GET", ["customaudiences"]:
                return self._list([self._view(o) for o in self.state.objects("audience", account_id)], params, path)
            case "POST", [("campaigns" | "adsets" | "adcreatives" | "ads") as edge]:
                return self._create(account, CREATE_EDGES[edge], body, headers)
            case "POST", ["customaudiences"]:
                return self._create_audience(account, body, headers)
            case "POST", ["adimages"]:
                return self._upload_image(body)
            case "POST", ["advideos"]:
                return {"id": self.state.new_id()}
            case "GET", ["delivery_estimate"]:
                low, high = insights.estimate_bounds(params.get("targeting_spec") or {})
                return {"data": [{"estimate_ready": True, "estimate_mau_lower_bound": low, "estimate_mau_upper_bound": high}]}
            case "GET", ["reachestimate"]:
                low, high = insights.estimate_bounds(params.get("targeting_spec") or {})
                return {"data": {"users_lower_bound": low, "users_upper_bound": high}, "estimate_ready": True}
            case "GET", ["insights"]:
                return paginate(insights.build_rows(self.state, account_id, params), params, path)
        raise _Fail(errors.invalid_parameter())

    # ------------------------------------------------------------ создание
    def _create(self, account: dict, kind: str, body: dict, headers: dict) -> dict[str, Any]:
        options = body.get("execution_options") or []
        validate_only = "validate_only" in options
        key = None if validate_only or self.ignore_idempotency_keys else headers.get(IDEMPOTENCY_HEADER)
        fingerprint = _fingerprint(kind, body)
        if known := self._replay(key, fingerprint):
            return known
        failure = self._validate_create(account, kind, body)
        if failure:
            raise _Fail(failure)
        if validate_only:
            return {"success": True}
        obj = self._store_object(account, kind, body)
        self._remember(key, fingerprint, obj["id"])
        return {"id": obj["id"]}

    def _validate_create(self, account: dict, kind: str, body: dict) -> FakeError | None:
        match kind:
            case "campaign":
                return rules.validate_campaign(account, body)
            case "adset":
                return rules.validate_adset(self.state, account, body)
            case "creative":
                return rules.validate_creative(self.state, body)
            case _:
                return rules.validate_ad(self.state, account, body)

    def _store_object(self, account: dict, kind: str, body: dict) -> dict[str, Any]:
        parent_id = body.get("campaign_id") if kind == "adset" else body.get("adset_id") if kind == "ad" else None
        parent = self.state.obj(str(parent_id)) if parent_id else None
        extra: dict[str, Any] = {"status": body.get("status", "PAUSED")}
        if parent is not None:
            extra.update(parent_id=parent["id"], campaign_id=parent.get("campaign_id") or parent["id"])
        if kind == "ad":
            extra.update(adset_id=parent["id"], review="PENDING")
        for key in ("daily_budget", "spend_cap"):
            if body.get(key) is not None:
                extra[key] = int(body[key])
        return self.state.add_object(kind, account["id"], dict(body), **extra)

    def _create_audience(self, account: dict, body: dict, headers: dict) -> dict[str, Any]:
        key = None if self.ignore_idempotency_keys else headers.get(IDEMPOTENCY_HEADER)
        fingerprint = _fingerprint("audience", body)
        if known := self._replay(key, fingerprint):
            return known
        failure = rules.validate_audience(self.state, body)
        if failure:
            raise _Fail(failure)
        obj = self.state.add_object("audience", account["id"], dict(body), status="ACTIVE")
        low = rules.audience_size(self.state, body, obj["id"])
        obj.update(count_low=low, count_high=int(low * 1.25))
        self._remember(key, fingerprint, obj["id"])
        return {"id": obj["id"]}

    def _upload_image(self, body: dict) -> dict[str, Any]:
        digest = hashlib.sha1(str(body.get("bytes", "")).encode()).hexdigest()
        self.state.data["images"][digest] = True
        return {"images": {str(body.get("name", "image.jpg")): {"hash": digest, "url": f"https://cdn.example/{digest}.jpg"}}}

    # ------------------------------------------------------------ объекты по id
    def _object(self, method: str, object_id: str, rest: list[str], params: dict, body: dict, path: str,
                headers: dict) -> dict[str, Any]:
        data = self.state.data
        if object_id in data["ig"]:
            return self._ig(method, data["ig"][object_id], rest, params, path)
        if object_id in data["media"]:
            return self._media_insights(data["media"][object_id], rest, params)
        if object_id in data["pages"]:
            return self._page(method, data["pages"][object_id], rest, params, body, path, headers)
        obj = self.state.obj(object_id)
        if obj is None:
            raise _Fail(FakeError(code=100, subcode=33, message="Unsupported get request: object does not exist"))
        return self._ad_object(method, obj, rest, params, body, path)

    def _page(self, method: str, page: dict, rest: list[str], params: dict, body: dict, path: str,
              headers: dict) -> dict[str, Any]:
        match method, rest:
            case "GET", []:
                return project({**page, "id": page["id"]}, params.get("fields") or "id,name")
            case "GET", ["leadgen_forms"]:
                self._require_page_token(page, headers)
                forms = [self.state.view(o) for o in self.state.objects("leadform", parent_id=page["id"])]
                return self._list(forms, params, path)
            case "POST", ["leadgen_forms"]:
                self._require_page_token(page, headers)
                return self._create_leadform(page, body, headers)
        raise _Fail(errors.invalid_parameter())

    @staticmethod
    def _require_page_token(page: dict, headers: dict) -> None:
        if headers.get("authorization") != f"Bearer {page['access_token']}":
            raise _Fail(errors.permission_denied())

    def _create_leadform(self, page: dict, body: dict, headers: dict) -> dict[str, Any]:
        key = None if self.ignore_idempotency_keys else headers.get(IDEMPOTENCY_HEADER)
        fingerprint = _fingerprint("leadform", body)
        if known := self._replay(key, fingerprint):
            return known
        if failure := rules.validate_leadform(body):
            raise _Fail(failure)
        obj = self.state.add_object("leadform", page["account_id"], dict(body), parent_id=page["id"], status="ACTIVE")
        self._remember(key, fingerprint, obj["id"])
        return {"id": obj["id"]}

    def _replay(self, key: str | None, fingerprint: str) -> dict[str, Any] | None:
        """Повтор запроса с тем же ключом отдаёт тот же id; тот же ключ с другими параметрами - ошибка."""
        if not key or key not in self.state.data["idem"]:
            return None
        known = self.state.data["idem"][key]
        if known["fp"] != fingerprint:
            raise _Fail(FakeError(code=100, subcode=1349016, message="Idempotency key reused with different parameters"))
        return {"id": known["id"]}

    def _remember(self, key: str | None, fingerprint: str, object_id: str) -> None:
        if key:
            self.state.data["idem"][key] = {"fp": fingerprint, "id": object_id}

    def _ad_object(self, method: str, obj: dict, rest: list[str], params: dict, body: dict, path: str) -> dict[str, Any]:
        account = self.state.data["accounts"][obj["account_id"]]
        match method, rest:
            case "GET", []:
                return project(self._view(obj), params.get("fields") or "id,name")
            case "GET", [("adsets" | "ads") as edge]:
                kind = CREATE_EDGES[edge]
                rows = [self.state.view(o) for o in self.state.objects(kind, parent_id=obj["id"])]
                return self._list(rows, params, path)
            case "GET", ["insights"]:
                return paginate(insights.build_rows(self.state, obj["id"], params), params, path)
            case "POST", []:
                return self._update(obj, account, body)
            case "DELETE", []:
                obj["deleted"] = True
                return {"success": True}
        raise _Fail(errors.invalid_parameter())

    def _view(self, obj: dict) -> dict:
        view = self.state.view(obj)
        if obj["kind"] == "audience":
            view.update(approximate_count_lower_bound=obj["count_low"], approximate_count_upper_bound=obj["count_high"],
                        time_created=int(datetime.fromisoformat(obj["created_time"]).timestamp()))
        return view

    def _update(self, obj: dict, account: dict, body: dict) -> dict[str, Any]:
        failure = rules.validate_update(self.state, obj, account, body)
        if failure:
            raise _Fail(failure)
        if "status" in body:
            obj["status"] = body["status"]
        for key in ("daily_budget", "spend_cap"):
            if key in body:
                obj[key] = int(body[key])
        if "name" in body:
            obj["params"]["name"] = body["name"]
        return {"success": True}

    # ------------------------------------------------------------ Instagram
    def _ig(self, method: str, ig: dict, rest: list[str], params: dict, path: str) -> dict[str, Any]:
        match method, rest:
            case "GET", []:
                fields = str(params.get("fields", "id,username"))
                found = DISCOVERY_RE.search(fields)
                result = project(ig, DISCOVERY_RE.sub("", fields).strip(","))
                if found:
                    result["business_discovery"] = self._discover(found.group(1))
                return result
            case "GET", ["media"]:
                rows = [m for m in self.state.data["media"].values() if m["ig_id"] == ig["id"]]
                return self._list([{k: v for k, v in m.items() if k not in ("insights", "ig_id")} for m in rows], params, path)
        raise _Fail(errors.invalid_parameter())

    def _discover(self, username: str) -> dict[str, Any]:
        rival = next((i for i in self.state.data["ig"].values() if i["username"] == username), None)
        if rival is None:
            raise _Fail(FakeError(code=100, subcode=2207013, message="Invalid user id: not a business account or not found"))
        media = [m for m in self.state.data["media"].values() if m["ig_id"] == rival["id"]]
        keys = ("caption", "like_count", "comments_count", "media_type", "permalink", "timestamp")
        return {**{k: rival[k] for k in ("followers_count", "media_count", "biography", "website")},
                "media": {"data": [{k: m[k] for k in keys} for m in media]}, "id": rival["id"]}

    def _media_insights(self, media: dict, rest: list[str], params: dict) -> dict[str, Any]:
        if rest != ["insights"]:
            return project(media, params.get("fields") or "id")
        metric = str(params.get("metric", ""))
        names = [m for m in metric.split(",") if m]
        unknown = [m for m in names if m not in media["insights"]]
        if unknown:
            raise _Fail(errors.invalid_parameter("metric"))
        return {"data": [{"name": n, "period": "lifetime", "values": [{"value": media["insights"][n]}], "title": n} for n in names]}


def _fingerprint(kind: str, body: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps([kind, body], sort_keys=True, default=str).encode()).hexdigest()


def _body(request: httpx.Request) -> dict[str, Any]:
    if not request.content or "json" not in request.headers.get("content-type", ""):
        return {}
    try:
        parsed = json.loads(request.content)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


