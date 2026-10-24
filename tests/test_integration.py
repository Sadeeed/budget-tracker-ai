"""Offline integration proof. Doubles here never count as live SDK evidence."""

import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import config
import database
from ai_service import AIService
from csv_import import commit_csv_import, find_cross_file_duplicates, parse_and_validate_csv
from dnotifier_adapter import CapabilityUnavailable, DNotifierAdapter, InvalidAIOutput, validate_alert
from finance import calculate_monthly_stats
from money import parse_to_minor
from notifications import dispatch_alert_sync, evaluate_threshold_crossings


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "demo.db")
    monkeypatch.setattr(database, "DB_PATH", path)
    monkeypatch.setattr(config, "DB_PATH", path)
    database.init_db()
    return path


def invoice(**overrides):
    return dict(merchant="Synthetic Market", invoice_date="2026-10-12",
                total_minor=85000, category="Groceries", invoice_number="DEMO-1", **overrides)


def import_sample(month):
    content = Path(f"sample_data/expenses_{month}.csv").read_bytes()
    rows, errors = parse_and_validate_csv(content)
    assert not errors
    return commit_csv_import(month + ".csv", content, rows)


def test_report_revision_tracks_budget_and_comparison_edits(db):
    import_sample("2026_09")
    import_sample("2026_10")
    first = database.get_report_revision("2026-10")
    database.set_budget("2026-10", "Groceries", 100000)
    second = database.get_report_revision("2026-10")
    assert first != second
    database.set_budget("2026-10", "Groceries", 100000)
    assert second == database.get_report_revision("2026-10")
    with database.get_connection() as conn:
        conn.execute("UPDATE transactions SET amount_minor = amount_minor + 1 WHERE occurred_on LIKE '2026-09%'")
    assert second != database.get_report_revision("2026-10")
    database.delete_budget("2026-10", "Groceries")
    assert "Groceries" not in database.get_budgets("2026-10")


def test_atomic_csv_provenance_alerts_and_rollback(db, monkeypatch):
    database.set_budget("2026-10", "Dining", 100000)
    content = b"date,description,amount,category,type\n2026-10-01,Cafe,1100,Dining,expense\n"
    rows, _ = parse_and_validate_csv(content)
    original = database._queue_crossings

    def fail(*args):
        original(*args)
        raise RuntimeError("Simulated persistence failure")

    monkeypatch.setattr(database, "_queue_crossings", fail)
    with pytest.raises(RuntimeError):
        commit_csv_import("test.csv", content, rows)
    assert database.get_import_by_hash(hashlib.sha256(content).hexdigest()) is None
    assert not database.get_transactions()
    assert not database.get_alerts()
    monkeypatch.setattr(database, "_queue_crossings", original)
    commit_csv_import("test.csv", content, rows)
    assert {a["threshold"] for a in database.get_alerts()} == {80, 100}
    assert find_cross_file_duplicates(rows) == [1]
    with pytest.raises(ValueError):
        commit_csv_import("renamed.csv", content, rows)


def test_invoice_validation_idempotency_and_atomic_alerts(db, monkeypatch):
    database.set_budget("2026-10", "Groceries", 100000)
    original = database._queue_crossings

    def fail(*args):
        original(*args)
        raise RuntimeError("Simulated persistence failure")

    monkeypatch.setattr(database, "_queue_crossings", fail)
    with pytest.raises(RuntimeError):
        database.commit_paid_invoice_as_new_expense(**invoice(), file_sha256="receipt")
    assert not database.get_invoices() and not database.get_transactions() and not database.get_alerts()
    monkeypatch.setattr(database, "_queue_crossings", original)
    database.commit_paid_invoice_as_new_expense(**invoice(), file_sha256="receipt")
    assert calculate_monthly_stats("2026-10").net_expenses_minor == 85000
    assert len(database.get_alerts()) == 1
    with pytest.raises(ValueError, match="already saved"):
        database.commit_paid_invoice_as_new_expense(**invoice(), file_sha256="receipt")
    invalid = invoice()
    invalid.update(invoice_date="not-a-date", invoice_number="DEMO-2")
    with pytest.raises(ValueError):
        database.commit_unpaid_invoice(**invalid)
    invalid.update(invoice_date="2026-10-13", merchant="")
    with pytest.raises(ValueError):
        database.commit_unpaid_invoice(**invalid)
    assert len(database.get_transactions()) == 1


def test_fractional_threshold_and_async_retry_preserve_snapshot(db):
    assert not evaluate_threshold_crossings("2026-10", "Dining", 0, 80, 101)
    alert = evaluate_threshold_crossings("2026-10", "Dining", 80, 81, 101)[0]
    database.set_budget("2026-10", "Dining", 9999)
    received = []

    async def failed(payload):
        raise InvalidAIOutput("Synthetic failure")

    async def sent(payload):
        received.append(payload)

    async def inside_loop():
        assert not dispatch_alert_sync(alert, failed)
        assert database.get_alerts()[0]["status"] == "failed"
        assert dispatch_alert_sync(database.get_alerts()[0], sent)
        assert dispatch_alert_sync(database.get_alerts()[0], sent)

    asyncio.run(inside_loop())
    assert len(received) == 1 and received[0]["budget_minor"] == 101


@pytest.mark.parametrize("value", ["USD 10", "1e3", "garbage12", "NaN", float("nan"), "1,00"])
def test_invalid_money_is_not_silently_reinterpreted(value):
    with pytest.raises(ValueError):
        parse_to_minor(value)


def test_missing_category_and_bad_iso_date_require_review():
    rows, errors = parse_and_validate_csv(b"date,description,amount,category\n2026-10-01,Store,10,Unknown\n2026-1-1,Store,20,Dining\n")
    assert not rows and len(errors) == 2


class FakeAdapter:
    def __init__(self):
        self.sent = []
        self.failure = False
        self.calls = 0

    async def request_json(self, prompt):
        self.calls += 1
        if self.failure:
            raise InvalidAIOutput("Synthetic invalid output; retry.")
        if "UNTRUSTED_DOCUMENT_JSON" in prompt:
            return dict(merchant="Synthetic Market", invoice_number="DEMO-1", date="2026-10-12",
                        total="850.00", currency="PKR", suggested_category="Groceries",
                        confidence="high", missing_fields=[], evidence_snippet="TOTAL PKR 850.00")
        return dict(summary="Review based on supplied facts.", findings=[], recommendations=[], limitations=[])

    async def publish_alert(self, payload):
        validate_alert(payload)
        self.sent.append(payload)

    async def extract_invoice_image(self, image_bytes, mime_type):
        raise CapabilityUnavailable("Image support unverified; enter fields manually.")


def ui(monkeypatch, uploads=None):
    adapter = FakeAdapter()
    monkeypatch.setattr(DNotifierAdapter, "from_environment", classmethod(lambda cls: adapter))
    monkeypatch.setattr(config, "is_dnotifier_configured", lambda: True)
    monkeypatch.setattr(st, "file_uploader", lambda *args, **kwargs: (uploads or {}).get(kwargs.get("key")))
    return AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py", default_timeout=15), adapter


def click(at, label):
    next(b for b in at.button if b.label == label).click().run()
    assert not at.exception


def test_ui_analysis_contract_origin_failure_and_staleness(db, monkeypatch):
    import_sample("2026_09")
    import_sample("2026_10")
    at, adapter = ui(monkeypatch)
    at.run()
    assert not at.exception
    assert next(t for t in at.text_input if t.label == "Merchant").value == ""
    click(at, "🚀 Analyze Month with DNotifier AI")
    report = database.get_latest_report("2026-10")
    assert report["origin"] == "dnotifier"
    assert len(json.loads(report["stats_json"])["largest_expenses"]) > 0
    assert adapter.calls == 1
    database.set_budget("2026-10", "Dining", 50000)
    at.run()
    assert any("Outdated" in w.value for w in at.warning)
    adapter.failure = True
    click(at, "🚀 Analyze Month with DNotifier AI")
    assert at.error
    assert database.get_latest_report("2026-10")["id"] == report["id"]


def test_ui_pdf_process_review_save_and_real_adapter_boundary_double(db, monkeypatch):
    import pymupdf
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "SYNTHETIC RECEIPT TOTAL PKR 850.00")
        pdf_bytes = document.tobytes()
    uploads = {"invoice_uploader": SimpleNamespace(name="receipt.pdf", getvalue=lambda: pdf_bytes)}
    database.set_budget("2026-10", "Groceries", 100000)
    at, adapter = ui(monkeypatch, uploads)
    at.run()
    click(at, "Process receipt with DNotifier")
    assert next(t for t in at.text_input if t.label == "Merchant").value == "Synthetic Market"
    assert not database.get_invoices() and not database.get_transactions()
    click(at, "Proceed to Match & Save")
    assert not database.get_invoices()
    click(at, "Finalize and Save Receipt")
    assert calculate_monthly_stats("2026-10").net_expenses_minor == 85000
    assert database.get_alerts()[0]["status"] == "sent"
    assert len(adapter.sent) == 1
    at.run()
    assert len(adapter.sent) == 1  # Ordinary reruns do not publish again.
    click(at, "Process receipt with DNotifier")
    click(at, "Proceed to Match & Save")
    click(at, "Finalize and Save Receipt")
    assert at.error and len(database.get_transactions()) == 1


def test_ui_unconfigured_analysis_does_not_save_fallback(db, monkeypatch):
    at, _ = ui(monkeypatch)
    monkeypatch.setattr(config, "is_dnotifier_configured", lambda: False)
    at.run()
    click(at, "🚀 Analyze Month with DNotifier AI")
    assert at.error and database.get_latest_report("2026-10") is None


def test_ui_csv_exact_decimal_and_explicit_duplicate_acknowledgement(db, monkeypatch):
    content = b"date,description,amount,category,type\n2026-10-01,Market,850.01,Groceries,expense\n"
    uploads = {"csv_file_uploader": SimpleNamespace(name="demo.csv", getvalue=lambda: content)}
    database.set_budget("2026-10", "Groceries", 100000)
    at, adapter = ui(monkeypatch, uploads)
    at.run()
    assert not database.get_transactions()
    click(at, "Confirm and Commit Import")
    assert database.get_transactions()[0]["amount_minor"] == 85001
    assert len(adapter.sent) == 1
    at.run()
    assert len(database.get_transactions()) == 1
    assert at.error  # Exact-file repeat is blocked.
    altered = content + b"\n"
    uploads["csv_file_uploader"] = SimpleNamespace(name="different.csv", getvalue=lambda: altered)
    at.run()
    click(at, "Confirm and Commit Import")
    assert at.error and len(database.get_transactions()) == 1
    at.checkbox[0].check().run()
    click(at, "Confirm and Commit Import")
    assert len(database.get_transactions()) == 2  # Explicitly approved real repeat.
    assert {a["threshold"] for a in database.get_alerts()} == {80, 100}


def test_ui_image_failure_and_upload_change_clear_old_review(db, monkeypatch):
    uploads = {"invoice_uploader": SimpleNamespace(name="scan.png", getvalue=lambda: b"synthetic-image")}
    at, _ = ui(monkeypatch, uploads)
    at.run()
    click(at, "Process receipt with DNotifier")
    assert any("unverified" in w.value for w in at.warning)
    assert not database.get_transactions()
    next(t for t in at.text_input if t.label == "Merchant").set_value("Manual Store")
    next(t for t in at.text_input if t.label == "Date (YYYY-MM-DD)").set_value("2026-10-12")
    next(t for t in at.text_input if t.label == "Total Amount (PKR)").set_value("123.45")
    click(at, "Proceed to Match & Save")
    assert any(b.label == "Finalize and Save Receipt" for b in at.button)
    uploads["invoice_uploader"] = SimpleNamespace(name="other.png", getvalue=lambda: b"other-image")
    at.run()
    assert not any(b.label == "Finalize and Save Receipt" for b in at.button)
    assert next(t for t in at.text_input if t.label == "Merchant").value == ""


@pytest.mark.parametrize("status", ["paid", "unpaid"])
def test_ui_linked_and_unpaid_invoices_never_increase_actuals(db, monkeypatch, status):
    rows, _ = parse_and_validate_csv(b"date,description,amount,category\n2026-10-12,Synthetic Market,850,Groceries\n")
    database.insert_transactions(rows)
    at, adapter = ui(monkeypatch)
    at.run()
    for label, value in {"Merchant": "Synthetic Market", "Date (YYYY-MM-DD)": "2026-10-12",
                         "Total Amount (PKR)": "850.00", "Invoice / Receipt Number": "MANUAL-1"}.items():
        next(t for t in at.text_input if t.label == label).set_value(value)
    next(r for r in at.radio if r.label == "Payment Status").set_value(status)
    click(at, "Proceed to Match & Save")
    click(at, "Finalize and Save Receipt")
    assert calculate_monthly_stats("2026-10").net_expenses_minor == 85000
    assert database.get_invoices()[0]["payment_status"] == status
    assert not adapter.sent


def test_existing_schema_migration_retains_and_labels_reports(tmp_path):
    path = str(tmp_path / "legacy.db")
    with sqlite3.connect(path) as conn:
        for ddl in database.DDL_STATEMENTS:
            conn.execute(ddl)
        conn.execute("INSERT INTO reports(month, stats_json, analysis_json, transaction_revision, created_at) VALUES ('2026-10', '{}', '{}', 0, 'old')")
    database.init_db(path)
    database.init_db(path)
    assert database.get_latest_report("2026-10", path)["origin"] == "legacy_unverified"
