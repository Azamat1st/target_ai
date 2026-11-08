"""Реестр обработчиков: каждый диспетчер получает свежие `Router` (aiogram не даёт подключить роутер дважды)."""

from __future__ import annotations

from aiogram import Router


class Routes:
    def __init__(self, name: str) -> None:
        self._name = name
        self._items: list[tuple[str, tuple, object]] = []

    def message(self, *filters):
        return self._register("message", filters)

    def callback_query(self, *filters):
        return self._register("callback_query", filters)

    def _register(self, kind: str, filters: tuple):
        def decorate(handler):
            self._items.append((kind, filters, handler))
            return handler
        return decorate

    def build(self) -> Router:
        router = Router(name=self._name)
        for kind, filters, handler in self._items:
            getattr(router, kind).register(handler, *filters)
        return router
