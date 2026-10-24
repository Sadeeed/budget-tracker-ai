"""Tests for CSV import, validation, error handling, and idempotency."""

from pathlib import Path
import pytest
import sqlite3
import tempfile

import database
from csv_import import (
    parse_and_validate_csv,
    check_csv_import_status,
    commit_csv_import,
    CSVValidationError,
)
from models import TransactionType


@pytest.fixture
def temp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    database.init_db(db_path)
    yield db_path
    Path(db_path).unlink(missing_ok=True)


def test_parse_valid_csv():
    csv_content = b"""date,description,amount,category,type
2026-10-01,Apartment Rent,35000,Housing,expense
2026-10-03,Supermarket,12500,Groceries,expense
2026-10-09,Salary,180000,Income,income
2026-10-15,Store Refund,2500,Shopping,refund
2026-10-20,Savings Transfer,10000,Other,transfer
"""
    valid_rows, errors = parse_and_validate_csv(csv_content)
    assert len(errors) == 0
    assert len(valid_rows) == 5

    # Check first row
    r0 = valid_rows[0]
    assert r0["occurred_on"] == "2026-10-01"
    assert r0["description"] == "Apartment Rent"
    assert r0["amount_minor"] == 3500000  # 35,000 * 100
    assert r0["category"] == "Housing"
    assert r0["type"] == TransactionType.EXPENSE.value
    assert r0["source_row"] == 1


def test_reject_negative_amount():
    csv_content = b"""date,description,amount,category,type
2026-10-01,Invalid Negative,-500,Shopping,expense
"""
    valid_rows, errors = parse_and_validate_csv(csv_content)
    assert len(valid_rows) == 0
    assert len(errors) == 1
    assert "Negative amount" in errors[0]["error_message"]


def test_reject_invalid_date():
    csv_content = b"""date,description,amount,category,type
10/01/2026,Bad Date,1200,Dining,expense
"""
    valid_rows, errors = parse_and_validate_csv(csv_content)
    assert len(valid_rows) == 0
    assert len(errors) == 1
    assert "Invalid date format" in errors[0]["error_message"]


def test_missing_required_columns():
    csv_content = b"""description,amount
Coffee,500
"""
    with pytest.raises(CSVValidationError) as exc_info:
        parse_and_validate_csv(csv_content)
    assert "missing required column" in str(exc_info.value)


def test_csv_idempotency_and_provenance(temp_db):
    sample_path = Path("sample_data/expenses_2026_09.csv")
    assert sample_path.exists()
    content = sample_path.read_bytes()

    valid_rows, errors = parse_and_validate_csv(content)
    assert len(errors) == 0
    assert len(valid_rows) == 12

    # First import must succeed
    status = check_csv_import_status("expenses_2026_09.csv", content, db_path=temp_db)
    assert status["is_duplicate_file"] is False

    import_id, inserted_ids = commit_csv_import("expenses_2026_09.csv", content, valid_rows, db_path=temp_db)
    assert import_id > 0
    assert len(inserted_ids) == 12

    # Second import with exact same bytes must be detected as duplicate
    status2 = check_csv_import_status("expenses_2026_09.csv", content, db_path=temp_db)
    assert status2["is_duplicate_file"] is True
    assert status2["existing_import_id"] == import_id

    # Trying to commit again raises ValueError
    with pytest.raises(ValueError) as exc:
        commit_csv_import("expenses_2026_09.csv", content, valid_rows, db_path=temp_db)
    assert "already been imported" in str(exc.value)

    # Check transactions in DB
    txs = database.get_transactions(month="2026-09", db_path=temp_db)
    assert len(txs) == 12
    for t in txs:
        assert t["import_id"] == import_id
        assert t["source_row"] is not None


def test_import_both_sample_months(temp_db):
    sep_bytes = Path("sample_data/expenses_2026_09.csv").read_bytes()
    oct_bytes = Path("sample_data/expenses_2026_10.csv").read_bytes()

    sep_rows, _ = parse_and_validate_csv(sep_bytes)
    oct_rows, _ = parse_and_validate_csv(oct_bytes)

    commit_csv_import("sep.csv", sep_bytes, sep_rows, db_path=temp_db)
    commit_csv_import("oct.csv", oct_bytes, oct_rows, db_path=temp_db)

    months = database.get_distinct_months(db_path=temp_db)
    assert months == ["2026-09", "2026-10"]

    sep_txs = database.get_transactions(month="2026-09", db_path=temp_db)
    oct_txs = database.get_transactions(month="2026-10", db_path=temp_db)
    assert len(sep_txs) == 12
    assert len(oct_txs) == 15
