"""Симулятор статистики: правдоподобные строки /insights для активных своих объектов, детерминированно."""

from __future__ import annotations

import random
from datetime import date
from typing import Any

from target_ai.clock import Clock
from target_ai.fakemeta.api import FakeGraphAPI
from target_ai.money import offset

SEED = 7
SPEND_LOW, SPEND_HIGH = 0.85, 1.25
SPIKE_CHANCE, SPIKE_HIGH = 0.04, 1.6
TRUE_CPA_LOW, TRUE_CPA_HIGH = 0.08, 0.35  # доля дневного бюджета
CPM_USD_LOW, CPM_USD_HIGH = 5.0, 12.0
CTR_LOW, CTR_HIGH = 0.008, 0.025
BASE_FREQUENCY, FREQUENCY_GROWTH = 1.1, 0.06
MAX_FREQUENCY = 6.0
LINK_CLICK_SHARE = 0.8
RESULT_NOISE = 0.15
PER_USD = {"USD": 1.0, "EUR": 0.92, "JPY": 150.0}  # грубый курс только для правдоподобных показов


class Simulator:
    def __init__(self, fake: FakeGraphAPI, clock: Clock, *, seed: int = SEED) -> None:
        self._fake = fake
        self._clock = clock
        self._seed = seed

    def simulate_day(self, day: date) -> int:
        """Генерирует строки статистики за день для активных своих групп и объявлений. Возвращает число строк."""
        state = self._fake.state
        self._approve_pending()
        rows = 0
        for adset in state.objects("adset"):
            if adset["foreign"] or not self._is_running(adset, day):
                continue
            totals = self._adset_totals(adset, day)
            if totals["spend_minor"] <= 0:
                continue
            state.put_stat(adset["id"], day.isoformat(), totals)
            rows += 1 + self._split_to_ads(adset, totals, day)
        state.save()
        return rows

    def _approve_pending(self) -> None:
        for ad in self._fake.state.objects("ad"):
            if ad["review"] == "PENDING":
                ad["review"] = "APPROVED"

    def _is_running(self, obj: dict[str, Any], day: date) -> bool:
        started = date.fromisoformat(obj["created_time"][:10])
        return day >= started and self._fake.state.effective_status(obj) == "ACTIVE"

    def _rng(self, *parts: object) -> random.Random:
        return random.Random(":".join(str(p) for p in (self._seed, *parts)))

    def _adset_totals(self, adset: dict[str, Any], day: date) -> dict[str, Any]:
        state = self._fake.state
        account = state.data["accounts"][adset["account_id"]]
        hidden, daily = self._rng("adset", adset["id"]), self._rng("day", adset["id"], day.isoformat())
        budget = adset.get("daily_budget") or 0
        factor = daily.uniform(SPEND_LOW, SPEND_HIGH) if daily.random() > SPIKE_CHANCE else daily.uniform(SPEND_HIGH, SPIKE_HIGH)
        spend = min(round(budget * factor), self._cap_room(adset, day))
        true_cpa = max(1.0, budget * hidden.uniform(TRUE_CPA_LOW, TRUE_CPA_HIGH))
        cpm_minor = hidden.uniform(CPM_USD_LOW, CPM_USD_HIGH) * PER_USD.get(account["currency"], 1.0) * offset(account["currency"])
        impressions = round(spend / cpm_minor * 1000)
        age = (day - date.fromisoformat(adset["created_time"][:10])).days
        frequency = min(MAX_FREQUENCY, BASE_FREQUENCY + FREQUENCY_GROWTH * age + daily.uniform(0, 0.1))
        ctr = hidden.uniform(CTR_LOW, CTR_HIGH)
        clicks = round(impressions * ctr * daily.uniform(0.9, 1.1))
        goal = adset["params"].get("optimization_goal", "LINK_CLICKS")
        reach = round(impressions / frequency)
        results = reach if goal == "REACH" else max(0, round(spend / true_cpa * daily.uniform(1 - RESULT_NOISE, 1 + RESULT_NOISE)))
        return {"spend_minor": spend, "impressions": impressions, "reach": reach, "frequency": frequency,
                "clicks": clicks, "link_clicks": round(clicks * LINK_CLICK_SHARE), "results": results, "goal": goal}

    def _cap_room(self, adset: dict[str, Any], day: date) -> int:
        """Сколько ещё можно потратить до spend_cap кампании (без учёта уже записанной строки этого дня)."""
        state = self._fake.state
        campaign = state.obj(adset["parent_id"])
        cap = campaign.get("spend_cap") if campaign else None
        if cap is None:
            return 10**18
        return max(0, cap - state.campaign_spent(campaign["id"], exclude=(adset["id"], day.isoformat())))

    def _split_to_ads(self, adset: dict[str, Any], totals: dict[str, Any], day: date) -> int:
        ads = [a for a in self._fake.state.objects("ad", parent_id=adset["id"]) if self._is_running(a, day)]
        if not ads:
            return 0
        weights = [self._rng("ad", a["id"]).uniform(0.6, 1.4) for a in ads]
        total_weight = sum(weights)
        for ad, weight in zip(ads, weights, strict=True):
            share = weight / total_weight
            quality = self._rng("adq", ad["id"]).uniform(0.7, 1.3)
            row = {
                "spend_minor": round(totals["spend_minor"] * share), "impressions": round(totals["impressions"] * share),
                "reach": round(totals["reach"] * share), "frequency": totals["frequency"],
                "clicks": round(totals["clicks"] * share * quality), "link_clicks": round(totals["link_clicks"] * share * quality),
                "results": round(totals["results"] * share * quality), "goal": totals["goal"],
            }
            self._fake.state.put_stat(ad["id"], day.isoformat(), row)
        return len(ads)

