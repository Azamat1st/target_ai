"""Присланные фото и видео (R37): привязка к компании, лимит 20 МБ Bot API, файлы в `data/media/`."""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from target_ai.notify import Notifier
from target_ai.storage import Database, MediaAsset

from .texts import button

log = logging.getLogger(__name__)

MAX_FILE_BYTES = 20 * 1024 * 1024
KINDS = ("image", "video")
ALLOWED_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov"})
KIND_MEDIA_COMPANY = "media_company"
PENDING_DIR = "pending"


def store_file(media_dir: Path, folder: str | int, data: bytes, filename: str) -> Path:
    """Сохраняет файл под случайным именем (имя из Telegram в путь не попадает)."""
    suffix = Path(filename).suffix.lower()
    target_dir = media_dir / str(folder)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{uuid.uuid4().hex}{suffix if suffix in ALLOWED_SUFFIXES else '.bin'}"
    path.write_bytes(data)
    return path


def current_company_ids(db: Database, chat_id: int) -> list[int]:
    """Компания из текущего диалога, иначе все компании (если одна — она и есть текущая)."""
    dialog = db.dialogs.get(chat_id)
    if dialog is not None and dialog.data.get("company_id"):
        return [int(dialog.data["company_id"])]
    return [c.id for c in db.companies.list()]


class MediaIntake:
    def __init__(self, db: Database, clock, notifier: Notifier, *, media_dir: Path) -> None:
        self._db, self._clock, self._notifier, self._dir = db, clock, notifier, media_dir

    async def receive(self, chat_id: int, kind: str, data: bytes | None, filename: str, *,
                      size: int | None = None, caption: str | None = None) -> None:
        """`data=None` — бот не смог скачать (файл больше лимита): передаёт только `size`."""
        total = size if size is not None else len(data or b"")
        if total > MAX_FILE_BYTES or data is None:
            await self._notifier.send(chat_id, "Файл больше 20 МБ — Telegram не отдаёт такие файлы боту. "
                                               "Пришлите, пожалуйста, файл поменьше.", [])
            return
        if kind not in KINDS:
            await self._notifier.send(chat_id, "Присылайте фото или видео.", [])
            return
        ids = await asyncio.to_thread(current_company_ids, self._db, chat_id)
        if not ids:
            await self._notifier.send(chat_id, "Сначала добавьте компанию (кнопка «➕ Компания»), потом присылайте фото и видео.", [])
            return
        if len(ids) == 1:
            await self._attach(chat_id, ids[0], kind, data, filename, caption)
            return
        path = await asyncio.to_thread(store_file, self._dir, PENDING_DIR, data, filename)
        rows = []
        for company_id in ids:
            company = await asyncio.to_thread(self._db.companies.get, company_id)
            payload = {"path": str(path), "kind": kind, "caption": caption, "chat_id": chat_id, "company_id": company_id}
            rows.append([button(self._db, self._clock.now(), company.name, KIND_MEDIA_COMPANY, "pick", company_id, payload)])
        await self._notifier.send(chat_id, "К какой компании привязать этот файл?", rows)

    async def handle_callback(self, chat_id: int, approval_id: str, choice: str) -> bool:
        approval = await asyncio.to_thread(self._db.approvals.get, approval_id)
        if approval is None or approval.kind != KIND_MEDIA_COMPANY:
            return False
        used = await asyncio.to_thread(self._db.approvals.consume, approval_id, self._clock.now())
        if used is None:
            await self._notifier.send(chat_id, "Эта кнопка уже использована или устарела.", [])
            return True
        p = approval.payload
        await self._save(chat_id, int(p["company_id"]), p["kind"], Path(p["path"]), p.get("caption"))
        return True

    async def _attach(self, chat_id: int, company_id: int, kind: str, data: bytes, filename: str, caption: str | None) -> None:
        path = await asyncio.to_thread(store_file, self._dir, company_id, data, filename)
        await self._save(chat_id, company_id, kind, path, caption)

    async def _save(self, chat_id: int, company_id: int, kind: str, path: Path, caption: str | None) -> None:
        asset = MediaAsset(company_id=company_id, kind=kind, path=str(path), caption=caption, created_at=self._clock.now())
        await asyncio.to_thread(self._db.media.create, asset)
        company = await asyncio.to_thread(self._db.companies.get, company_id)
        await self._notifier.send(chat_id, f"Сохранил файл для «{company.name}». Он будет в креативах следующего медиаплана.", [])
