CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    instagram_handle TEXT,
    website TEXT,
    competitors TEXT NOT NULL DEFAULT '[]',
    ad_account_id TEXT,
    page_id TEXT,
    ig_user_id TEXT,
    currency TEXT,
    timezone TEXT,
    monthly_limit_minor INTEGER,
    monthly_limit_currency TEXT,
    target_cpa_minor INTEGER,
    target_cpa_currency TEXT,
    mode TEXT NOT NULL DEFAULT 'confirm' CHECK (mode IN ('confirm', 'autonomous')),
    status TEXT NOT NULL DEFAULT 'active',
    profile TEXT NOT NULL DEFAULT '{}',
    created_at TEXT
);

CREATE TABLE facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    text TEXT NOT NULL,
    source TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'general',
    created_at TEXT
);
CREATE INDEX idx_facts_company ON facts(company_id);

CREATE TABLE passports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    version INTEGER NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',
    created_at TEXT,
    UNIQUE (company_id, version)
);

CREATE TABLE media_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    kind TEXT NOT NULL,
    source_ref TEXT,
    path TEXT,
    caption TEXT,
    meta_id TEXT,
    data TEXT NOT NULL DEFAULT '{}',
    created_at TEXT
);
CREATE INDEX idx_media_company ON media_assets(company_id);

CREATE TABLE plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    version INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','proposed','approved','cancelled','launched','superseded','validated')),
    data TEXT NOT NULL DEFAULT '{}',
    created_at TEXT,
    updated_at TEXT,
    UNIQUE (company_id, version)
);

CREATE TABLE launches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL REFERENCES plans(id),
    company_id INTEGER NOT NULL REFERENCES companies(id),
    status TEXT NOT NULL DEFAULT 'started',
    error TEXT,
    started_at TEXT,
    finished_at TEXT,
    UNIQUE (plan_id)
);

CREATE TABLE launch_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    launch_id INTEGER NOT NULL REFERENCES launches(id),
    step TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    meta_id TEXT,
    data TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT
);
CREATE INDEX idx_launch_steps_launch ON launch_steps(launch_id);

CREATE TABLE meta_objects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    meta_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    launch_id INTEGER REFERENCES launches(id),
    parent_meta_id TEXT,
    name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'PAUSED',
    daily_budget_minor INTEGER,
    daily_budget_currency TEXT,
    last_budget_change_at TEXT,
    created_at TEXT
);
CREATE INDEX idx_objects_company ON meta_objects(company_id);

CREATE TABLE approvals (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    company_id INTEGER,
    payload TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','consumed','cancelled')),
    created_at TEXT,
    expires_at TEXT NOT NULL,
    consumed_at TEXT
);

CREATE TABLE stats_daily (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meta_id TEXT NOT NULL,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    date TEXT NOT NULL,
    spend_minor INTEGER,
    spend_currency TEXT,
    impressions INTEGER NOT NULL DEFAULT 0,
    reach INTEGER NOT NULL DEFAULT 0,
    frequency REAL NOT NULL DEFAULT 0,
    clicks INTEGER NOT NULL DEFAULT 0,
    results INTEGER NOT NULL DEFAULT 0,
    UNIQUE (meta_id, date)
);
CREATE INDEX idx_stats_company_date ON stats_daily(company_id, date);

CREATE TABLE history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    kind TEXT NOT NULL,
    hypothesis TEXT,
    outcome TEXT,
    data TEXT NOT NULL DEFAULT '{}',
    created_at TEXT
);

CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    company_id INTEGER,
    data TEXT NOT NULL DEFAULT '{}',
    created_at TEXT
);

CREATE TABLE jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    company_id INTEGER NOT NULL DEFAULT 0,
    due_at TEXT NOT NULL,
    last_run_at TEXT,
    UNIQUE (kind, company_id)
);

CREATE TABLE alerts_sent (
    key TEXT PRIMARY KEY,
    sent_at TEXT NOT NULL
);

CREATE TABLE processed_updates (
    update_id INTEGER PRIMARY KEY,
    processed_at TEXT NOT NULL
);

CREATE TABLE dialog_state (
    chat_id INTEGER PRIMARY KEY,
    state TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT
);

CREATE TABLE sim_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    offset_us INTEGER NOT NULL DEFAULT 0
);
INSERT INTO sim_state (id, offset_us) VALUES (1, 0);

CREATE TABLE fake_meta_store (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
