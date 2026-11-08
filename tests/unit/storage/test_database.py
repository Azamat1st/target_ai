import sqlite3
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta

import pytest

from target_ai.clock import SimClock
from target_ai.money import Money
from target_ai.storage import AlreadyLaunched, Company, Database, Launch, MetaObject, NotFound, Plan

SPEC_TABLES = {
    "settings", "companies", "facts", "passports", "media_assets", "plans", "launches", "launch_steps",
    "meta_objects", "approvals", "stats_daily", "history", "audit_log", "jobs", "alerts_sent",
    "processed_updates", "dialog_state", "sim_state", "fake_meta_store",
}


def table_names(path):
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def test_open_creates_all_spec_tables_and_enables_wal(tmp_path):
    path = tmp_path / "nested" / "app.sqlite3"
    Database(path).close()
    assert SPEC_TABLES <= table_names(path)
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()


def test_reopening_keeps_data_and_does_not_reapply_migrations(tmp_path):
    path = tmp_path / "app.sqlite3"
    first = Database(path)
    first.companies.create(Company(name="Кофейня"))
    first.close()
    second = Database(path)
    assert [c.name for c in second.companies.list()] == ["Кофейня"]
    second.close()


def test_transaction_rolls_back_on_exception(db):
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.companies.create(Company(name="Откат"))
            raise RuntimeError("сбой")
    assert db.companies.list() == []


def test_transaction_commits_on_success(db):
    with db.transaction():
        db.companies.create(Company(name="Ок"))
    assert len(db.companies.list()) == 1


def test_nested_transaction_failure_rolls_back_only_inner(db):
    with db.transaction():
        db.companies.create(Company(name="внешняя"))
        with pytest.raises(RuntimeError):
            with db.transaction():
                db.companies.create(Company(name="внутренняя"))
                raise RuntimeError("сбой")
    assert [c.name for c in db.companies.list()] == ["внешняя"]


def test_models_are_frozen_and_update_returns_copy(db):
    company = db.companies.create(Company(name="A"))
    assert company.id > 0
    with pytest.raises(FrozenInstanceError):
        company.name = "B"  # type: ignore[misc]
    changed = replace(company, name="B")
    db.companies.update(changed)
    assert db.companies.get(company.id).name == "B"
    assert company.name == "A"


def test_update_of_missing_row_raises_not_found(db):
    with pytest.raises(NotFound):
        db.companies.update(Company(id=999, name="нет"))


def test_money_json_and_datetime_round_trip(db, now):
    company = db.companies.create(
        Company(name="A", competitors=["x", "y"], monthly_limit=Money(30000000, "USD"),
                profile={"город": "Алматы"}, created_at=now)
    )
    loaded = db.companies.get(company.id)
    assert loaded == company
    assert loaded.monthly_limit == Money(30000000, "USD")
    assert loaded.created_at == now and loaded.created_at.utcoffset() == timedelta(0)


def test_naive_datetime_is_rejected(db):
    from datetime import datetime

    with pytest.raises(ValueError):
        db.companies.create(Company(name="A", created_at=datetime(2026, 1, 1)))


def test_list_filters_and_unknown_column_is_rejected(db):
    db.companies.create(Company(name="A", status="active"))
    db.companies.create(Company(name="B", status="paused"))
    assert [c.name for c in db.companies.list(status="paused")] == ["B"]
    with pytest.raises(ValueError):
        db.companies.list(**{"name; DROP TABLE companies": "x"})


def test_launch_unique_per_plan_raises_already_launched(db):
    company = db.companies.create(Company(name="A"))
    plan = db.plans.create(Plan(company_id=company.id, version=1))
    db.launches.create(Launch(plan_id=plan.id, company_id=company.id))
    with pytest.raises(AlreadyLaunched):
        db.launches.create(Launch(plan_id=plan.id, company_id=company.id))
    assert len(db.launches.list()) == 1


def test_object_registry_lookup_by_meta_id(db, now):
    company = db.companies.create(Company(name="A"))
    db.objects.create(MetaObject(company_id=company.id, meta_id="123", kind="campaign",
                                 daily_budget=Money(500, "USD"), last_budget_change_at=now))
    found = db.objects.get_by_meta_id("123")
    assert found.daily_budget == Money(500, "USD")
    assert db.objects.get_by_meta_id("nope") is None


def test_sim_offset_survives_new_clock_and_new_connection(tmp_path):
    path = tmp_path / "app.sqlite3"
    first = Database(path)
    SimClock(first.sim).advance(timedelta(days=7))
    first.close()
    second = Database(path)
    assert second.sim.get_offset() == timedelta(days=7)
    second.close()
