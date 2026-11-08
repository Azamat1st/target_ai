from datetime import UTC, datetime


def test_prune_keeps_only_the_newest_update_ids(db):
    now = datetime(2026, 11, 8, tzinfo=UTC)
    for update_id in range(1, 11):
        db.updates.mark(update_id, now)

    removed = db.updates.prune(3)

    assert removed == 7
    assert [i for i in range(1, 11) if db.updates.seen(i)] == [8, 9, 10]
