"""Opt-in real integration demo in a fresh temporary DB; synthetic data only.

AppTest drives the real Streamlit actions. Only the file chooser is injected;
PDF extraction, AI services, SDK transports, persistence and subscriber are real.
This is headless acceptance evidence, not a browser/manual usability test.
"""

import argparse
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True,
                        help="Opt in to three real AI requests and one expense-triggered WS alert.")
    parser.parse_args()
    os.chdir(ROOT)
    base = "/tmp/opencode" if Path("/tmp/opencode").is_dir() else None
    directory = tempfile.mkdtemp(prefix="budgetpilot-demo-", dir=base)
    os.environ["BUDGETPILOT_DB_PATH"] = str(Path(directory) / "demo.db")

    import database
    import pymupdf
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    from finance import calculate_monthly_stats
    from money import minor_to_decimal
    from notifications import build_alert_payload

    uploads = {}
    subscriber = None
    stage = "fresh startup"

    def click(at, label):
        next(b for b in at.button if b.label == label).click().run()
        assert not at.exception, "Streamlit execution failed."

    try:
        with patch.object(st, "file_uploader", side_effect=lambda *a, **kw: uploads.get(kw.get("key"))):
            at = AppTest.from_file(ROOT / "app.py", default_timeout=100).run()
            assert not at.exception, "Fresh-DB startup failed."
            for month in ("09", "10"):
                stage = f"CSV import {month}"
                content = (ROOT / f"sample_data/expenses_2026_{month}.csv").read_bytes()
                uploads["csv_file_uploader"] = SimpleNamespace(name=f"{month}.csv", getvalue=lambda b=content: b)
                at.run()
                click(at, "Confirm and Commit Import")
                assert len(database.get_transactions(f"2026-{month}")) == (12 if month == "09" else 15), "CSV import failed."
            uploads.pop("csv_file_uploader")
            before = calculate_monthly_stats("2026-10").net_expenses_minor
            database.set_budget("2026-10", "Dining", 1000000)
            at.run()
            stage = "monthly AI review"
            # Explicitly select the synthetic fixture month, not the machine's current month.
            next(s for s in at.selectbox if s.label == "Target Month").set_value("2026-10").run()
            next(t for t in at.text_input if t.label == "Monthly income (Rs)").set_value(str(minor_to_decimal(before + 42500)))
            click(at, "Save monthly income")
            click(at, "🚀 Analyze Month with DNotifier AI")
            report = database.get_latest_report("2026-10")
            assert report and report["origin"] == "dnotifier", "Live monthly analysis did not save a validated report."

            with pymupdf.open() as document:
                page = document.new_page()
                page.insert_text((72, 72), "SYNTHETIC RECEIPT\nMerchant: Synthetic Market\nInvoice: LIVE-DEMO-1\n"
                                 "Date: 2026-10-12\nItems: rice and milk\nCurrency: PKR\nGRAND TOTAL PKR 850.00")
                pdf_bytes = document.tobytes()
            uploads["invoice_uploader"] = SimpleNamespace(name="synthetic.pdf", getvalue=lambda: pdf_bytes)
            at.run()
            stage = "invoice interpretation"
            click(at, "Process receipt with DNotifier")
            values = {t.label: t.value for t in at.text_input}
            assert values["Merchant"] == "Synthetic Market" and values["Total Amount (PKR)"] == "850.00", "Live invoice interpretation mismatched source."
            assert values["Date (YYYY-MM-DD)"] == "2026-10-12" and values["Currency (PKR only)"] == "PKR"
            assert not database.get_invoices(), "Invoice was saved before explicit confirmation."
            click(at, "Proceed to Match & Save")
            assert not database.get_invoices()

            subscriber = subprocess.Popen(
                [sys.executable, str(ROOT / "watch_alerts.py"), "--once", "--timeout", "75"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            with selectors.DefaultSelector() as selector:
                stage = "subscriber readiness"
                selector.register(subscriber.stdout, selectors.EVENT_READ)
                assert selector.select(timeout=45), "Separate subscriber did not become ready."
                ready = json.loads(subscriber.stdout.readline())
            assert ready.get("status") == "ready", "Separate subscriber did not authenticate."
            stage = "invoice confirmation and alert"
            click(at, "Finalize and Save Receipt")
            assert not at.error, "Invoice confirmation failed."
            assert calculate_monthly_stats("2026-10").net_expenses_minor == before + 85000
            assert report["transaction_revision"] != database.get_report_revision("2026-10")
            alerts = database.get_alerts()
            assert len(alerts) == 1 and alerts[0]["status"] == "sent", "Durable expense alert was not sent."
            assert alerts[0]["category"] == "__overall__", "Expected an overall monthly-income overrun alert."
            output, _ = subscriber.communicate(timeout=85)
            stage = "subscriber delivery verification"
            received = [json.loads(line) for line in output.splitlines() if line]
            expected = build_alert_payload(alerts[0])
            assert subscriber.returncode == 0 and any(
                event.get("status") == "received" and all(event["alert"].get(k) == v for k, v in expected.items())
                for event in received
            ), "Separate subscriber receipt did not match the persisted alert."
            stage = "updated monthly AI review"
            click(at, "🚀 Analyze Month with DNotifier AI")
            latest = database.get_latest_report("2026-10")
            assert latest["id"] != report["id"] and latest["transaction_revision"] == database.get_report_revision("2026-10")
        print(json.dumps({"verified": True, "synthetic": True, "headless_ui": True,
                          "file_chooser_injected_only": True, "real_ai_requests": 3,
                          "two_csvs_imported": True, "paid_invoice_delta_minor": 85000,
                          "separate_subscriber_received": True, "updated_report_current": True,
                          "overall_income_overrun_alert": True,
                          "image_support": "blocked/unverified", "database": os.environ["BUDGETPILOT_DB_PATH"]}))
        return 0
    except Exception as exc:
        # Do not print SDK errors, credentials or raw receipt data.
        print(json.dumps({"verified": False, "failure_type": type(exc).__name__,
                          "stage": stage,
                          "check": str(exc) if isinstance(exc, AssertionError) else "Integration operation failed.",
                          "database": os.environ["BUDGETPILOT_DB_PATH"]}))
        return 1
    finally:
        if subscriber is not None and subscriber.poll() is None:
            subscriber.terminate()
            try:
                subscriber.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                subscriber.kill()
                subscriber.communicate()


if __name__ == "__main__":
    raise SystemExit(main())
