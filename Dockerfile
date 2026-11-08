# Образ бота: Python 3.12 + uv. Секреты в образ не попадают — они приходят из .env при запуске.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.14 /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# Обычный пользователь без прав администратора (uid 1000 совпадает с первым пользователем на типичном VPS).
RUN useradd --create-home --uid 1000 bot \
    && mkdir -p /app/data \
    && chown -R bot:bot /app

WORKDIR /app
USER bot

# Сначала зависимости (этот слой кэшируется), потом код.
COPY --chown=bot:bot pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY --chown=bot:bot src ./src
RUN uv sync --frozen --no-dev

VOLUME ["/app/data"]

CMD ["python", "-m", "target_ai"]
