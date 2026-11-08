"""Хранилище фейкового Graph API: словари в памяти, опционально сохраняются в SQLite (db.fake_meta)."""

from __future__ import annotations

import json
from typing import Any, Protocol
from urllib.parse import urlencode

from target_ai.clock import Clock, RealClock

STATE_KEY = "graph_state"
ID_BASE = 120_000_000_000_000
DEFAULT_PAGE_LIMIT = 25

OWN_KINDS = ("campaign", "adset", "ad", "creative", "audience")


class KeyValueStore(Protocol):
    def get(self, key: str, default: Any = None) -> Any: ...
    def set(self, key: str, value: Any) -> None: ...


def _fresh() -> dict[str, Any]:
    return {
        "seq": 0, "accounts": {}, "pages": {}, "ig": {}, "media": {}, "objects": {}, "stats": {},
        "idem": {}, "images": {}, "token": {"is_valid": True, "expires_at": 0, "scopes": ["ads_management", "ads_read"]},
    }


class FakeState:
    def __init__(self, store: KeyValueStore | None, clock: Clock | None) -> None:
        self._store = store
        self.clock: Clock = clock or RealClock()
        loaded = store.get(STATE_KEY) if store is not None else None
        self.data: dict[str, Any] = loaded if isinstance(loaded, dict) else _fresh()

    def save(self) -> None:
        if self._store is not None:
            self._store.set(STATE_KEY, self.data)

    def new_id(self) -> str:
        self.data["seq"] += 1
        return str(ID_BASE + self.data["seq"])

    # --- объекты рекламы -------------------------------------------------
    def add_object(self, kind: str, account_id: str, fields: dict[str, Any], **extra: Any) -> dict[str, Any]:
        obj = {
            "id": self.new_id(), "kind": kind, "account_id": account_id, "deleted": False, "foreign": False,
            "status": "PAUSED", "created_time": self.clock.now().isoformat(), "review": "APPROVED",
            "review_feedback": None, **extra, "params": fields,
        }
        self.data["objects"][obj["id"]] = obj
        return obj

    def obj(self, object_id: str) -> dict[str, Any] | None:
        return self.data["objects"].get(object_id)

    def objects(self, kind: str | None = None, account_id: str | None = None, *, parent_id: str | None = None,
                include_deleted: bool = False) -> list[dict[str, Any]]:
        return [
            o for o in self.data["objects"].values()
            if (kind is None or o["kind"] == kind) and (account_id is None or o["account_id"] == account_id)
            and (parent_id is None or o.get("parent_id") == parent_id) and (include_deleted or not o["deleted"])
        ]

    def effective_status(self, obj: dict[str, Any]) -> str:
        if obj["deleted"]:
            return "DELETED"
        if obj["kind"] == "ad" and obj["review"] == "DISAPPROVED":
            return "DISAPPROVED"
        if obj["status"] != "ACTIVE":
            return obj["status"]
        if obj["kind"] == "campaign" and self.cap_reached(obj):
            return "WITH_ISSUES"  # упёрлись в spend_cap кампании
        parent = self.obj(obj["parent_id"]) if obj.get("parent_id") else None
        if parent is not None and parent["status"] != "ACTIVE":
            return f"{parent['kind'].upper()}_PAUSED"
        grand = self.obj(parent["parent_id"]) if parent and parent.get("parent_id") else None
        campaign = grand or parent
        if campaign is not None and campaign["kind"] == "campaign" and (
            campaign["status"] != "ACTIVE" or self.cap_reached(campaign)
        ):
            return "CAMPAIGN_PAUSED"
        return "PENDING_REVIEW" if obj["kind"] == "ad" and obj["review"] == "PENDING" else "ACTIVE"

    def campaign_spent(self, campaign_id: str, *, exclude: tuple[str, str] | None = None) -> int:
        """Накопленный расход кампании (мин. единицы) по строкам её групп; exclude=(id, день) — строку не считать."""
        return sum(
            row["spend_minor"]
            for adset in self.objects("adset", parent_id=campaign_id, include_deleted=True)
            for day, row in self.data["stats"].get(adset["id"], {}).items() if (adset["id"], day) != exclude
        )

    def cap_reached(self, campaign: dict[str, Any]) -> bool:
        cap = campaign.get("spend_cap")
        return cap is not None and self.campaign_spent(campaign["id"]) >= cap

    def view(self, obj: dict[str, Any]) -> dict[str, Any]:
        """Плоское представление объекта в форме ответа Graph API."""
        row = {
            "id": obj["id"], "name": obj["params"].get("name", ""), "status": obj["status"],
            "effective_status": self.effective_status(obj), "account_id": obj["account_id"].removeprefix("act_"),
            "created_time": obj["created_time"], "objective": obj["params"].get("objective"),
            "campaign_id": obj.get("campaign_id"), "adset_id": obj.get("adset_id"),
            "ad_review_feedback": obj["review_feedback"], "issues_info": obj.get("issues_info", []),
        }
        for key in ("daily_budget", "spend_cap"):
            if obj.get(key) is not None:
                row[key] = str(obj[key])
        return row

    # --- статистика ------------------------------------------------------
    def put_stat(self, object_id: str, day: str, row: dict[str, Any]) -> None:
        self.data["stats"].setdefault(object_id, {})[day] = row


def project(row: dict[str, Any], fields: str | None) -> dict[str, Any]:
    """Только запрошенные поля (id возвращается всегда); без fields — id и name."""
    names = [f.strip() for f in fields.split(",")] if fields else ["name"]
    out = {"id": row["id"]} if "id" in row else {}
    out.update({name: row[name] for name in names if name in row and row[name] is not None})
    return out


def paginate(rows: list[dict[str, Any]], params: dict[str, Any], path: str) -> dict[str, Any]:
    limit = max(1, int(params.get("limit") or DEFAULT_PAGE_LIMIT))
    start = int(params.get("after") or 0)
    chunk = rows[start:start + limit]
    body: dict[str, Any] = {"data": chunk}
    if rows:
        paging: dict[str, Any] = {"cursors": {"before": str(start), "after": str(start + len(chunk))}}
        if start + limit < len(rows):
            paging["next"] = f"https://graph.facebook.com/{path}?" + urlencode({"limit": limit, "after": start + limit})
        body["paging"] = paging
    return body


def decode_query(raw: dict[str, str]) -> dict[str, Any]:
    """Значения запроса Graph API: JSON-строки раскрываются обратно в структуры."""
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if value[:1] in "[{":
            try:
                out[key] = json.loads(value)
                continue
            except ValueError:
                pass
        out[key] = value
    return out
