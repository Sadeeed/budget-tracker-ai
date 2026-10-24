"""Tests for threshold alert rules, duplicate prevention, and durable status."""

from pathlib import Path
import tempfile
import pytest

import database
from notifications import (
    evaluate_threshold_crossings,
    dispatch_alert_sync,
    build_alert_payload,
)


@pytest.fixture
def temp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    database.init_db(db_path)
    yield db_path
    Path(db_path).unlink(missing_ok=True)


def test_crossing_80_percent(temp_db):
    month = "2026-10"
    category = "Dining"
    budget_minor = 1000000  # Rs 10,000

    # Before: Rs 7,000 (70%); After: Rs 8,500 (85%)
    alerts = evaluate_threshold_crossings(
        month=month,
        category=category,
        before_spent_minor=700000,
        after_spent_minor=850000,
        budget_minor=budget_minor,
        db_path=temp_db,
    )
    assert len(alerts) == 1
    assert alerts[0]["threshold"] == 80
    assert alerts[0]["status"] == "pending"

    # Verify persisted in database
    db_alerts = database.get_alerts(month=month, db_path=temp_db)
    assert len(db_alerts) == 1
    assert db_alerts[0]["threshold"] == 80


def test_crossing_100_percent(temp_db):
    month = "2026-10"
    category = "Dining"
    budget_minor = 1000000  # Rs 10,000

    # First cross 80%
    evaluate_threshold_crossings(
        month=month,
        category=category,
        before_spent_minor=700000,
        after_spent_minor=850000,
        budget_minor=budget_minor,
        db_path=temp_db,
    )

    # Next transaction pushes spend from 8,500 to 11,000 (110%)
    alerts2 = evaluate_threshold_crossings(
        month=month,
        category=category,
        before_spent_minor=850000,
        after_spent_minor=1100000,
        budget_minor=budget_minor,
        db_path=temp_db,
    )
    assert len(alerts2) == 1
    assert alerts2[0]["threshold"] == 100

    db_alerts = database.get_alerts(month=month, db_path=temp_db)
    assert len(db_alerts) == 2


def test_single_transaction_crosses_both_80_and_100(temp_db):
    month = "2026-10"
    category = "Groceries"
    budget_minor = 2000000  # Rs 20,000 (80% = 16,000; 100% = 20,000)

    # Jump from Rs 10,000 (50%) to Rs 22,000 (110%)
    alerts = evaluate_threshold_crossings(
        month=month,
        category=category,
        before_spent_minor=1000000,
        after_spent_minor=2200000,
        budget_minor=budget_minor,
        db_path=temp_db,
    )
    assert len(alerts) == 2
    thresholds = {a["threshold"] for a in alerts}
    assert thresholds == {80, 100}


def test_no_duplicate_alert_on_subsequent_transactions(temp_db):
    month = "2026-10"
    category = "Dining"
    budget_minor = 1000000  # Rs 10,000

    # Cross 80% once
    alerts1 = evaluate_threshold_crossings(
        month=month,
        category=category,
        before_spent_minor=700000,
        after_spent_minor=850000,
        budget_minor=budget_minor,
        db_path=temp_db,
    )
    assert len(alerts1) == 1

    # Additional spend within 80%-100% range (85% to 95%)
    alerts2 = evaluate_threshold_crossings(
        month=month,
        category=category,
        before_spent_minor=850000,
        after_spent_minor=950000,
        budget_minor=budget_minor,
        db_path=temp_db,
    )
    assert len(alerts2) == 0

    # Only 1 alert total in DB
    assert len(database.get_alerts(month=month, db_path=temp_db)) == 1


def test_dispatch_alert_success_and_failure(temp_db):
    month = "2026-10"
    category = "Dining"
    budget_minor = 1000000

    alerts = evaluate_threshold_crossings(
        month=month,
        category=category,
        before_spent_minor=700000,
        after_spent_minor=850000,
        budget_minor=budget_minor,
        db_path=temp_db,
    )
    alert = alerts[0]

    # Test failure: publisher raises exception
    def failing_publisher(payload):
        raise ConnectionError("Mock WS failure")

    success = dispatch_alert_sync(alert, failing_publisher, db_path=temp_db)
    assert success is False
    saved = database.get_alert(month, category, 80, db_path=temp_db)
    assert saved["status"] == "failed"

    # Test success: publisher succeeds
    sent_payloads = []

    def successful_publisher(payload):
        sent_payloads.append(payload)

    success = dispatch_alert_sync(alert, successful_publisher, db_path=temp_db)
    assert success is True
    assert len(sent_payloads) == 1
    assert sent_payloads[0]["threshold_percent"] == 80

    saved = database.get_alert(month, category, 80, db_path=temp_db)
    assert saved["status"] == "sent"
    assert saved["sent_at"] is not None
