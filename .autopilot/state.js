window.STATE =
{
  "slug": "instagram-ads-agent",
  "dir": "2026-11-08-instagram-ads-agent--wip",
  "title": "Агентная система таргета Instagram с управлением через Telegram",
  "mode": "full",
  "depth": "normal",
  "polish": null,
  "tier": "T3",
  "briefFile": "2026-11-08-brief.md",
  "memoryFile": "CLAUDE.md",
  "skillDir": "/Users/azamat/.codex/skills/autopilot",
  "startedAt": "2026-11-08T18:32:59+05:00",
  "updatedAt": "2026-11-08T19:10:30+05:00",
  "finishedAt": null,
  "stages": [
    {
      "id": "preflight",
      "status": "done",
      "startedAt": "2026-11-08T18:32:59+05:00",
      "finishedAt": "2026-11-08T18:35:24+05:00"
    },
    {
      "id": "manifest",
      "status": "done",
      "startedAt": "2026-11-08T18:35:10+05:00",
      "finishedAt": "2026-11-08T18:35:24+05:00"
    },
    {
      "id": "briefing",
      "status": "skipped",
      "startedAt": "2026-11-08T18:35:24+05:00",
      "finishedAt": "2026-11-08T18:39:17+05:00",
      "note": "полный автомат — самобрифинг"
    },
    {
      "id": "spec",
      "status": "done",
      "startedAt": "2026-11-08T18:39:17+05:00",
      "finishedAt": "2026-11-08T19:05:43+05:00"
    },
    {
      "id": "plan",
      "status": "done",
      "startedAt": "2026-11-08T19:05:43+05:00",
      "note": "11 тасков, ярус T3, 6 волн",
      "finishedAt": "2026-11-08T19:10:08+05:00"
    },
    {
      "id": "build",
      "status": "active",
      "startedAt": "2026-11-08T19:10:08+05:00",
      "note": "0 из 11 тасков готовы"
    },
    {
      "id": "review",
      "status": "pending"
    },
    {
      "id": "final",
      "status": "pending"
    }
  ],
  "requirements": {
    "total": 107,
    "done": 0,
    "inTicket": 106,
    "inSpec": 0,
    "placeholder": 0,
    "deferred": 1,
    "dropped": 0
  },
  "tickets": [
    {
      "id": "01",
      "title": "Каркас: проект, настройки, деньги, время, база",
      "requirements": [
        "R107i",
        "R46",
        "R64",
        "R65",
        "R84",
        "R85",
        "R87",
        "R92"
      ],
      "blockedBy": [],
      "wave": 1,
      "zone": [
        "src/target_ai/{config,logging_setup,money,clock,storage}/",
        "pyproject.toml",
        "start.sh"
      ],
      "status": "in-progress",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0,
      "startedAt": "2026-11-08T19:10:30+05:00"
    },
    {
      "id": "02",
      "title": "Политика денег и правил оптимизатора",
      "requirements": [
        "R06",
        "R19",
        "R21",
        "R22",
        "R23",
        "R29",
        "R30",
        "R48",
        "R49",
        "R51",
        "R53",
        "R54",
        "R55",
        "R56",
        "R57",
        "R58",
        "R59",
        "R60",
        "R66",
        "R67",
        "R91"
      ],
      "blockedBy": [
        "01"
      ],
      "wave": 2,
      "zone": [
        "src/target_ai/policy/"
      ],
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
    },
    {
      "id": "03",
      "title": "Клиент Meta, фейковый Graph API и симулятор",
      "requirements": [
        "R12",
        "R13",
        "R65",
        "R69",
        "R70",
        "R73",
        "R74",
        "R79",
        "R80",
        "R81",
        "R98"
      ],
      "blockedBy": [
        "01"
      ],
      "wave": 2,
      "zone": [
        "src/target_ai/meta/",
        "src/target_ai/fakemeta/"
      ],
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
    },
    {
      "id": "04",
      "title": "Claude: шлюз моделей и агенты",
      "requirements": [
        "R03",
        "R04",
        "R05",
        "R06",
        "R07",
        "R105i",
        "R106i",
        "R14",
        "R15",
        "R16",
        "R17",
        "R38",
        "R59",
        "R60",
        "R77",
        "R82",
        "R83"
      ],
      "blockedBy": [
        "01"
      ],
      "wave": 2,
      "zone": [
        "src/target_ai/llm/",
        "src/target_ai/agents/"
      ],
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
    },
    {
      "id": "05",
      "title": "Исполнитель: запуск, восстановление, стоп-кран",
      "requirements": [
        "R08",
        "R25",
        "R42",
        "R44",
        "R45",
        "R47",
        "R50",
        "R51",
        "R52",
        "R61",
        "R62",
        "R63",
        "R74",
        "R75",
        "R93",
        "R94",
        "R96",
        "R97"
      ],
      "blockedBy": [
        "02",
        "03"
      ],
      "wave": 3,
      "zone": [
        "src/target_ai/executor/"
      ],
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
    },
    {
      "id": "06",
      "title": "Анкета, анализ, паспорт, медиаплан",
      "requirements": [
        "R01",
        "R09",
        "R10",
        "R105i",
        "R11",
        "R12",
        "R13",
        "R14",
        "R15",
        "R16",
        "R17",
        "R18",
        "R19",
        "R20",
        "R21",
        "R22",
        "R23",
        "R24",
        "R36",
        "R37",
        "R38",
        "R60",
        "R65",
        "R66",
        "R67"
      ],
      "blockedBy": [
        "04",
        "05"
      ],
      "wave": 4,
      "zone": [
        "src/target_ai/services/intake/"
      ],
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
    },
    {
      "id": "07",
      "title": "Статистика, оптимизация, отчёты, уведомления, перемотка",
      "requirements": [
        "R07",
        "R107i",
        "R26",
        "R27",
        "R28",
        "R29",
        "R30",
        "R31",
        "R32",
        "R33",
        "R34",
        "R35",
        "R38",
        "R49",
        "R53",
        "R58",
        "R68",
        "R69",
        "R73",
        "R84"
      ],
      "blockedBy": [
        "04",
        "05"
      ],
      "wave": 4,
      "zone": [
        "src/target_ai/services/ops/",
        "src/target_ai/scheduler/"
      ],
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
    },
    {
      "id": "08",
      "title": "Подключение, уровни работы, настройки, компании",
      "requirements": [
        "R104i",
        "R33",
        "R39",
        "R40",
        "R41",
        "R42",
        "R43",
        "R52",
        "R54",
        "R68",
        "R71",
        "R72",
        "R73",
        "R74",
        "R75"
      ],
      "blockedBy": [
        "04",
        "05"
      ],
      "wave": 4,
      "zone": [
        "src/target_ai/services/admin/"
      ],
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
    },
    {
      "id": "09",
      "title": "Telegram-бот и сборка приложения",
      "requirements": [
        "R02",
        "R09",
        "R24",
        "R35",
        "R37",
        "R39",
        "R40",
        "R41",
        "R42",
        "R43",
        "R44",
        "R45",
        "R46",
        "R61",
        "R62",
        "R63",
        "R71",
        "R76",
        "R77",
        "R78",
        "R85",
        "R95"
      ],
      "blockedBy": [
        "06",
        "07",
        "08"
      ],
      "wave": 5,
      "zone": [
        "src/target_ai/bot/",
        "src/target_ai/notify/",
        "src/target_ai/app/"
      ],
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
    },
    {
      "id": "10",
      "title": "Сквозной демо-цикл и денежные автотесты",
      "requirements": [
        "A01",
        "R103",
        "R89",
        "R90",
        "R91",
        "R92",
        "R93",
        "R94",
        "R95",
        "R96",
        "R97",
        "R98"
      ],
      "blockedBy": [
        "09"
      ],
      "wave": 6,
      "zone": [
        "tests/e2e/",
        "scripts/",
        "docs/demo-transcript.md"
      ],
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
    },
    {
      "id": "11",
      "title": "Docker, README, инструкция для VPS",
      "requirements": [
        "A02",
        "R100",
        "R101",
        "R102",
        "R78",
        "R85",
        "R86",
        "R99"
      ],
      "blockedBy": [
        "09"
      ],
      "wave": 6,
      "zone": [
        "Dockerfile",
        "docker-compose.yml",
        "README.md",
        "docs/"
      ],
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
    }
  ],
  "singlePass": null,
  "tests": null,
  "debt": {
    "placeholders": [],
    "assumptions": [
      "стек Python+aiogram+SQLite",
      "модели opus-5-5 / haiku-5-5",
      "лимит Meta на уровне кампании",
      "увеличение расхода = рост суммы дневных бюджетов",
      "офлайн-демо без ключа Claude",
      "уровень работы глобальный",
      "восстановление запуска по кнопке"
    ],
    "emptyEnv": []
  },
  "additions": [
    "A01 → R89: стенограмма демо-прогона",
    "A02 → R99: разделы README «Деньги и безопасность», «Частые проблемы»"
  ],
  "coverage": {
    "findings": 7,
    "missing": 0,
    "halfCovered": 7,
    "beyondBrief": 27,
    "actions": "7 половинчатых дописаны в спеку (лимит Meta при нескольких кампаниях, частота уведомлений, тон, варианты из постов, правила Контролёра, бюджет=лимит, сроки App Review); 27 «сверх брифа» привязаны к требованиям в §15, новых возможностей 2 (A01, A02)"
  },
  "concerns": [],
  "reviewers": {
    "manifestSpec": null,
    "craft": null
  },
  "blind": null
}
