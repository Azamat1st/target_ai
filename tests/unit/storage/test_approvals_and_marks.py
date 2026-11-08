import threading
from datetime import timedelta

from target_ai.storage import Company, DailyStat, Database, DialogState
from target_ai.money import Money


def test_approval_consumed_exactly_once_sequentially(db, now):
    approval = db.approvals.create(kind="launch", now=now, payload={"plan_id": 1})
    first = db.approvals.consume(approval.id, now)
    second = db.approvals.consume(approval.id, now)
    assert first is not None and first.status == "consumed" and first.payload == {"plan_id": 1}
    assert second is None


def test_approval_consumed_exactly_once_across_threads(tmp_path, now):
    database = Database(tmp_path / "t.sqlite3")
    approval = database.approvals.create(kind="launch", now=now)
    results = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        results.append(database.approvals.consume(approval.id, now))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(r is not None for r in results) == 1
    database.close()


def test_expired_approval_is_not_consumed(db, now):
    approval = db.approvals.create(kind="launch", now=now, ttl=timedelta(hours=1))
    assert db.approvals.consume(approval.id, now + timedelta(hours=2)) is None
    assert db.approvals.get(approval.id).status == "pending"


def test_unknown_approval_is_not_consumed(db, now):
    assert db.approvals.consume("нет-такого", now) is None


def test_cancelled_approval_is_not_consumed(db, now):
    approval = db.approvals.create(kind="launch", now=now)
    assert db.approvals.cancel(approval.id) is True
    assert db.approvals.consume(approval.id, now) is None


def test_processed_update_is_marked_once(db, now):
    assert db.updates.mark(42, now) is True
    assert db.updates.mark(42, now) is False


def test_alert_dedup_key(db, now):
    assert db.alerts.mark("spend:1:2026-11-08", now) is True
    assert db.alerts.mark("spend:1:2026-11-08", now) is False
    assert db.alerts.seen("spend:1:2026-11-08") is True


def test_settings_store_json_values(db):
    db.settings.set("halted", True)
    db.settings.set("rules", {"cpa_factor": 1.5})
    assert db.settings.get("halted") is True
    assert db.settings.get("rules") == {"cpa_factor": 1.5}
    assert db.settings.get("missing", "default") == "default"


def test_stats_upsert_replaces_same_day_row(db):
    company_id = db.companies.create(Company(name="A")).id
    db.stats.upsert(DailyStat(meta_id="a1", company_id=company_id, date="2026-11-07", spend=Money(100, "USD"), clicks=1))
    db.stats.upsert(DailyStat(meta_id="a1", company_id=company_id, date="2026-11-07", spend=Money(250, "USD"), clicks=5))
    rows = db.stats.list_range(company_id, "2026-11-01", "2026-11-30")
    assert len(rows) == 1 and rows[0].spend == Money(250, "USD") and rows[0].clicks == 5


def test_jobs_schedule_is_idempotent_and_due_filters_by_time(db, now):
    db.jobs.schedule("stats", now)
    db.jobs.schedule("stats", now + timedelta(hours=3))
    db.jobs.schedule("report", now + timedelta(days=1))
    assert len(db.jobs.list()) == 2
    assert db.jobs.due(now) == []
    assert [j.kind for j in db.jobs.due(now + timedelta(hours=3))] == ["stats"]


def test_dialog_state_is_saved_and_replaced(db, now):
    db.dialogs.save(DialogState(chat_id=7, state="ask_name", data={"step": 1}, updated_at=now))
    db.dialogs.save(DialogState(chat_id=7, state="ask_site", data={"step": 2}, updated_at=now))
    assert db.dialogs.get(7).state == "ask_site"
    db.dialogs.delete(7)
    assert db.dialogs.get(7) is None


def test_audit_log_is_append_only(db, now):
    import pytest
    from target_ai.storage import AuditEntry

    entry = db.audit.create(AuditEntry(actor="owner", action="launch", created_at=now))
    with pytest.raises(NotImplementedError):
        db.audit.update(entry)


def test_approvals_list_applies_filters_and_rejects_unknown(db, now):
    import pytest

    a = db.approvals.create(kind="launch", now=now)
    db.approvals.create(kind="pause", now=now)
    db.approvals.consume(a.id, now)
    assert [x.kind for x in db.approvals.list(kind="pause")] == ["pause"]
    assert [x.id for x in db.approvals.list(status="consumed")] == [a.id]
    with pytest.raises(ValueError):
        db.approvals.list(bogus=1)


def test_three_decimal_currencies_are_unknown():
    import pytest
    from target_ai.money import UnknownCurrency, offset

    for cur in ("KWD", "OMR", "TND", "LYD", "IQD"):
        with pytest.raises(UnknownCurrency):
            offset(cur)
