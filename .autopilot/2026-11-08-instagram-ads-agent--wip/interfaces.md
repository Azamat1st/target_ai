# Интерфейсы

Этот файл читает каждый исполнитель **до того, как писать код**. Сверху — правила проекта и границы, решённые в спецификации (копия `spec.md` → «Границы и швы»). Снизу — то, что построили завершённые таски: их публичные сигнатуры дописываются после каждого таска.

## Правила проекта

- **Стек.** Python 3.12 (`.python-version`), менеджер — `uv` (`pyproject.toml` + `uv.lock`). Пакет — `src/target_ai/`, тесты — `tests/`. Зависимости (ставит **только таск 01**, одной командой, все сразу): `aiogram>=3.31,<4`, `anthropic>=2.6,<3`, `httpx>=0.28`, `pydantic>=2.14,<3`, `pydantic-settings>=2.16`, `tzdata`; dev: `pytest>=9`, `pytest-asyncio`, `pytest-cov`. Любой следующий таск, которому не хватает пакета, **не ставит его**, а возвращает `BLOCKED` с названием пакета.
- **Команды.** `uv sync` — установка; `uv run pytest -q` — все тесты; `uv run pytest tests/<путь> -q` — часть; `./start.sh` — запуск бота; `uv run python -m target_ai` — то же без проверок.
- **Сеть в тестах запрещена.** Meta — только через `FakeGraphAPI.transport`; Claude — `ScriptedLLM`/`OfflineLLM`; Telegram — фейковая сессия бота. Ни один тест не требует `.env`.
- **Деньги** — только `money.Money` (целые минимальные единицы + валюта). `float` для денег запрещён; ввод — `Decimal`.
- **Время** — только через `clock.Clock` (`now()` — aware datetime в UTC; перевод в часовой пояс аккаунта — `zoneinfo`). Прямой `datetime.now()` вне модуля `clock` запрещён.
- **Изменения в Meta** — только модуль `executor`. Модули `agents`, `services`, `bot` не вызывают изменяющие методы `MetaGateway` (проверяется тестом на импорты/вызовы в таске 05).
- **Агенты** не импортируют `storage`, `meta`, `executor`.
- **Секреты** — только из `config.Settings`; в лог и в сообщения не попадают. Логгер — `logging`, никакого `print` в коде приложения.
- **Стиль** (правила пользователя): неизменяемые структуры (`@dataclass(frozen=True)`, pydantic `frozen` где уместно), обновление — новой копией; файлы 200–400 строк, максимум 800; функции < 50 строк; вложенность ≤ 4; константы вместо «магических чисел»; ошибки обрабатываются явно, ничего не глотается молча; пользовательские тексты — на русском; идентификаторы — на английском.
- **Тесты.** pytest, структура Arrange-Act-Assert, имена описывают поведение. Сначала тест (красный), затем код. Покрытие нового кода ≥ 80% (`uv run pytest --cov=target_ai`).
- **Не трогать:** `.autopilot/` (кроме случаев, когда таск прямо говорит), `.env`.
- **Справочники.** Meta — `.autopilot/2026-11-08-instagram-ads-agent--wip/research-meta.md` (по номерам разделов, указанным в спеке §6). Claude SDK — скилл claude-api: `/private/tmp/claude-501/bundled-skills/2.1.318/ce473a25ae335659c3fa55f2c2835583/claude-api/python/claude-api/README.md` и `tool-use.md` (structured outputs через `client.messages.parse(..., output_format=Model)`, server tools `web_search_20260209`/`web_fetch_20260209`, vision base64, `thinking={"type":"adaptive"}`, `output_config={"effort": ...}`, без `temperature`/`top_p`, без prefill, без forced `tool_choice`). Если папка скилла недоступна — опираться на формулировки спеки §5.
- **Спецификация** — `.autopilot/2026-11-08-instagram-ads-agent--wip/spec.md`; словарь терминов — там же, раздел «Словарь».

## Границы, решённые в спецификации

| Модуль | Владеет | Выставляет | Прячет |
|---|---|---|---|
| `config` | настройки из `.env` | `Settings` (pydantic), `load_settings()` | разбор `.env`, значения по умолчанию |
| `logging_setup` | логирование | `setup_logging(level)` | фильтр маскировки секретов |
| `money` | пересчёт сумм | `Money(amount_minor:int, currency:str)`, `offset(currency)->int`, `from_major(Decimal, currency)->Money`, `Money.to_major()->Decimal`, `format_ru(Money)->str`, `parse_amount_ru(text)->(Decimal, currency|None)` | таблица валют |
| `clock` | время | `Clock` (протокол `now()->datetime` aware), `RealClock`, `SimClock(store)` с `advance(timedelta)` | хранение смещения |
| `storage` | SQLite | `Database(path)`, репозитории: `companies`, `facts`, `passports`, `media`, `plans`, `launches`, `objects`, `approvals`, `stats`, `history`, `audit`, `jobs`, `settings`, `alerts`, `updates`, `dialogs`, `sim` — методы `get/list/create/update` на dataclass-моделях; `db.transaction()` | SQL, миграции |
| `policy` | деньги и правила (чистые функции) | `daily_cap(...)`, `check_action(action, snapshot)->Decision`, `validate_plan(proposal, ctx)->ValidatedPlan`, `decide(groups, stats, rules, now)->list[Decision]`, `check_facts(text, facts)->list[str]`, `classify_special_category(texts)->Category|None`, `OptimizerRules` | алгоритмы |
| `meta` | Graph API | `MetaGateway` (протокол) и `MetaClient(token, version, transport, validate_only)`: чтение — `list_ad_accounts`, `get_ad_account`, `list_pages`, `get_page_instagram`, `search_interests`, `estimate_audience`, `get_insights`, `get_ad_status`, `find_by_name_tag`, `get_ig_profile`, `get_ig_media`, `business_discovery`, `debug_token`, `get_engagement_audience_size`; изменение — `create_campaign/adset/creative/ad/audience`, `upload_image/video`, `set_status`, `set_daily_budget(Money)`, `set_spend_cap(Money)`, `delete`. `MetaError(kind, user_message_ru, code, subcode, retry_after)` | HTTP, повторы, разбор ошибок, `appsecret_proof` |
| `fakemeta` | фейковый Graph API + симулятор | `FakeGraphAPI(store, clock)` → `.transport` (`httpx.MockTransport`), `.inject_error(match, error)`, `.seed_demo()`; `Simulator(fake, clock).simulate_day(date)` | хранилище, генерация статистики |
| `llm` | вызовы Claude | `LLM` (протокол): `structured(tier, system, user_content, schema, tools=None) -> BaseModel`; `ClaudeLLM(settings)`, `OfflineLLM()`, `ScriptedLLM(responses)` | SDK, повторы, кэш, fallback |
| `agents` | промпты и схемы агентов | `analyze(llm, input)->Passport`, `strategize(...)->MediaPlanProposal`, `write_copy(...)->AdCopySet`, `control(...)->ComplianceVerdict`, `explain(...)`, `report(...)->Report`, `parse_intent(...)->Intent`; модели схем | тексты промптов |
| `executor` | все изменения в Meta | `Executor(db, gateway_factory, policy, clock, notifier)`: `launch(plan_id, approval_id)`, `recover_incomplete()`, `halt_all()`, `resume_system(approval_id)`, `pause_company(id)`, `resume_company(id, approval_id)`, `apply_decisions(company_id, decisions, approval_id=None)`, `apply_creative_fix(ad_id, copy, approval_id)`, `set_monthly_limit(company_id, Money, approval_id)`, `month_rollover(company_id, approval_id)`, `rollback_launch(launch_id)` | сага, реестр, журнал |
| `services` | сценарии | `Onboarding`, `Analysis`, `Planning`, `Optimization`, `Reporting`, `Alerts`, `Moderation`, `ConnectionCheck`, `LevelSwitch`, `DemoTime` — их вызывает бот и планировщик | оркестрация агентов, политики, Исполнителя |
| `scheduler` | расписание | `Scheduler(db, clock, services).tick()`, `run_until(datetime)` (для перемотки) | таблица jobs |
| `notify` | исходящие сообщения | `Notifier` (протокол `send(chat_id|None, text, buttons)->message_id`, `edit`), `TelegramNotifier(bot)` | форматирование, разбиение |
| `bot` | Telegram | `build_dispatcher(app)->Dispatcher` | роутеры, клавиатуры, FSM анкеты |
| `app` | сборка | `App.create(settings, overrides)`, `App.run()`; `python -m target_ai` | связывание зависимостей |

**Швы для тестов** (поведение проверяется только через них):
1. **Главный шов — приложение целиком:** `App.create(settings, overrides={llm: ScriptedLLM|OfflineLLM, meta_transport: FakeGraphAPI.transport, clock: SimClock, bot_session: FakeTelegramSession})` + подача апдейтов `dp.feed_update`. Сквозной тест, денежные тесты (двойное нажатие, сбой, чужой ID, стоп-кран, чужие кампании) — здесь.
2. **Чистые функции `policy` и `money`** — табличные тесты лимитов, темпа, правил, валют, вредных ответов модели.
3. **`MetaClient` на `FakeGraphAPI`** — ошибки Meta и повторы.

## Что построено

_(дописывается после каждого таска: публичные сигнатуры, которые реально существуют в коде, и всё, что отличается от плана выше)_
