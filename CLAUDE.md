<!-- autopilot:start -->
# Агентная система таргета Instagram

Telegram-бот, через который один владелец ведёт рекламу в Instagram для нескольких компаний-клиентов: агенты на Claude анализируют компанию, строят медиаплан и объясняют оптимизацию, а деньги в Meta тратит только код с жёсткими лимитами.

## Команды

| Команда | Что делает |
|---------|------------|
| `uv sync` | Установить зависимости (Python 3.12 uv скачает сам; системный python3 — 3.9, не подходит) |
| `uv run pytest -q` | Все тесты (последний полный прогон: 1080 passed); сеть и `.env` не нужны |
| `uv run pytest tests/unit/money/test_parse.py -q` | Один файл; `-k <имя>` — один тест |
| `uv run pytest tests/e2e -q` | Сквозные сценарии (приложение целиком) |
| `uv run pytest --cov=target_ai -q` | Покрытие (цель нового кода — 80%) |
| `./scripts/demo-transcript.sh` | Демо-цикл на `OfflineLLM`, пишет `docs/demo-transcript.md`; сеть и ключи не нужны |
| `./start.sh` | `uv sync` + запуск бота; создаёт `.env` из `.env.example`. Без настоящего `TELEGRAM_BOT_TOKEN` и `TELEGRAM_ALLOWED_IDS` бот не поднимется: выйдет с кодом 2 и подсказкой |
| `uv run python -m target_ai` | То же без `uv sync` и без создания `.env` |
| `docker compose up -d` | Запуск на сервере (`Dockerfile`, том `./data`, `env_file: .env`) |

Линтера и форматтера в проекте нет; `pytest` настроен в `pyproject.toml` (`asyncio_mode=auto`, `pythonpath=src`).

## Структура

```
src/target_ai/
  config/       Settings (из .env) и runtime.py — ключи настроек в БД и их умолчания
  money/        Money, валюты Meta, parse_amount_ru, format_ru
  clock/        Clock, RealClock, SimClock (смещение в БД), to_account_time
  storage/      SQLite: database.py, models.py (frozen-датаклассы), repositories.py, migrations/0001_init.sql
  policy/       чистые функции: лимиты, темп, правила оптимизатора, validate_plan, check_action
  meta/         MetaClient (Graph API), MetaGateway, MetaError, лимитер, regions (ЕС)
  fakemeta/     FakeGraphAPI (httpx.MockTransport) и Simulator статистики — демо-уровень и тесты
  llm/          LLM-протокол, ClaudeLLM, OfflineLLM, ScriptedLLM, конверт недоверенных данных
  agents/       промпты, схемы и вызовы агентов (analyst/strategist/copywriter/controller/optimizer/intent)
  executor/     единственный, кто меняет Meta: сага запуска, стоп-кран, реестр объектов, аудит
  services/     intake (анкета, анализ, план), ops (статистика, алерты, оптимизация, отчёты, перемотка), admin (уровни, компании, настройки, журнал)
  scheduler/    jobs-таблица, tick(), run_until() для перемотки
  notify/       Notifier-протокол, FakeNotifier, TelegramNotifier
  bot/          aiogram: роутеры, колбэки, команды, меню, middleware доступа
  app/          App.create — сборка зависимостей; LevelGateways, LevelClock, ClockAwareLevelSwitch
tests/          unit/<модуль>/, e2e/, support/fake_telegram.py, conftest.py (db, now)
scripts/        demo-transcript.sh -> demo_transcript.py
docs/           adr/0001..0012 (решения), demo-transcript.md (генерируется)
```

## Ключевые файлы

- `src/target_ai/app/app.py` — `App.create/run/startup/shutdown`, вся сборка; `overrides`: `llm, meta_transport, fake_meta, clock, base_clock, bot_session, notifier, claude_probe, meta_sleeper, media_dir, tick_seconds` (другой ключ — `ValueError`).
- `src/target_ai/__main__.py` — точка входа, `check_settings` (подсказки про `.env`), код выхода 2 при плохих настройках.
- `src/target_ai/bot/routes.py`, `callbacks.py`, `commands.py` — порядок: доступ -> дедупликация `update_id` -> команды/меню -> колбэки -> медиа -> текст.
- `src/target_ai/executor/executor.py`, `saga.py`, `core.py`, `controls.py` — запуск, откат, стоп-кран, `enable_object`.
- `src/target_ai/policy/check.py`, `spend.py`, `optimizer.py`, `plan.py` — `check_action`, `daily_cap`, `decide`, `validate_plan`.
- `src/target_ai/meta/client.py`, `errors.py` — `MetaClient`, повторы, `MetaError.outcome_unknown`.
- `src/target_ai/fakemeta/api.py` — фейк Graph API (`inject_error`, `enable_rate_limit`, `requests_matching`).
- `src/target_ai/config/runtime.py` — ключи настроек в БД (`level`, `halted`, `optimizer_rules`, `alert_thresholds`, `install_id`, `report_time`, `report_tz`), `Thresholds`.
- `src/target_ai/storage/models.py`, `repositories.py` — модели и репозитории; новые миграции — следующий `storage/migrations/NNNN_*.sql`.
- `tests/support/fake_telegram.py` — `FakeTelegram` (`say`, `press`, `press_button`, `.texts`, `.last`); `tests/e2e/conftest.py` — класс `E2E` (приложение + «человек»).
- `README.md` — инструкция для владельца и словарь; сверяется с кодом тестом `tests/unit/docs/test_readme.py`.

## Архитектура

Поток: Telegram -> `bot` -> `services` (intake / ops / admin) -> `agents` (через `llm`) и `policy` (чистые функции) -> `executor` -> `meta` (`MetaGateway`). Планировщик (`scheduler`, тик каждые 30 с) вызывает `services.ops` по расписанию; ответы уходят через `notify`.

- Уровень работы глобальный (`demo|test|live`, по умолчанию `demo`), читается из `db.settings` при каждом обращении (`LevelGateways`): демо — всегда `FakeGraphAPI` и симулированное время (`LevelClock`), тест — реальная Meta с `validate_only=True` (+ второй клиент без него для временных «[ТЕСТ]»-родителей), боевой — реальная Meta. Смена уровня — без перезапуска, в боевой и из него — через кнопку.
- Без ключа Claude приложение работает на `OfflineLLM` (демо-тексты с пометкой `DEMO_MARK`).
- Деньги: `policy` решает (`Allowed | Denied | NeedsApproval`), `executor` исполняет и сам потребляет подтверждение (`db.approvals.consume`, атомарно, до действия). Ключ подтверждения привязан к действию И снимку состояния (`action_key`).
- Запуск — сага с шагами (`campaign, audience:N, adset:N, media, leadform, creative, ad, activate`), каждое создание помечено тегом `[ta:<install_id>:L<launch_id>:<шаг>]` и ключом идемпотентности; после обрыва сначала сверка по тегу.
- Стоп-кран (`halted`) перечитывается из БД до и после каждого включающего вызова Meta; все включающие пути идут через `Core.enable_object`.
- Оптимизатор считает решения в коде (`policy.decide`), модель только объясняет и предлагает паузы; порядок исполнения: паузы -> снижения -> переносы -> масштабирование.

Запрещено (часть проверяется тестами):
- Изменяющие методы шлюза (`create_*`, `upload_*`, `set_*`, `delete`) вне `executor`, `meta`, `fakemeta` — `tests/unit/executor/test_fence.py` (AST-обход, ловит и `getattr` по имени).
- `agents` не импортируют `storage`, `meta`, `executor` — `tests/unit/agents/test_boundaries.py`; `policy` ввода-вывода не имеет — `tests/unit/policy/test_policy_boundaries.py`.
- Деньги только `money.Money` (целые минимальные единицы + валюта); `float` -> `TypeError`; ввод — `Decimal`; смешение валют -> `CurrencyMismatch`.
- Время только через `Clock`; `datetime.now()` вне `clock/` запрещён; naive datetime в моделях -> `ValueError`.
- Секреты только из `Settings` (`SecretStr`), в логах маскируются (`logging_setup.mask_secrets`); `print` в приложении запрещён — `logging`.
- Данные из внешних источников (подписи, посты, факты не от владельца) модели передаются только в `<untrusted_data source=...>` (`llm.wrap_untrusted`); доверенные факты — только добавленные владельцем через бота (`Fact.kind="user"`).

## Соглашения кода

- Модели неизменяемы: `@dataclass(frozen=True, kw_only=True)` / pydantic `frozen`; обновление — `dataclasses.replace` + `repo.update`.
- Пользовательские тексты (бот, ошибки `user_message_ru`, отчёты) — на русском; идентификаторы и комментарии к коду — по месту (идентификаторы английские).
- Ключи настроек в БД и их умолчания — только в `config/runtime.py`; остальные модули импортируют оттуда.
- Колбэки `callback_data` ≤ 64 байт; намерение кнопки хранится в `Approval.payload`, в колбэке — только `ap:<approval_id>:<choice>` (и префиксы `lvl set cmp log sys ops launch`).
- Хранилище синхронное: из async звать через `asyncio.to_thread`; транзакция целиком внутри одного вызова.
- Тестовые доп. ограждения: `test_fence` (мутации Meta), `test_boundaries` (импорты агентов), `test_runtime_keys` (ключи настроек).
- Файлы 200–400 строк, максимум 800; функции < 50 строк; новые решения — ADR в `docs/adr/`.

## Окружение

Читает `config.Settings` из `.env` (окружение процесса важнее файла; пустое значение = не задано). Значения не хранить в репозитории.

| Переменная | Зачем |
|------------|-------|
| `TELEGRAM_BOT_TOKEN` | токен бота от @BotFather; без него бот не стартует |
| `TELEGRAM_ALLOWED_IDS` | числовые Telegram ID владельцев через запятую (белый список); без них бот не стартует |
| `ANTHROPIC_API_KEY` | ключ Claude; без него `OfflineLLM` (демо), для боевого уровня нужен |
| `MODEL_STRONG`, `MODEL_CHEAP` | модели для сильного и дешёвого яруса агентов |
| `META_ACCESS_TOKEN` | токен Meta для тестового и боевого уровня |
| `META_APP_ID`, `META_APP_SECRET` | приложение Meta: проверка срока токена (`debug_token`), `appsecret_proof` |
| `META_API_VERSION` | версия Graph API |
| `DATABASE_PATH` | путь к SQLite (по умолчанию `data/target_ai.sqlite3`) |
| `REPORT_TZ` | часовой пояс отчётов по умолчанию |
| `LOG_LEVEL` | уровень логирования |

## Тесты

- `pytest` + `pytest-asyncio` (`asyncio_mode=auto`); структура Arrange-Act-Assert; фикстуры `db` (файловая SQLite в tmp), `now` — `tests/conftest.py`.
- `tests/unit/<модуль>/` повторяет `src/target_ai/<модуль>/`; `tests/e2e/` — приложение целиком (`test_demo_cycle.py`, `test_meta_errors.py`, `test_money.py`).
- Сети нет: Meta — `FakeGraphAPI.transport`, Claude — `ScriptedLLM`/`OfflineLLM`, Telegram — `FakeTelegram().session` в `overrides["bot_session"]`, часы — `SimClock`.
- Один файл: `uv run pytest tests/unit/executor/test_launch.py -q`.
- Стенограмма демо: `./scripts/demo-transcript.sh` -> `docs/demo-transcript.md` (перегенерируется целиком, руками не править).
- `tests/unit/docs/test_readme.py` падает, если переменная из `.env.example` не описана в `README.md` (формат `` `NAME=` ``) или команда бота исчезла из `bot/commands.py`.

## Обнаружено при сборке

- `pydantic` закреплён `<2.14`: `aiogram 3.31` не ставится с более новым.
- `anthropic` 2.x ходит через `httpx2` (транзитивная зависимость, в dev-группе указана явно); подмена транспорта в тестах — `httpx2.MockTransport`, у не-стримингового запроса с большим `max_tokens` нужен явный `timeout`.
- Meta не поддерживает KZT/UZS: таблица валют `money/currencies.py` — ровно официальная страница Meta (сверено 2026-11-08); неизвестная валюта -> ошибка, не догадка.
- `parse_amount_ru` принимает только короткий фрагмент суммы (≤ 60 символов) со строгим белым списком слов; главная защита суммы — показ в валюте аккаунта и кнопка «Всё верно» (D01).
- `Database.transaction()` держит блокировку: транзакция целиком — внутри одного `asyncio.to_thread`, без `await` внутри.
- Изменения в Meta делает только `executor`; создание после обрыва (`MetaError.outcome_unknown`) повторяется только после сверки тега `[ta:<install_id>:L<id>:<шаг>]`.
- Тесты с `validate_only=True`: клиент отказывает в `set_*`, `delete`, `upload_*`, `create_audience` (`validate_only_unsupported`) — поэтому тестовый уровень держит второй клиент.
- `Claude`: `messages.create` + `output_config.format` и ручной `model_validate_json` вместо `messages.parse` (тот прячет `stop_reason` при обрезке); без `temperature`/`top_p`, prefill и forced `tool_choice`.
- Лимит Meta: корзина 60 баллов/300 с на `act_*`; запись в объект по id тоже списывается на аккаунт.
- Приложение в тестах — только с `overrides["base_clock"] = FixedClock()` (`tests/support/clock.py`, 2026-11-08 18:00 UTC): иначе демо-время идёт от настоящего «сейчас», дневной бюджет зависит от дня запуска, и ожидаемые суммы краснеют на следующий день.

## Как здесь работает Autopilot

Сборка ведётся навыком `/autopilot`. Требования, спецификация и таски — в `.autopilot/`.
Прогресс — `.autopilot/dashboard.html`. Правило: требование из `manifest.md`
может снять только пользователь.

Если работа продолжается — скажи «продолжи автопилот»: состояние поднимется
из `.autopilot/state.js`, переспрашивать ничего не нужно.
<!-- autopilot:end -->
