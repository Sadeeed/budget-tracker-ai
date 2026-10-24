"""Tests for invoice review, linking, paid/unpaid persistence, and double-counting prevention."""

from pathlib import Path
import tempfile
import pytest

import database
from finance import calculate_monthly_stats
from models import TransactionType


@pytest.fixture
def temp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    database.init_db(db_path)
    yield db_path
    Path(db_path).unlink(missing_ok=True)


def test_unpaid_invoice_does_not_affect_spend(temp_db):
    month = "2026-10"

    # Add unpaid invoice of Rs 50,000
    inv_id = database.commit_unpaid_invoice(
        merchant="Furniture Mart",
        invoice_date="2026-10-15",
        total_minor=5000000,
        category="Shopping",
        invoice_number="INV-9901",
        db_path=temp_db,
    )
    assert inv_id > 0

    # Verify stored in invoices table
    invoices = database.get_invoices(month=month, db_path=temp_db)
    assert len(invoices) == 1
    assert invoices[0]["payment_status"] == "unpaid"
    assert invoices[0]["linked_transaction_id"] is None

    # Transactions table MUST be empty!
    txs = database.get_transactions(month=month, db_path=temp_db)
    assert len(txs) == 0

    # Net expenses must be 0
    stats = calculate_monthly_stats(month, db_path=temp_db)
    assert stats.net_expenses_minor == 0


def test_paid_linked_invoice_zero_spend_change(temp_db):
    month = "2026-10"

    # Pre-existing CSV transaction of Rs 12,500 for Supermarket
    tx_ids = database.insert_transactions(
        [
            {
                "occurred_on": "2026-10-05",
                "description": "Metro Supermarket",
                "amount_minor": 1250000,
                "category": "Groceries",
                "type": TransactionType.EXPENSE.value,
                "source": "csv",
            }
        ],
        db_path=temp_db,
    )
    existing_tx_id = tx_ids[0]

    stats_before = calculate_monthly_stats(month, db_path=temp_db)
    assert stats_before.net_expenses_minor == 1250000

    # Reviewing receipt for same purchase and choosing LINK
    inv_id = database.commit_paid_invoice_linked(
        merchant="Metro Supermarket",
        invoice_date="2026-10-05",
        total_minor=1250000,
        category="Groceries",
        linked_transaction_id=existing_tx_id,
        invoice_number="REC-4412",
        db_path=temp_db,
    )

    # Net expenses must remain exactly 12,500 (NO double counting!)
    stats_after = calculate_monthly_stats(month, db_path=temp_db)
    assert stats_after.net_expenses_minor == 1250000

    # Verify mutual linkage
    inv = database.get_invoices(month=month, db_path=temp_db)[0]
    assert inv["linked_transaction_id"] == existing_tx_id

    tx = database.get_transactions(month=month, db_path=temp_db)[0]
    assert tx["invoice_id"] == inv_id


def test_paid_unmatched_invoice_creates_one_expense(temp_db):
    month = "2026-10"

    rev_before = database.get_transaction_revision(month, db_path=temp_db)

    # User uploads and confirms new paid receipt of Rs 7,906
    inv_id, tx_id = database.commit_paid_invoice_as_new_expense(
        merchant="Al-Fatah Store",
        invoice_date="2026-10-26",
        total_minor=790600,
        category="Groceries",
        invoice_number="INV-2026-10-8842",
        db_path=temp_db,
    )
    assert inv_id > 0
    assert tx_id > 0

    # Exactly 1 transaction created with source='invoice'
    txs = database.get_transactions(month=month, db_path=temp_db)
    assert len(txs) == 1
    assert txs[0]["id"] == tx_id
    assert txs[0]["source"] == "invoice"
    assert txs[0]["amount_minor"] == 790600
    assert txs[0]["invoice_id"] == inv_id

    # Spend increases by exactly 7,906
    stats = calculate_monthly_stats(month, db_path=temp_db)
    assert stats.net_expenses_minor == 790600

    # Revision increased (invalidates older reports)
    rev_after = database.get_transaction_revision(month, db_path=temp_db)
    assert rev_after > rev_before


def test_find_potential_duplicate_candidates(temp_db):
    # Insert existing CSV transaction
    database.insert_transactions(
        [
            {
                "occurred_on": "2026-10-10",
                "description": "Electronics Store",
                "amount_minor": 1500000,
                "category": "Shopping",
                "type": TransactionType.EXPENSE.value,
                "source": "csv",
            }
        ],
        db_path=temp_db,
    )

    # Candidate with exact date and amount
    exact_matches = database.find_potential_duplicates(
        date="2026-10-10",
        amount_minor=1500000,
        description="Electronics Store",
        db_path=temp_db,
    )
    assert len(exact_matches) == 1
    assert exact_matches[0]["amount_minor"] == 1500000

    # Candidate with 2 days tolerance
    tolerance_matches = database.find_potential_duplicates(
        date="2026-10-12",
        amount_minor=1500000,
        description="Electronics Mart",
        db_path=temp_db,
    )
    assert len(tolerance_matches) == 1
