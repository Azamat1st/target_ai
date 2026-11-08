#!/usr/bin/env bash
# Запуск одной командой: проверка окружения, установка зависимостей, старт бота.
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "Не найден uv — менеджер Python-проектов, на нём всё держится."
  echo "Установите его одной командой и запустите ./start.sh снова:"
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo "(или, если есть Homebrew: brew install uv)"
  exit 1
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Файл .env не найден — создан из .env.example."
  echo "Откройте .env и впишите: TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_IDS (ваш числовой ID),"
  echo "ANTHROPIC_API_KEY и данные Meta (META_ACCESS_TOKEN, META_APP_ID, META_APP_SECRET)."
  echo "Для знакомства в демо-режиме ключи Claude и Meta можно не вписывать."
fi

uv sync
exec uv run python -m target_ai
