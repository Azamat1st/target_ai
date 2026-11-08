"""Анализ компании (R13–R17, R60, R66, R105i): Instagram Graph API или скриншоты, сайт и конкуренты — Аналитик."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from target_ai.agents import AnalystInput, Objective, Passport, PassportFact, Questionnaire, Screenshot, analyze
from target_ai.llm.errors import LLMError
from target_ai.meta.errors import MetaError
from target_ai.storage import DialogState, Fact, MediaAsset, Passport as PassportRow

from .media import store_file
from .passport_view import USER_SOURCE, render_passport
from .texts import button

log = logging.getLogger(__name__)

STATE_SCREENS = "analysis:screens"
KIND_ANALYSIS, KIND_PLAN_START = "analysis_go", "plan_start"
MAX_SCREENSHOTS = 3
TOP_POSTS = 12
INSIGHT_LOOKUPS = 10
WAIT_TEXT = "Изучаю компанию, это займёт 1–3 минуты…"


@dataclass(frozen=True)
class AnalysisResult:
    status: str  # done | needs_screenshots | failed | no_company
    passport_version: int | None = None


def engagement_rating(post: dict[str, Any], insights: dict[str, int], followers: int | None) -> float | None:
    """(лайки + комментарии + сохранения + репосты) / охват; без охвата — / подписчики (R36)."""
    interactions = (int(post.get("like_count") or 0) + int(post.get("comments_count") or 0)
                    + int(insights.get("saved", 0)) + int(insights.get("shares", 0)))
    denominator = insights.get("reach") or followers
    return round(interactions / denominator, 4) if denominator else None


class Analysis:
    def __init__(self, db, gateway_factory, llm, clock, notifier, *, media_dir: Path) -> None:
        self._db, self._factory, self._llm = db, gateway_factory, llm
        self._clock, self._notifier, self._dir = clock, notifier, media_dir

    # ------------------------------------------------------------------ вход
    async def start(self, company_id: int, chat_id: int) -> AnalysisResult:
        """Instagram API, если доступен (не демо и IG привязан); иначе просит 1–3 скриншота."""
        company = await self._io(self._db.companies.get, company_id)
        if company is None:
            return AnalysisResult("no_company")
        shots = await self._io(self._db.media.list, company_id=company_id, kind="screenshot")
        if await self._api_available(company) or shots:
            return await self.run(company_id, chat_id)
        await self._io(self._db.dialogs.save, DialogState(chat_id=chat_id, state=STATE_SCREENS, data={"company_id": company_id}, updated_at=self._clock.now()))
        skip = self._btn("Пропустить", "skip", company_id)
        await self._notifier.send(chat_id, "Instagram через API сейчас недоступен (демо или нет доступа). Пришлите 1–3 скриншота "
                                           "профиля Instagram — я прочитаю их. Или нажмите «Пропустить».", [[skip]])
        return AnalysisResult("needs_screenshots")

    async def receive_screenshot(self, chat_id: int, data: bytes, filename: str) -> bool:
        dialog = await self._io(self._db.dialogs.get, chat_id)
        if dialog is None or dialog.state != STATE_SCREENS:
            return False
        company_id = int(dialog.data["company_id"])
        path = await self._io(store_file, self._dir, company_id, data, filename)
        await self._io(self._db.media.create, MediaAsset(company_id=company_id, kind="screenshot", path=str(path), created_at=self._clock.now()))
        count = len(await self._io(self._db.media.list, company_id=company_id, kind="screenshot"))
        if count >= MAX_SCREENSHOTS:
            await self.run(company_id, chat_id)
            return True
        row = [self._btn("Анализировать", "go", company_id), self._btn("Пропустить", "skip", company_id)]
        await self._notifier.send(chat_id, f"Получил скриншотов: {count} из {MAX_SCREENSHOTS}. Пришлите ещё или нажмите «Анализировать».", [row])
        return True

    async def handle_callback(self, chat_id: int, approval_id: str, choice: str) -> bool:
        approval = await self._io(self._db.approvals.get, approval_id)
        if approval is None or approval.kind != KIND_ANALYSIS:
            return False
        if await self._io(self._db.approvals.consume, approval_id, self._clock.now()) is None:
            await self._notifier.send(chat_id, "Эта кнопка уже использована или устарела.", [])
            return True
        company_id = int(approval.payload["company_id"])
        await self.run(company_id, chat_id, use_screenshots=choice != "skip")
        return True

    async def add_fact(self, company_id: int, chat_id: int, text: str) -> Fact | None:
        """«добавь факт: …» — источник «пользователь», только такие факты доверенные (R66)."""
        body = text.strip()
        if not body:
            await self._notifier.send(chat_id, "Напишите сам факт, например: «добавь факт: скидка 10% до 30 ноября».", [])
            return None
        fact = await self._io(self._db.facts.create, Fact(company_id=company_id, text=body[:500], source=USER_SOURCE, kind="user", created_at=self._clock.now()))
        await self._notifier.send(chat_id, f"Факт добавлен (от вас): {fact.text}", [])
        return fact

    # ------------------------------------------------------------------ анализ
    async def run(self, company_id: int, chat_id: int, *, use_screenshots: bool = True) -> AnalysisResult:
        company = await self._io(self._db.companies.get, company_id)
        if company is None:
            return AnalysisResult("no_company")
        await self._io(self._db.dialogs.delete, chat_id)
        await self._notifier.send(chat_id, WAIT_TEXT, [])
        profile, posts = await self._collect_instagram(company)
        screens = await self._load_screenshots(company_id) if use_screenshots else ()
        facts = await self._io(self._db.facts.list, company_id=company_id)
        data = AnalystInput(
            questionnaire=Questionnaire(company_name=company.name, instagram_handle=company.instagram_handle,
                                        website=company.website, geo=company.profile.get("geo"),
                                        objective=company.profile.get("objective")),
            ig_profile=profile, ig_posts=tuple(posts), screenshots=screens,
            competitor_urls=tuple(str(c) for c in company.competitors), history=await self._history(company_id),
            user_facts=tuple(PassportFact(text=f.text, source=f.source) for f in facts if f.kind == "user"),
            external_facts=tuple(PassportFact(text=f.text, source=f.source) for f in facts if f.kind != "user"),
        )
        try:
            passport = await analyze(self._llm, data)
        except LLMError as exc:
            retry = self._btn("Повторить", "retry", company_id)
            reason = getattr(exc, "user_message_ru", None) or "Не удалось получить ответ от Claude."
            await self._notifier.send(chat_id, f"{reason}\nДанные компании сохранены, компания остаётся черновиком.", [[retry]])
            return AnalysisResult("failed")
        row = await self._io(self._save, company, passport)
        await self._publish(chat_id, company, row.version, passport)
        return AnalysisResult("done", row.version)

    def _save(self, company, passport: Passport) -> PassportRow:
        """Паспорт новой версией, факты с источниками; статус компании — active."""
        with self._db.transaction():
            latest = self._db.passports.latest(company.id)
            row = self._db.passports.create(PassportRow(
                company_id=company.id, version=(latest.version + 1) if latest else 1,
                data=passport.model_dump(mode="json"), created_at=self._clock.now()))
            known = {f.text.strip().lower() for f in self._db.facts.list(company_id=company.id)}
            for fact in passport.facts:
                if fact.text.strip().lower() not in known:
                    known.add(fact.text.strip().lower())
                    self._db.facts.create(Fact(company_id=company.id, text=fact.text, source=fact.source,
                                               kind="external", created_at=self._clock.now()))
            self._db.companies.update(replace(company, status="active"))
        return row

    async def _publish(self, chat_id: int, company, version: int, passport: Passport) -> None:
        facts = await self._io(self._db.facts.list, company_id=company.id)
        messages = render_passport(company.name, version, passport, facts, company.currency)
        for text in messages[:-1]:
            await self._notifier.send(chat_id, text, [])
        plan_btn = button(self._db, self._clock.now(), "➡️ Составить медиаплан", KIND_PLAN_START, "start", company.id, {"company_id": company.id})
        await self._notifier.send(chat_id, messages[-1], [[plan_btn]])

    # ------------------------------------------------------------------ источники
    async def _api_available(self, company) -> bool:
        level = await self._io(self._db.settings.get, "level", "demo")
        return level != "demo" and bool(company.ig_user_id)

    async def _collect_instagram(self, company) -> tuple[dict | None, list[dict]]:
        if not await self._api_available(company):
            return None, []
        gateway = self._factory(False)
        try:
            profile = await gateway.get_ig_profile(company.ig_user_id)
            media = await gateway.get_ig_media(company.ig_user_id, limit=50)
        except MetaError as exc:
            log.warning("Instagram API недоступен: %s", exc.user_message_ru)
            return None, []
        followers = profile.get("followers_count")
        ranked = await self._rank(gateway, media, followers)
        await self._io(self._remember_posts, company.id, ranked)
        return {k: profile.get(k) for k in ("username", "name", "biography", "website", "followers_count", "media_count")}, ranked

    async def _rank(self, gateway, media: list[dict], followers: int | None) -> list[dict]:
        by_likes = sorted(media, key=lambda m: int(m.get("like_count") or 0) + int(m.get("comments_count") or 0), reverse=True)
        rated: list[dict] = []
        for index, post in enumerate(by_likes):
            insights: dict[str, int] = {}
            if index < INSIGHT_LOOKUPS:
                try:
                    insights = await gateway.get_ig_media_insights(post["id"])
                except MetaError:
                    insights = {}  # ошибка одной метрики не роняет анализ
            rating = engagement_rating(post, insights, followers)
            rated.append({"id": post["id"], "caption": post.get("caption", ""), "type": post.get("media_product_type"),
                          "permalink": post.get("permalink"), "likes": post.get("like_count"),
                          "comments": post.get("comments_count"), "engagement": rating})
        return sorted(rated, key=lambda p: p["engagement"] or 0.0, reverse=True)[:TOP_POSTS]

    def _remember_posts(self, company_id: int, posts: list[dict]) -> None:
        known = {m.meta_id for m in self._db.media.list(company_id=company_id, kind="ig_post")}
        for post in posts:
            if post["id"] not in known:
                self._db.media.create(MediaAsset(company_id=company_id, kind="ig_post", meta_id=post["id"],
                                                 caption=post["caption"], data={"engagement": post["engagement"]},
                                                 created_at=self._clock.now()))

    async def _load_screenshots(self, company_id: int) -> tuple[Screenshot, ...]:
        assets = await self._io(self._db.media.list, company_id=company_id, kind="screenshot")
        shots = []
        for asset in assets[:MAX_SCREENSHOTS]:
            path = Path(asset.path or "")
            if path.is_file():
                media_type = mimetypes.guess_type(path.name)[0] or "image/png"
                shots.append(Screenshot(media_type=media_type, data=await self._io(path.read_bytes)))
        return tuple(shots)

    async def _history(self, company_id: int) -> tuple[str, ...]:
        rows = await self._io(self._db.history.list, company_id=company_id)
        return tuple(f"{r.hypothesis or r.kind}: {r.outcome or 'без итога'}" for r in rows)

    def _btn(self, label: str, choice: str, company_id: int) -> tuple[str, str]:
        return button(self._db, self._clock.now(), label, KIND_ANALYSIS, choice, company_id, {"company_id": company_id})

    @staticmethod
    async def _io(fn, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)
