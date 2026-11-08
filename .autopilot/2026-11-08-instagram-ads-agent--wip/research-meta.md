# Meta Marketing API + Instagram Graph API: проверенные факты (состояние на 2026-11-08)

Метки: **[Д]** подтверждено документацией (developers.facebook.com, прочитано 2026-11-08) · **[В]** из вторичного источника · **[Н]** не удалось подтвердить · **[Вывод]** мой вывод, не факт.
Ссылки даны сносками `[ключ]`, список в конце файла. Основной корпус документации переехал на `developers.facebook.com/documentation/ads-commerce/...` (старые `/docs/marketing-api/...` частично редиректят). Машиночитаемый индекс: `developers.facebook.com/documentation/ads-commerce/llms.txt`, страницы отдаются как `.md`.

---

## 1. Версии API

- Текущая версия Marketing API — **v27.0**, вышла **4 ноября 2026**, срок «TBD». [Д][mchlog][ver]
- Таблица Marketing API: v26.0 — 29.07.2026 (TBD); v25.0 — 18.02.2026 → **до 23.02.2027**; v24.0 — 08.10.2025 → **до 10.11.2026** (истекает через 2 дня). [Д][mchlog]
- Marketing API выходит примерно раз в 4 месяца. Предыдущая версия живёт **не меньше 90 дней** после выхода новой. Вызовы без версии в Marketing API **падают**. [Д][ver]
- С мая 2024 работает auto-upgrade: вызов к устаревшей версии повышается до следующей, если эндпоинт в ней не менялся. Признак — заголовок `X-Ad-Api-Version-Warning`. Изменённые эндпоинты (adsets, delivery_estimate и т.п.) просто падают. Отключается в настройках продукта Marketing API. [Д][ver]
- Ошибка **2635** — «You are calling a deprecated version of the Ads API». [Д][deliv]
- Graph API (платформа, не Marketing) по своей таблице: v25 до 29.07.2028, v24 до 18.02.2028, v23 до 08.10.2027, v22 до 20.05.2027, v21 до 21.01.2027. v20 истекла 24.09.2026, v19 — 21.05.2026. Для core API действует гарантия 2 года. [Д][gchlog][ver]
- Что изменилось в последних версиях:
  - **v27**: `lookalike_spec.country` и `location_spec` отклоняются. Для Sales-кампаний с `user_device`/`user_os` по умолчанию включена `targeting_automation.individual_setting.device=1`. Добавлен `phone_otp_verification_type` у лид-форм. [Д][v27]
  - **v26**: удалены poll ads; у `delivery_estimate` удалены `daily_outcomes_curve`, `estimate_dau`, `budget_guardrail` (во всех версиях с 27.10.2026). Удалён плейсмент «Instagram Explore Feed» и Messenger Stories. В HEF-кампаниях `advantage_audience` обязателен. [Д][v26]
  - **v25**: запрещено создавать и менять Advantage+ shopping/app campaigns (во всех версиях с 19.05.2026). [Д][v25]
  - **v22**: `instagram_actor_id` устарел, вместо него `instagram_user_id` (во всех версиях с **09.09.2025**). Instagram-эндпоинты внутри Marketing API устарели, надо перейти на Instagram Platform (во всех версиях с 21.04.2025). [Д][v22]

## 2. Разрешения (permissions)

| Разрешение | Что даёт | Зависимости |
|---|---|---|
| `ads_read` | Ads Insights API, отчёты | нет [Д][perms] |
| `ads_management` | чтение и управление рекламными аккаунтами | `pages_read_engagement`, `pages_show_list` [Д][perms] |
| `business_management` | Business Manager API (активы, claim ad accounts) | `pages_read_engagement`, `pages_show_list` [Д][perms] |
| `pages_show_list` | список Страниц пользователя | нет [Д][perms] |
| `pages_read_engagement` | контент и метаданные Страницы | `pages_show_list` [Д][perms] |
| `pages_manage_ads` | реклама от имени Страницы, в т.ч. click-to-Messenger/IG Direct/WhatsApp | `pages_show_list` [Д][perms] |
| `pages_manage_metadata` | вебхуки Страницы, настройки | `pages_show_list` [Д][perms] |
| `instagram_basic` | профиль и медиа IG | `pages_read_user_content`, `pages_show_list` [Д][perms] |
| `instagram_manage_insights` | инсайты IG и business discovery | `instagram_basic`, `pages_read_engagement`, `pages_show_list` [Д][perms] |
| `leads_retrieval` | чтение лидов | Ads Mgmt access tier, `ads_management`, `ads_read`, `business_management`, `pages_manage_ads`, `pages_read_engagement`, `pages_show_list` [Д][perms] |

Какие разрешения нужны под задачу:
- **Список рекламных аккаунтов** — `GET /me/adaccounts` [Д][ver]; нужен `ads_read` или `ads_management` [Вывод].
- **Список Страниц** — `GET /me/accounts`, разрешение `pages_show_list`. [Д][igstart]
- **Связанные IG-аккаунты**:
  - `GET /{page_id}?fields=instagram_business_account` (`instagram_basic` + `pages_show_list`) [Д][igstart];
  - или `GET /act_{id}/connected_instagram_accounts`, `/{business_id}/instagram_business_accounts`, `/{page_id}/instagram_accounts` (последний — с page token). [Д][posts][pig]
- **Кампания с IG-плейсментами** (на примере click-to-IG): `ads_management`, `pages_manage_ads`, `pages_read_engagement`, `pages_show_list`. Плюс Page access token человека с задачей ADVERTISE на Странице. [Д][ctig]
- **Продвижение существующего IG-поста**: `ads_management` (+ pages_*). Чтобы найти медиа: `instagram_basic` + `pages_read_engagement`; если роль на Странице выдана через Business Manager — ещё `ads_management` или `ads_read`. [Д][igm][posts]
- **Instagram insights**: `instagram_basic`, `instagram_manage_insights`, `pages_read_engagement` (+ `ads_management`/`ads_read` при роли через BM). [Д][igmi]
- **Лид-формы**: создание — `ads_management`, `pages_manage_ads`, `pages_read_engagement`, `pages_show_list` и Page token (ADVERTISE). [Д][lead] Чтение лидов — `leads_retrieval` [Д][perms]. Вебхук leadgen требует `pages_manage_metadata` [Вывод из описания разрешения].
- **Срок доступа к данным.** 90-дневный Data Access Expiration **не распространяется** на `ads_read`, `ads_management`, `business_management`. При этом любое разрешение, которым приложение не пользуется 90 дней, может истечь. [Д][dae]

## 3. Уровни доступа, App Review, Business Verification, System User

- **Переименование 05.04.2026.** «Ads Management Standard Access» теперь называется **Marketing API Access Tier**: «Standard» → **Limited access**, «Advanced» → **Full access**. Код менять не нужно. [Д][mchlog][authz]
- **Limited** (по умолчанию, выдаётся при добавлении продукта Marketing API):
  - сильный rate limit, «для разработки, не для production с живыми рекламодателями»;
  - неограниченное число ad accounts (вызовы делают админы и разработчики приложения от имени админов аккаунта);
  - нет доступа к Business Manager API для управления аккаунтами, правами и Страницами;
  - 1 system user + 1 admin system user. [Д][authz]
- **Full**: лёгкий rate limit, полный BM API, 10 system users + 1 admin. [Д][authz]
- **Как получить Full**: ≥ **500** вызовов Marketing API за 15 дней и ошибок < **15%** на последних 500 вызовах, затем кнопка «+Upgrade» в App Review. Скринкаст больше не нужен. В changelog порог назван «auto-approval threshold», т.е. одобрение автоматическое. [Д][authz][mchlog]
- **Уровни доступа к разрешениям (отдельная ось).**
  - Standard: разрешения можно запросить только у пользователей с ролью в приложении. Выдаётся автоматически.
  - Advanced: требует App Review по каждому разрешению и **Business Verification**. [Д][lvl][bv]
  - Цитата: «If your app is only managing your ad account, standard access to `ads_read` and `ads_management` are sufficient». Для чужих аккаунтов нужен Advanced. [Д][authz]
  - Если приложением пользуются только люди с ролью в нём, Business Verification не нужна. [Д][bv]
- **Rate limit Limited vs Full** (уровень ad account):
  - баллы: чтение = 1, запись = 3;
  - максимум баллов — 60 (Limited) против 9000 (Full); decay 300 с;
  - блокировка при достижении максимума — 300 с (Limited) против 60 с (Full);
  - ошибки: 17/2446079, 613/1487742. [Д][rl]
- **BUC-квоты в час на ad account** [Д][rl]:
  - `ads_management`: 300 (dev) или 100 000 (Full) + 40 × число активных объявлений;
  - `ads_insights`: 600 или 190 000 + 400 × активные объявления − 0.001 × ошибки пользователя;
  - `custom_audience`: 5 000 (dev) или 190 000 (Full) + 40 × активные аудитории, потолок 700 000.
- **Прочие лимиты** [Д][rl]:
  - мутации (create/edit кампаний, групп, объявлений): 100 QPS на пару app+ad account, ошибка 613/5044001;
  - правки одного объекта: не чаще 1 раза в 30 с, ошибка 613/4841018;
  - бюджет ad set: не больше 4 изменений в час, ошибка 613/1487632;
  - `spend_cap` аккаунта: не больше 10 изменений в сутки, ошибка 17/1885172.
- **System User** [Д][tok][su][authz]:
  - Создание: Business settings → System Users → «+Add», роль Admin или Employee. Затем «Assign assets» → приложение с правом «Manage app».
  - Токен через API: `POST /{system-user-id}/access_tokens` с параметрами `business_app`, `scope`, `appsecret_proof`.
  - Без флага токен **не истекает**. С `set_token_expires_in_60_days=true` живёт 60 дней; обновление — `GET /oauth/access_token?grant_type=fb_exchange_token&set_token_expires_in_60_days=true…`. Отзыв — `GET /oauth/revoke`.
  - Условия: system user и приложение в одном BM; у приложения должен быть Standard или выше доступ к Ads Management API.
- По документации токены приложений со Standard access к Marketing API и токены system users **не истекают по времени**, но могут быть инвалидированы по другим причинам. [Д][tok][authn]
- **Сроки**:
  - App Review — официального SLA нет; по вторичным данным ~3–7 рабочих дней (встречается «около 5 дней»). [В][bundle][unified]
  - Business Verification — «несколько рабочих дней». [В][unified]

## 4. Цели ODAX: настройка под каждый сценарий

Общее: в кампании `special_ad_categories` обязателен (`[]` или `NONE`). [Д][sac] Раньше (≤v16) objective назывались `LINK_CLICKS`, `MESSAGES` и т.п.; с v17 они устарели. [Д][camp]

| Сценарий | objective | optimization_goal | billing_event | destination_type | promoted_object |
|---|---|---|---|---|---|
| (а) Сообщения в IG Direct | `OUTCOME_ENGAGEMENT` (также `OUTCOME_SALES`, `OUTCOME_TRAFFIC`) | `CONVERSATIONS` (для Engagement также `LINK_CLICKS`) | `IMPRESSIONS` (обязательно) | `INSTAGRAM_DIRECT` | `page_id` обязателен [Д][ctig] |
| (б) Лид-форма | `OUTCOME_LEADS` | `LEAD_GENERATION` или `QUALITY_LEAD` | `IMPRESSIONS` | `ON_AD` (также есть `LEAD_FROM_IG_DIRECT`) | `page_id` [Д][lead][camp] |
| (в) Трафик на сайт | `OUTCOME_TRAFFIC` | `LINK_CLICKS` или `LANDING_PAGE_VIEWS` (также `REACH`/`IMPRESSIONS`) | `IMPRESSIONS`; для `LINK_CLICKS` допустим и `LINK_CLICKS` | в таблице ODAX «—»; в enum есть `WEBSITE` | — [Д][camp][bill][adset] |
| (г) Трафик в IG-профиль | `OUTCOME_TRAFFIC` или `OUTCOME_ENGAGEMENT` | `PROFILE_VISIT` (Traffic) или `PROFILE_AND_PAGE_ENGAGEMENT` (Engagement) | `IMPRESSIONS` (обязательно) | `INSTAGRAM_PROFILE` | `page_id` Страницы, связанной с IG business account [Д][pv] |
| (д) Охват | `OUTCOME_AWARENESS` | `REACH` (также `IMPRESSIONS`, `AD_RECALL_LIFT`) | `IMPRESSIONS` | — | `page_id` [Д][camp][bid] |

Детали по сценариям:
- **(а) IG Direct.**
  - Креатив: `object_story_spec` с `page_id` и IG-аккаунтом, `call_to_action: {type: "INSTAGRAM_MESSAGE", value: {app_destination: "INSTAGRAM_DIRECT"}}`. Приветствие задаётся в `page_welcome_message` (по умолчанию «Hello! Can I get more info on this?»). [Д][ctig]
  - **Противоречие в документации:** в примерах креатива на странице click-to-IG всё ещё стоит `instagram_actor_id`. По changelog v22 он устарел во всех версиях с 09.09.2025, поэтому использовать `instagram_user_id`. [Д][ctig][v22]
- **(б) Лид-форма.**
  - `POST /{page_id}/leadgen_forms` с Page token. Обязательны `name` и `questions[]`, у каждого вопроса обязателен `type`: FULL_NAME, EMAIL, PHONE, CUSTOM и др.; для CUSTOM нужны `key` и `label`, опционально `options`. [Д][lgf][lead]
  - `privacy_policy` — объект `{url, link_text}`, `link_text` ≤ 70 символов. В схеме API он не помечен обязательным; без него форма не публикуется — это только вторичный источник. [Д][lgf] / [В][adsup]
  - Креатив: `call_to_action: {type: "SIGN_UP", value: {lead_gen_form_id}}`. [Д][lead]
  - С v27 есть `phone_otp_verification_type`: `NOT_ENABLED`, `OTP_REQUIRED`, `OTP_OPTIONAL`. [Д][v27]
- **(г) Профиль.**
  - **Создание через API в limited availability**, раскатывается постепенно. [Д][pv]
  - Special ad categories не поддерживаются (только `[]`/`NONE`). [Д][pv]
  - Креатив: `object_story_spec.instagram_user_id`, CTA `VIEW_INSTAGRAM_PROFILE`. Можно собрать из существующего поста: `object_id` + `instagram_user_id` + `source_instagram_media_id`. [Д][pv]
- **(д) Охват.** Meta объединила оптимизации Reach и Impressions: при выборе REACH в API читается `optimization_goal=IMPRESSIONS` вместе с frequency control. [Д][bid]

## 5. Плейсменты только Instagram

- В ad set: `targeting.publisher_platforms=["instagram"]`, `instagram_positions` ⊂ {`stream`, `story`, `explore`, `explore_home`, `reels`, `profile_feed`, `ig_search`, `profile_reels`}. Если поле не задано, берутся все IG-позиции. [Д][place][igads]
- Правила сочетаний [Д][igads][place]:
  - `explore` требует `stream`;
  - `explore_home` требует `stream` + `explore`;
  - `ig_search` требует `stream`;
  - карусель — только в `stream`, `story`, `ig_search`;
  - `publisher_platforms=["instagram"]` + `device_platforms=["desktop"]` → ошибка.
- Видео в `ig_search` раскатывается постепенно, ошибка 100/5234001. [Д][err][igads]
- **Противоречия в документации**:
  - В таблице placement-targeting написано «If provided, `publisher_platforms` must include `facebook`», но гайд по Instagram-рекламе прямо показывает `["instagram"]` как единственное значение. [Д, противоречие][place][igads]
  - v26 «удалил Instagram Explore Feed placement», а значение `explore` в списке позиций осталось. Какое именно значение удалено — **[Н]**. [v26]
- **Advantage+ placements — поведение по умолчанию в API**: если плейсменты не заданы, доставка идёт везде. Значит, IG-only всегда задаётся явно. У такой кампании `advantage_state_info.advantage_state=DISABLED` — это ожидаемо, не ошибка. [Д][advc]
- **`placement_soft_opt_out`** позволяет отдать до 5% расхода на исключённые плейсменты. Работает только если явно передать поле — для IG-only его **не передавать**. [Д][place]
- Совместимость objective ↔ позиция в IG-гайде дана в старых названиях: в Reels нет `LEAD_GENERATION` и `POST_ENGAGEMENT`, в `profile_feed` нет `LEAD_GENERATION`, в Stream и Story есть `LEAD_GENERATION` и `MESSAGES`. Таблица может быть устаревшей. [Д][igads]
- Явной несовместимости Advantage+ audience с IG-only плейсментами в документации нет. **[Н]** — прямого подтверждения тоже нет.

## 6. Advantage+ audience

- Включается полем `targeting.targeting_automation.advantage_audience`: `1` — вкл, `0` — выкл. [Д][aa]
- **С v23.0 при создании ad set** поле либо по умолчанию `1` (если таргетинг дефолтный или «relaxed»), либо **обязательно явно**. Если возраст, пол, custom audiences или детальный таргетинг не дефолтные и без relaxation, создание без флага **падает**. На обновление это не влияет. [Д][aa]
- Когда включено [Д][aa]:
  - `age_min` можно только 18–25, `age_max` зафиксирован на 65;
  - `age_range` передаётся как подсказка;
  - жёсткими остаются гео, минимальный возраст, язык, исключения custom audiences — они не расширяются.
- HEF (housing/employment/financial): `advantage_audience` указывать явно обязательно (v26). [Д][v26]
- Для статуса Advantage+ campaign нужны одновременно Advantage+ audience, бюджет на уровне кампании и Advantage+ placements. [Д][advc]

## 7. Поиск интересов

- `GET /search?type=adinterest&q=…` → `id`, `name`, `path`, `locale`. [Д][ts]
- `type=adinterestsuggestion&interest_list=[…]` → `id`, `name`, `audience_size`, `path`, `description` (поля по примеру ответа). [Д][ts]
- `type=adinterestvalid` с `interest_list` или `interest_fbid_list` — валидация. Интересы переименовываются, поэтому валидировать **по id**. [Д][ts]
- `type=adTargetingCategory&class=interests|behaviors|demographics|life_events|…` → `id`, `name`, `audience_size_lower_bound`, `audience_size_upper_bound`, `path`, `description`, `type`. [Д][ts]
- `type=targetingoptionstatus&targeting_option_list=[…]` → `current_status` (`NORMAL`, `DEPRECATING`, `NON-DELIVERABLE`…) и `future_plan`. Ошибка устаревшей категории — 100/1487694. [Д][ts][err]
- Есть общий эндпоинт `GET /act_{id}/targetingsearch`. [Д][ts]
- Возвращает ли `adinterest` поля `audience_size_lower_bound`/`upper_bound`: в таблице полей их нет. **[Н]**

## 8. Оценка размера аудитории, пересечения

- `GET /act_{id}/reachestimate?targeting_spec=…` → `users_lower_bound`, `users_upper_bound`, `estimate_ready`. Для custom audiences может вернуть `-1` — это не пустая аудитория. [Д][reach]
- `GET /act_{id}/delivery_estimate?targeting_spec=…&optimization_goal=…&promoted_object=…` [Д][deliv]:
  - `targeting_spec` обязателен;
  - не работает для неактивных lookalike;
  - с v26 удалены `daily_outcomes_curve`, `estimate_dau`, `budget_guardrail` (во всех версиях с 27.10.2026) [Д][v26];
  - остаются `estimate_mau_lower_bound`, `estimate_mau_upper_bound`, `estimate_ready` — по SDK. [В][sdk-de]
- **Пересечение аудиторий (audience overlap) через Marketing API — эндпоинта не нашёл.** В документации и в индексе llms.txt его нет. Есть только UI-инструмент в Ads Manager. **[Н]**

## 9. Ретаргетинг на IG-аудиторию и Lookalike

- **Engagement CA из IG-профиля**: `POST /act_{id}/customaudiences` с параметрами `name`, `prefill=1` и `rule` [Д][eca]:
  ```
  {"inclusions":{"operator":"or","rules":[{"event_sources":[{"id":"<IG_BUSINESS_ID>","type":"ig_business"}],
    "retention_seconds":<N>,"filter":{"operator":"and","filters":[{"field":"event","operator":"eq","value":"ig_business_profile_all"}]}}]}}
  ```
  - `subtype` для engagement CA **не передаётся** (не поддерживается с 2018, кроме видео). [Д][eca]
  - События: `ig_business_profile_all`, `ig_business_profile_engaged`, `ig_user_messaged_business`, `ig_business_profile_visit`, `ig_business_profile_ad_saved`, а также `ig_ad_like/comment/share/save/cta_click/carousel_swipe` и `ig_organic_like/comment/share/save/swipe/carousel_swipe`. [Д][eca]
  - Максимальная давность для IG-профиля — **730 дней**. Не больше 500 engagement CA на аккаунт. [Д][eca]
  - Нужно принять Custom Audience ToS, иначе ошибки 200/1870034, 1870090 или 1870092. [Д][eca][err]
- Минимальный размер CA для показа — 100 человек. [В][loomer-lal] В документации API порог для доставки **[Н]**. Удалить пользователей так, чтобы аудитория стала слишком маленькой, нельзя — ошибка 200/1870047. [Д][err]
- **Lookalike** [Д][lal]:
  - `POST /act_{id}/customaudiences` с параметрами `subtype=LOOKALIKE`, `origin_audience_id`, `lookalike_spec={"ratio":0.01..0.20 | "type":"similarity|reach", "starting_ratio", "allow_international_seeds"}`;
  - в источнике **не меньше 100 человек**;
  - с v27 `country`/`location_spec` **отклоняются** (в v26 игнорировались); lookalike доставляется в гео ad set'а;
  - наполняется 1–6 часов, обновляется раз в 3 дня;
  - `operation_status=471` — аудиторию пометили по integrity (с 02.09.2025);
  - для HEF lookalike недоступен. [Д][sac]
- Правило «100 человек из одной страны» прежде было в Help Center, по вторичным данным отменено. [В][loomer-lal]

## 10. Минимальные бюджеты и перерасход

- **Минимум дневного бюджета ad set** (USD, «иллюстративно», при `LOWEST_COST_WITHOUT_CAP`) [Д][adset]:
  - показы — $0.50;
  - клики, лайки, просмотры видео — $2.50;
  - низкочастотные действия — $40;
  - **×2** для аккаунтов из AU, AT, BE, CA, DK, FI, FR, DE, GR, HK, IL, IT, JP, NL, NZ, NO, SG, KR, ES, SE, CH, TW, UK, US.
  - С bid cap: показы ≥ `bid_amount`, клики и действия ≥ 5 × `bid_amount`.
  - Ограничение распространяется и на lifetime-бюджет (минимум × число дней).
- **Точные значения берутся из API**: `GET /act_{id}/minimum_budgets` (опционально `bid_amount`) → `min_daily_budget_imp`, `min_daily_budget_high_freq`, `min_daily_budget_low_freq`, `min_daily_budget_video_views`, `currency`. [Д][minb] У ad account также есть поля `min_daily_budget` и `min_campaign_group_spend_cap`. [Д][acct]
- Ошибки слишком низкого бюджета: 1885272, 1885650 («…обновление бюджета может занять до 15 мин»); пример 1487901 «It must be at least $1.00 per day» с `blame_field_specs: [["daily_budget"]]`. [Д][err]
- `daily_budget` допустим только при длительности > 24 ч. Понизить бюджет можно не ниже чем до потраченного + 10%. [Д][adset]
- **spend_cap кампании** [Д][camp][err]:
  - целое число в минимальных единицах, **минимум $100** или эквивалент;
  - снять ограничение — передать `922337203685478`;
  - ошибка 2446307 — «spend cap is less than minimum».
- **spend_cap аккаунта** [Д][acct]:
  - `0` — без лимита; новый лимит действует только на расход после установки;
  - `spend_cap_action=reset` обнуляет `amount_spent`, `delete` снимает лимит.
  - **Расхождение в документации:** на чтение поле в минимальных единицах («cents for USD»), а в описании записи — «standard denomination, e.g. 23.50». Проверить на sandbox или через `validate_only` — **[Н]**.
- **Перерасход дневного бюджета**:
  - Dev-документация: «up to **25%** more». [Д][budg]
  - Help Center (по цитатам): до **75%** в отдельный день, за неделю не больше **7×** дневного бюджета, раскатывается постепенно; при кампании короче 7 дней расход ≤ дневной бюджет × число дней. [В][loomer]

## 11. Денежные единицы и поля аккаунта

- `daily_budget`, `lifetime_budget`, `bid_amount`, `spend_cap` (кампании) задаются в **минимальных единицах валюты** с учётом offset из официальной таблицы. [Д][budg][cur]
- **Offset = 1** (дробной части нет): CLP, COP, CRC, HUF, ISK, IDR, JPY, KRW, PYG, TWD, VND. У остальных из таблицы offset = 100 (USD, EUR, RUB, TRY, AED…). [Д][cur]
- **KZT и UZS в таблице поддерживаемых валют нет.** Список совпадает с валютами ad accounts. [Д][cur] Аккаунты клиентов из KZ/UZ, значит, будут в USD/EUR/RUB и т.п. [Вывод]
- Поля ad account [Д][acct]:
  - `currency`, `timezone_name`, `timezone_offset_hours_utc`;
  - `amount_spent` — относительно `spend_cap` или всего;
  - `balance` — сумма к оплате;
  - `spend_cap`, `min_daily_budget`, `is_prepay_account`;
  - `funding_source` — без способа оплаты объявления создаются, но **не крутятся**.
- `account_status` [Д][acct]: 1 ACTIVE, 2 DISABLED, 3 UNSETTLED, 7 PENDING_RISK_REVIEW, 8 PENDING_SETTLEMENT, 9 IN_GRACE_PERIOD, 100 PENDING_CLOSURE, 101 CLOSED, 201 ANY_ACTIVE, 202 ANY_CLOSED.
- `disable_reason` [Д][acct]: 0 NONE, 1 ADS_INTEGRITY_POLICY, 2 ADS_IP_REVIEW, 3 RISK_PAYMENT, 4 GRAY_ACCOUNT_SHUT_DOWN, 5 ADS_AFC_REVIEW, 6 BUSINESS_INTEGRITY_RAR, 7 PERMANENT_CLOSE, 8 UNUSED_RESELLER_ACCOUNT, 9 UNUSED_ACCOUNT, 10 UMBRELLA_AD_ACCOUNT, 11 BUSINESS_MANAGER_INTEGRITY_POLICY, 12 MISREPRESENTED_AD_ACCOUNT, 13 AOAB_DESHARE_LEGAL_ENTITY, 14 CTX_THREAD_REVIEW, 15 COMPROMISED_AD_ACCOUNT.

## 12. Проверочные запросы (`execution_options`)

- `POST /act_{id}/campaigns` и `/adsets`: `validate_only`, `include_recommendations` (последний только вместе с validate_only). [Д][camp][adset]
- `POST /act_{id}/ads`: `validate_only`, `include_recommendations`, `synchronous_ad_review`. Последний — только вместе с `validate_only`: запускает проверки Ads Integrity (язык, текст на картинке и т.п.). [Д][ads-edge][ad]
- `POST /act_{id}/adcreatives`: только `validate_only`. [Д][cr-edge]
- Ответ при успехе — `{"success": true}`. Если есть рекомендации — отдельная секция `recommendations`. При провале — ошибка, в `error_data.blame_field_specs` указан виноватый параметр. [Д][camp][err]
- С `validate_only` **не отправлять** ключ идемпотентности: ключ не сохраняется, но сверяется с уже сохранёнными. [Д][idem]

## 13. Продвижение существующего IG-поста, загрузка медиа

- **Креатив из IG-поста (v27)**: `POST /act_{id}/adcreatives` с `object_id=<PAGE_ID>`, `instagram_user_id=<IG_USER_ID>`, `source_instagram_media_id=<MEDIA_ID>`. Опционально `call_to_action` (`INSTAGRAM_MESSAGE`, `VIEW_INSTAGRAM_PROFILE`, `LEARN_MORE`+link…) и `object_type`/`body` (пример для profile visit). [Д][posts][ctig][pv]
- Устарели и заменены [Д][v22]:
  - `instagram_actor_id` → `instagram_user_id`;
  - `instagram_story_id` → `source_instagram_media_id`;
  - `effective_instagram_story_id` → `effective_instagram_media_id`.
  - Передать оба поля одновременно — ошибка 2238055 или 2446149. [Д][err]
- `instagram_user_id` — это IG User ID из Instagram Platform. Старые Marketing-ID доступны в полях `legacy_instagram_user_id` и `legacy_instagram_media_id` (для v21 и ниже). [Д][igu][igm]
- Пригодность поста к продвижению: поле медиа `boost_eligibility_info`; активные объявления на основе поста — `boost_ads_list`. Нельзя продвигать посты с защищённой музыкой или фильтрами и IGTV. [Д][igm][posts]
- Если IG подключён к Странице, в креативе нужен `page_id` **именно этой Страницы**. [Д][pig] Ошибка 1815199 — у рекламного аккаунта нет доступа к IG-аккаунту. [Д][err]
- **Свой креатив**: `object_story_spec` с `page_id` + `instagram_user_id` + `link_data`/`photo_data`/`video_data`. Без Page ID креатив для IG не собрать; на самой IG-рекламе Страница не видна. [Д][igads]
- **Фото**: `POST /act_{id}/adimages` (`bytes` base64 или файл) → `hash`, затем `image_hash` в креативе. Копирование между аккаунтами — `copy_from`. [Д][img]
- **Видео**: `POST /act_{id}/advideos` (`file_url` или chunked upload с `file_size`) → id, затем `video_data.video_id` + превью. [Д][vid]
- Ошибка 3858082: креатив подходит под Standard Enhancements, но не передан `enroll_status` в `degrees_of_freedom_spec`. [Д][err]

## 14. Модерация

- `effective_status` объявления: ACTIVE, PAUSED, DELETED, PENDING_REVIEW, DISAPPROVED, PREAPPROVED, PENDING_BILLING_INFO, CAMPAIGN_PAUSED, ARCHIVED, ADSET_PAUSED, IN_PROCESS, WITH_ISSUES. [Д][ad]
- Ad set: ACTIVE, PAUSED, DELETED, CAMPAIGN_PAUSED, ARCHIVED, IN_PROCESS, WITH_ISSUES. Кампания: ACTIVE, PAUSED, DELETED, ARCHIVED, IN_PROCESS, WITH_ISSUES. [Д][adset][camp]
- Новое объявление сначала в `PENDING_REVIEW`, затем переходит в заданный статус. [Д][ad]
- **Причина отклонения** [Д][ad][rfb][iss]:
  - `ad_review_feedback`: `global` (map «причина → описание» по всем платформам) и `placement_specific` (facebook/instagram);
  - `issues_info[]`: `error_code`, `error_message`, `error_summary`, `error_type` (HARD_ERROR/SOFT_ERROR), `level`, `mid`;
  - также `failed_delivery_checks`.
  - В гайде по вебхукам поле названо `review_feedback`.
- Отклонённое объявление после последней проверки выключено. Чтобы включить, надо внести правки и **создать новое объявление** (ошибки 2490427, 2490468). [Д][err]
- **Вебхук `effective_status`**:
  - подписка `POST /{app_id}/subscriptions` (object=ad_account) + `POST /act_{id}/subscribed_apps`;
  - приходит `field_changed` с `object_id`/`object_type`, **без нового значения** — дальше опрос через `GET /{id}?fields=effective_status,configured_status,review_feedback,…`. [Д][es]
- Если кампания в WITH_ISSUES, PAUSED, ARCHIVED или DELETED, дочерние объекты наследуют статус. Лимит ARCHIVED-объектов — 100 000 на каждый уровень. [Д][status]

## 15. Статистика (Insights)

- `GET /{object}/insights` [Д][ins]:
  - поля: `spend`, `impressions`, `reach`, `frequency`, `clicks`, `inline_link_clicks`, `ctr`, `cpc`, `cpm`, `inline_link_click_ctr`, `actions`, `cost_per_action_type`, `results`, `cost_per_result`;
  - параметры: `level`, `date_preset` (по умолчанию `last_30d`), `time_range`, `time_increment`, `filtering`, `action_attribution_windows`;
  - `use_unified_attribution_setting=true` — «чтобы совпадало с Ads Manager».
- `action_type` [Д][aas]:
  - **сообщения**: `onsite_conversion.messaging_conversation_started_7d` (Messaging Conversations Started), `onsite_conversion.messaging_first_reply` (New Messaging Conversations), `onsite_conversion.messaging_block`;
  - **лиды**: `lead` (все offsite + on-Facebook), `onsite_conversion.lead_grouped` (все On-Facebook), `leadgen_grouped` (Messenger + Instant Forms);
  - **клики**: `link_click`, `landing_page_view`, `outbound_click`;
  - **визиты IG-профиля**: отдельного action_type в справочнике **не нашёл [Н]** — брать `results`/`cost_per_result` [Вывод].
- **Свежесть данных**: «Insights refresh every 15 minutes», через 28 дней данные больше не меняются, после остановки объявления метрики могут дообновляться пару дней. [Д][insbp]
- Удалённые объекты по-прежнему читаются по своему id, но выпадают из `level=`-списков. Удалённое объявление может досчитывать события 28 дней. [Д][status]
- Rate limit Insights: ошибка 4 с subcode 1504022 или 1504039, заголовок `X-FB-Ads-Insights-Throttle` (`app_id_util_pct`, `acc_id_util_pct`). [Д][rl][insbp]

## 16. Instagram Graph API (свой и чужие аккаунты)

- Instagram-эндпоинты внутри Marketing API устарели (во всех версиях с 21.04.2025); использовать Instagram Platform API на `graph.facebook.com` (вариант «with Facebook Login»). [Д][v22]
- **Профиль** `GET /{ig_user_id}?fields=…`: `username`, `name`, `biography`, `website`, `followers_count`, `follows_count`, `media_count`, `profile_picture_url`, `has_profile_pic`, `legacy_instagram_user_id`. Права: `instagram_basic` + `pages_read_engagement` (+`ads_management`/`ads_read` при роли через BM). [Д][igu]
- **Медиа** `GET /{ig_user_id}/media`, поля [Д][igm]:
  - `id`, `caption`, `like_count`, `comments_count`, `media_type` (IMAGE, VIDEO, CAROUSEL_ALBUM), `media_product_type` (AD, FEED, STORY, REELS), `permalink`, `timestamp`, `media_url`, `thumbnail_url`, `boost_eligibility_info`;
  - `like_count` скрыт, если владелец скрыл лайки (при запросе через другой эндпоинт);
  - `media_url` отсутствует у видео с лицензионной музыкой.
- **Инсайты медиа** `GET /{media_id}/insights?metric=…` [Д][igmi]:
  - метрики: `reach`, `views`, `likes`, `comments`, `saved`, `shares`, `total_interactions`, `follows`, `profile_visits`, `profile_activity`, `reposts`, `ig_reels_avg_watch_time`, `ig_reels_video_view_total_time`, `reels_skip_rate`, `navigation`/`replies`/`link_clicks` (stories), `total_likes`/`total_comments`/`total_views` (с учётом рекламы), `facebook_views`, `crossposted_views`;
  - `impressions` устарел для медиа после 02.07.2024; `plays` в списке нет;
  - задержка до 48 ч, хранение 2 года; метрики сторис живут 24 ч, значения меньше 5 → ошибка 10;
  - права: `instagram_basic`, `instagram_manage_insights`, `pages_read_engagement`.
- **Инсайты аккаунта**: `reach`, `views`, `accounts_engaged`, `total_interactions`, `likes`, `comments`, `saves`, `shares`, `follows_and_unfollows`, `profile_links_taps`, `follower_demographics`, `engaged_audience_demographics`. `impressions` устарел (во всех версиях с 21.04.2025). [Д][igui]
- **business_discovery** (чужие business/creator аккаунты) [Д][bd]:
  - запрос: `GET /{МОЙ_ig_user_id}?fields=business_discovery.username(<ник>){followers_count,media_count,media{comments_count,like_count,view_count,…}}`;
  - доступны только «Public»-поля: у профиля `biography`, `followers_count`, `id`, `media_count`, `username`, `website`; у медиа `caption`, `comments_count`, `like_count`, `media_type`, `media_url`, `permalink`, `timestamp`, `view_count`;
  - age-gated аккаунты не возвращаются;
  - права: `instagram_basic`, `instagram_manage_insights`, `pages_read_engagement` (+ads_* при роли через BM).

## 17. Особые категории (Special Ad Categories)

- `special_ad_categories` — обязательный массив, enum: NONE, EMPLOYMENT, HOUSING, CREDIT, ISSUES_ELECTIONS_POLITICS, ONLINE_GAMBLING_AND_GAMING, FINANCIAL_PRODUCTS_SERVICES. [Д][camp]
  - `FINANCIAL_PRODUCTS_SERVICES` заменил `CREDIT` с 14.01.2025. [Д][sac]
  - `ONLINE_GAMBLING_AND_GAMING` есть в enum, но в гайде не описан — **[Н]** какие ограничения у него.
- Если выбрана любая категория, обязателен `special_ad_category_country` (массив ISO-кодов). Для HEF он по умолчанию берётся из налоговой страны. [Д][sac]
- **Ограничения HEF** (для бизнесов из US/CA/EU или с показом туда) [Д][sac]:
  - возраст фиксирован 18–65+ (исключение — кредиты в EU);
  - пол выбрать нельзя;
  - радиус ≥ 15 миль / 25 км (US, CA) и ≥ 15 км (Европа);
  - нельзя исключать локации; недоступны zip, neighborhood, subcity, metro, small_geo_area, electoral_district;
  - нет behaviors и demographics, нет исключений, интересы только из одобренного списка;
  - **нет lookalike** и saved audiences.
  - Бизнесы вне US/CA/EU, которые показывают рекламу вне этих регионов, могут указать NONE.
- **Политика**: нужна авторизация в стране; в креативе `authorization_category` = `POLITICAL` или `POLITICAL_WITH_DIGITALLY_CREATED_MEDIA`. В EU запрещено с 06.10.2025. [Д][sac]
- Для profile visit ads категории не поддерживаются. [Д][pv]
- Отдельно от SAC у ad set есть поля `dsa_beneficiary`/`dsa_payor` (до 512 символов) и `regional_regulated_categories` (TW, AU, SG, TH, MY, BR, IN, US…; ошибки «Beneficiary/payer is missing» 3858634/3858636; у объявления — 3858152). [Д][adset][err] Что DSA-поля обязательны именно при таргетинге на EU — [Вывод] по названию, в прочитанной справке это прямо не сказано.

## 18. Ошибки

- **Формат ответа**: `{"error":{"message","type","code","error_subcode","error_user_title","error_user_msg","fbtrace_id"}}`. [Д][gerr] В Marketing API также приходят `is_transient` (если `true` — повторить позже) [Д][trbl] и `error_data.blame_field_specs` [Д][err]. Обрабатывать **только по кодам**: текст описаний меняется без предупреждения. [Д][err]
- **190** — невалидный токен. Подкоды [Д][gerr][dbg]:
  - 458 — приложение не установлено или доступ не выдан;
  - 459 — checkpoint;
  - 460 — сменён пароль или выход;
  - **463 — истёк**;
  - 464 — неподтверждённый пользователь;
  - **467 — невалиден или отозван**;
  - 492 — у пользователя page token нет роли на Странице.
- **Права**: 10 — разрешение не выдано или отозвано; 200–299 — ошибки разрешений; 294 — нужен `ads_management`. [Д][gerr][err]
- **Rate limit** [Д][rl][grl]:
  - коды: 4 (app; 4/1504022, 4/1504039 — Insights), 17 (user; 17/2446079), 32 (Pages с user token), 613 (subcodes 1487742, 5044001, 4841018, 1487632, 1487225, 1996, без subcode — abuse), 80000 (Insights), 80001 (Pages), 80002 (Instagram), 80003 (Custom Audience), **80004 (Ads Management)**, 80005 (LeadGen), 80014 (Catalog Batch).
  - `X-Business-Use-Case-Usage`: `{biz_id:[{type, call_count, total_cputime, total_time, estimated_time_to_regain_access (минуты), ads_api_access_tier}]}`.
  - `X-App-Usage`: `{call_count, total_time, total_cputime}` в %, с 100 начинается троттлинг.
  - `X-Ad-Account-Usage`: `acc_id_util_pct`, `reset_time_duration` (секунды), `ads_api_access_tier`.
  - **Пауза**: перестать слать запросы до `estimated_time_to_regain_access` или `reset_time_duration` — вызовы во время блокировки продлевают её. Экспоненциальный backoff. Блок по score: 300 с (Limited) или 60 с (Full). Повторная правка того же объекта — через ≥ 30 с.
- **100** — invalid parameter. Подкоды: 1487694 (устаревшая категория таргетинга), 5234001 (видео в IG search), 1349016 (ключ идемпотентности), 33 (нет доступа system user к аудитории). [Д][err][idem]
- **2635** — устаревшая версия Ads API. [Д][deliv]
- **Бюджет и креатив** [Д][err]:
  - 1885272, 1885650 — бюджет слишком мал;
  - 1885621 — бюджет можно задать либо у ad set, либо у кампании;
  - 2446307 — spend cap кампании ниже минимума;
  - 1487007, 1487033 — дата окончания;
  - 2446383 — цель требует URL сайта;
  - 1885557, 2490155 — пост недоступен;
  - 1815199 — нет доступа к IG;
  - 3858082 — нужен enroll_status;
  - 1487990 — слишком много архивных объектов;
  - 3858152 — нужны beneficiary/payer.

## 19. Срок жизни токена

- `GET /debug_token?input_token=<T>&access_token=<app|admin token того же приложения>` → `app_id`, `type`, `application`, `data_access_expires_at`, `expires_at`, `is_valid`, `scopes`, `granular_scopes`, `user_id`; `issued_at` — для long-lived. Значения полей на странице не расшифрованы. [Д][dbg]
- Short-lived токен живёт 1–2 ч, long-lived пользовательский — **около 60 дней** (`expires_in` ≈ 5 183 944 с). Истёкший токен продлить нельзя. Long-lived Page token, полученный из long-lived user token, срока не имеет. [Д][tok][ll]
- Meta **не уведомляет** об истечении токенов. Токен инвалидируется при смене пароля или отзыве прав. [Д][dbg][authn]
- System user token: бессрочный или 60-дневный (раздел 3). [Д][su]

## 20. Идемпотентность

- **Механизм есть (новое):** заголовок **`X-Client-Idempotent-Key`** в create-запросах `POST /act_{id}/campaigns`, `/adsets`, `/ads`, `/adcreatives`. [Д][idem]
  - Формат ключа: 1–128 символов `[A-Za-z0-9_-]`, сравнение точное; рекомендуется UUID v4.
  - Ключ действует в пределах **ad account**, общий для всех эндпоинтов, хранится **7 дней**.
  - Тот же ключ + те же параметры → возвращается исходный результат. Тот же ключ + другие параметры → ошибка 100/1349016.
  - Не работает для update, delete, batch и async; не слать вместе с `validate_only`; не повторять параллельно.
  - **Раскатывается постепенно: пока функция не включена для приложения, заголовок молча игнорируется.**
- Дополнительный механизм — **ad labels** [Д][labels]:
  - метки на кампаниях, группах, объявлениях и креативах;
  - поиск через `/act_{id}/campaignsbylabels`, `/adsetsbylabels`, `/adsbylabels`, `/adcreativesbylabels`, только точное совпадение;
  - до 100 000 меток на аккаунт и 50 на объект.
- Фильтр `filtering` по `name` для `/campaigns`: в таблице параметров v27 его нет (есть `effective_status`, `is_completed`; у adsets — `updated_since`). **[Н]**

---

## Что важно для архитектуры (всё ниже — **[Вывод]**)

1. **Версию держать в конфиге, стартовать с v27.0** (v26 допустима). v24 умирает 10.11.2026, v25 — 23.02.2027, новая версия выходит примерно раз в 4 месяца с окном 90 дней. Логировать `X-Ad-Api-Version-Warning` и ловить код 2635.
2. **Доступ.** Схема «один оператор, клиентские ad accounts расшарены в его бизнес-портфолио, system user в том же BM, что и приложение» укладывается в Standard access без App Review. Лимит Limited tier — 60 баллов за 5 минут на ad account, то есть примерно 20 записей. Поэтому нужен **троттлер на каждый ad account**, который читает `X-Business-Use-Case-Usage`/`X-Ad-Account-Usage`. Апгрейд до Full — через 500 вызовов за 15 дней с ошибками < 15%. Не забивать квоту ошибками: валидировать заранее.
3. **Токен**: system user, бессрочный или 60-дневный с плановым refresh. Проверка «Проверить подключение» строится на `/debug_token` (`is_valid`, `expires_at`, `scopes`) и сверке с нужными разрешениями (раздел 2). Уведомлять при 190/463/467.
4. **IG-only всегда явно**: `publisher_platforms=["instagram"]` + `instagram_positions` с соблюдением зависимостей (explore→stream и т.д.). Никогда не передавать `placement_soft_opt_out`. `advantage_state=DISABLED` — норма. Позиции меняются между версиями (v26), поэтому валидировать через `validate_only`.
5. **`targeting_automation.advantage_audience` всегда передавать явно** (1 для «широкой», 0 для интересов и ретаргетинга). Иначе с v23 создание падает на суженном таргетинге.
6. **Деньги**: хранить целыми в минимальных единицах; offset брать из таблицы и `currency` аккаунта (KZT/UZS не поддерживаются). Минимумы — из `/minimum_budgets`. Поле `spend_cap` аккаунта трогать не нужно (по описанию на запись единицы другие); месячный лимит бота считать самим с запасом на перерасход **до 75% в день и до 7× за неделю**. Учесть лимиты API: 4 изменения бюджета в час на группу, понижение не ниже потраченного + 10%, `spend_cap` кампании ≥ $100 — это возможная страховка.
7. **Двойное нажатие и восстановление после сбоя**:
   - UUID-ключ генерировать и **сохранять в БД до вызова**, слать в `X-Client-Idempotent-Key`;
   - так как заголовок может игнорироваться (раскатка), каждый объект ещё помечать ad label или маркером в `name` вида `bot:<company>:<step>:<uuid>`;
   - перед повторной попыткой сверяться через `*bylabels`.
   - Это же даёт правило «бот трогает только свои кампании».
8. **Проверочные запросы — штатный режим**: `validate_only` для campaigns/adsets/adcreatives, для ads — с `synchronous_ad_review`. Ответ `{"success":true}` или ошибка с `blame_field_specs`, которую стоит переводить в понятный пользователю текст.
9. **Модерация и сигналы**: вебхук `effective_status` требует публичный HTTPS. Без него — опрос не чаще чем раз в N минут. DISAPPROVED → читать `ad_review_feedback` и `issues_info`, исправленный вариант создавать **новым объявлением**. `funding_source` пуст или `account_status≠1` → уведомление «проблема с оплатой / аккаунт ограничен».
10. **Статистика**: опрашивать не чаще раза в 15 минут, всегда с `use_unified_attribution_setting=true`. Результат по цели: Direct → `messaging_conversation_started_7d`; лиды → `onsite_conversion.lead_grouped`/`lead`; сайт → `link_click`/`landing_page_view`; профиль и охват → `results`/`cost_per_result`/`reach`.
11. **Profile visit ads через API в limited availability**: держать за флагом и проверять доступность через `validate_only`. Если недоступно — предупреждать пользователя.
12. **У каждой компании должна быть Facebook-Страница, связанная с IG business account**: `page_id` нужен в `promoted_object` и креативах всех IG-сценариев. В «Проверить подключение» добавить проверку `GET /{page}?fields=instagram_business_account` и доступа ad account к IG (иначе ошибка 1815199).
13. Meta просит AI-агентов **указывать себя в User-Agent** в формате `<AgentName>/<Version> (<Model>) <HTTPClient>`, дописывая к стандартному UA, а не заменяя его. [Д][llms]

## Не подтверждено

- Типичные сроки App Review и Business Verification — только вторичные оценки (3–7 рабочих дней; «несколько дней»). [В][bundle][unified]
- Перерасход «до 75% в день, 7× в неделю» — только цитаты Help Center во вторичных источниках; dev-документация говорит про 25%. [В][loomer]
- Минимальный размер custom audience для показа (100) — только вторичные источники; в документации API нет.
- Возвращает ли `type=adinterest` поля `audience_size_lower_bound`/`upper_bound` (в таблице полей их нет; для adTargetingCategory есть).
- Есть ли API для пересечения аудиторий — не найден.
- Отдельный `action_type` для визитов IG-профиля в Insights — не найден.
- Совместимость Advantage+ audience с IG-only плейсментами — запрета нет, явного подтверждения тоже.
- Какое значение `instagram_positions` удалено в v26 («Instagram Explore Feed»), если `explore` всё ещё в списке.
- Строгая обязательность `privacy_policy` при создании лид-формы через API (в схеме не помечена обязательной).
- Поля ответа `delivery_estimate` на v27 (`estimate_mau_lower_bound`/`upper_bound`, `estimate_ready`) — только по SDK.
- Значение `expires_at=0` в `/debug_token` как «бессрочный» — на странице не описано.
- Единицы `spend_cap` ad account при записи (документация противоречит себе).
- Включён ли `X-Client-Idempotent-Key` для конкретного приложения (идёт раскатка).
- Фильтрация `/campaigns` по `name` через `filtering` в v27.
- Лид-реклама в Reels (в устаревшей таблице IG-гайда `LEAD_GENERATION` для Reels нет).
- Ограничения `ONLINE_GAMBLING_AND_GAMING`.
- Сроки модерации объявлений (часто пишут «большинство за 24 ч») — не проверял.
- Не работали инструменты: Firecrawl (Unauthorized), Perplexity (401). Использованы WebFetch, WebSearch и прямое чтение `.md` с developers.facebook.com.

---

## Источники

[mchlog]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/marketing-api-changelog
[v27]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/marketing-api-changelog/version27.0
[v26]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/marketing-api-changelog/version26.0
[v25]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/marketing-api-changelog/version25.0
[v22]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/marketing-api-changelog/version22.0
[gchlog]: https://developers.facebook.com/docs/graph-api/changelog
[ver]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/overview/versioning
[authz]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/get-started/authorization
[authn]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/get-started/authentication
[rl]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/overview/rate-limiting
[grl]: https://developers.facebook.com/docs/graph-api/overview/rate-limiting
[perms]: https://developers.facebook.com/documentation/development/permissions
[lvl]: https://developers.facebook.com/documentation/development/graph-api/overview/access-levels
[bv]: https://developers.facebook.com/documentation/development/release/business-verification
[tok]: https://developers.facebook.com/documentation/facebook-login/guides/access-tokens
[ll]: https://developers.facebook.com/documentation/facebook-login/guides/access-tokens/get-long-lived
[dbg]: https://developers.facebook.com/docs/facebook-login/access-tokens/debugging-and-error-handling
[dae]: https://developers.facebook.com/docs/facebook-login/auth-vs-data
[su]: https://developers.facebook.com/docs/business-management-apis/system-users/install-apps-and-generate-tokens
[igads]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/guides/instagramads/get-started
[place]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/audiences/reference/placement-targeting
[ctig]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/ad-creative/messaging-ads/click-to-instagram
[pv]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/ad-creative/profile-visit-ads/instagram-profile-visit
[lead]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/guides/lead-ads/create
[lgf]: https://developers.facebook.com/docs/graph-api/reference/page/leadgen_forms/
[camp]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/reference/ad-campaign-group
[adset]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/reference/ad-campaign
[ad]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/reference/adgroup
[ads-edge]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/reference/ad-account/ads
[cr-edge]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/reference/ad-account/adcreatives
[bid]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/bidding/overview
[bill]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/bidding/overview/billing-events
[budg]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/bidding/overview/budgets
[minb]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/reference/ad-account/minimum_budgets
[acct]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/reference/ad-account
[cur]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/currencies
[aa]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/audiences/reference/targeting-expansion/advantage-audience
[advc]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/advantage-campaigns
[ts]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/audiences/reference/targeting-search
[reach]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/reference/ad-account/reachestimate
[deliv]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/reference/ad-account/delivery_estimate
[eca]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/audiences/guides/engagement-custom-audiences
[lal]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/audiences/guides/lookalike-audiences
[sac]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/audiences/special-ad-category
[ins]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/reference/ad-account/insights
[insbp]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/insights/best-practices
[aas]: https://developers.facebook.com/docs/marketing-api/reference/ads-action-stats/
[err]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/error-reference
[gerr]: https://developers.facebook.com/docs/graph-api/guides/error-handling
[trbl]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/troubleshooting
[es]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/ads-webhooks/effective-status
[status]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/best-practices/manage-your-ad-object-status
[rfb]: https://developers.facebook.com/docs/marketing-api/reference/adgroup-review-feedback/
[iss]: https://developers.facebook.com/docs/marketing-api/reference/adgroup-issues-info/
[posts]: https://developers.facebook.com/documentation/ads-commerce/instagram/ads-api/guides/use-posts-as-ads
[pig]: https://developers.facebook.com/documentation/ads-commerce/instagram/ads-api/guides/pages-ig-account
[img]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/reference/ad-account/adimages
[vid]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/reference/ad-account/advideos
[igu]: https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user
[igm]: https://developers.facebook.com/documentation/instagram-platform/reference/instagram-media
[igmi]: https://developers.facebook.com/documentation/instagram-platform/reference/instagram-media/insights
[igui]: https://developers.facebook.com/documentation/instagram-platform/api-reference/instagram-user/insights
[bd]: https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/business_discovery
[igstart]: https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-facebook-login/get-started
[idem]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/idempotent-requests
[labels]: https://developers.facebook.com/documentation/ads-commerce/marketing-api/reference/ad-label
[llms]: https://developers.facebook.com/llms.txt
[loomer]: https://www.jonloomer.com/updates-to-meta-ads-budgeting/
[loomer-lal]: https://www.jonloomer.com/qvt/lookalike-audiences-error/
[adsup]: https://adsuploader.com/blog/facebook-lead-ads-privacy-policy
[sdk-de]: https://cdn.jsdelivr.net/npm/@types/facebook-nodejs-business-sdk@23.0.0/src/objects/ad-account-delivery-estimate.d.ts
[bundle]: https://bundle.social/blog/meta-app-review-20-days
[unified]: https://unified.to/blog/how_to_get_your_meta_ads_api_key
