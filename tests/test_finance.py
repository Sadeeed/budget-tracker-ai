"""Tests for deterministic financial arithmetic and monthly statistics calculation."""

from pathlib import Path
import tempfile
import pytest

import database
from csv_import import parse_and_validate_csv, commit_csv_import
from finance import calculate_monthly_stats, get_prior_month_str
from models import TransactionType, OVERALL_BUDGET_KEY


@pytest.fixture
def temp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    database.init_db(db_path)
    yield db_path
    Path(db_path).unlink(missing_ok=True)


def test_prior_month_helper():
    assert get_prior_month_str("2026-10") == "2026-09"
    assert get_prior_month_str("2026-01") == "2025-12"


def test_net_expenses_with_refunds_and_transfers(temp_db):
    txs = [
        # Regular expense
        {
            "occurred_on": "2026-10-01",
            "description": "Store Purchase",
            "amount_minor": 100000,  # 1000
            "category": "Shopping",
            "type": TransactionType.EXPENSE.value,
            "source": "csv",
        },
        # Another expense
        {
            "occurred_on": "2026-10-02",
            "description": "Lunch",
            "amount_minor": 50000,  # 500
            "category": "Dining",
            "type": TransactionType.EXPENSE.value,
            "source": "csv",
        },
        # Refund for Shopping
        {
            "occurred_on": "2026-10-05",
            "description": "Store Refund",
            "amount_minor": 20000,  # 200
            "category": "Shopping",
            "type": TransactionType.REFUND.value,
            "source": "csv",
        },
        # Transfer - must NOT be counted in net expenses
        {
            "occurred_on": "2026-10-10",
            "description": "Transfer to Savings",
            "amount_minor": 300000,  # 3000
            "category": "Other",
            "type": TransactionType.TRANSFER.value,
            "source": "csv",
        },
        # Income - must be separate
        {
            "occurred_on": "2026-10-15",
            "description": "Salary",
            "amount_minor": 1000000,  # 10000
            "category": "Income",
            "type": TransactionType.INCOME.value,
            "source": "csv",
        },
    ]
    database.insert_transactions(txs, db_path=temp_db)
    # The sidebar setting is authoritative; imported salary must not be added to it.
    database.set_budget("2026-10", "__overall__", 2000000, db_path=temp_db)

    stats = calculate_monthly_stats("2026-10", db_path=temp_db)
    # Net expenses: 1000 + 500 - 200 = 1300 (130,000 minor)
    assert stats.net_expenses_minor == 130000
    assert stats.income_minor == 2000000

    # Check Shopping category net: 1000 - 200 = 800 (80,000 minor)
    shopping_stat = next(c for c in stats.categories if c.category == "Shopping")
    assert shopping_stat.spent_minor == 80000

    # Check Dining category: 500 (50,000 minor)
    dining_stat = next(c for c in stats.categories if c.category == "Dining")
    assert dining_stat.spent_minor == 50000


def test_budget_variances(temp_db):
    txs = [
        {
            "occurred_on": "2026-10-02",
            "description": "Dinner",
            "amount_minor": 1200000,  # 12,000 spent
            "category": "Dining",
            "type": TransactionType.EXPENSE.value,
            "source": "csv",
        },
        {
            "occurred_on": "2026-10-03",
            "description": "Rent",
            "amount_minor": 3500000,  # 35,000 spent
            "category": "Housing",
            "type": TransactionType.EXPENSE.value,
            "source": "csv",
        },
    ]
    database.insert_transactions(txs, db_path=temp_db)

    # Set budgets: Dining 10,000 (over budget), Housing 40,000 (under budget), Overall 50,000
    database.set_budget("2026-10", "Dining", 1000000, db_path=temp_db)
    database.set_budget("2026-10", "Housing", 4000000, db_path=temp_db)
    database.set_budget("2026-10", OVERALL_BUDGET_KEY, 5000000, db_path=temp_db)

    stats = calculate_monthly_stats("2026-10", db_path=temp_db)

    # Total net expenses: 47,000
    assert stats.net_expenses_minor == 4700000
    assert stats.overall_budget_minor == 5000000
    assert stats.overall_variance_minor == -300000  # 47,000 - 50,000 = -3,000 (under cap)
    assert stats.overall_percent_used == 94.0

    # Dining: 12,000 vs 10,000 limit -> 2,000 over, 120% used
    dining = next(c for c in stats.categories if c.category == "Dining")
    assert dining.spent_minor == 1200000
    assert dining.budget_minor == 1000000
    assert dining.over_budget_minor == 200000
    assert dining.percent_used == 120.0

    # Housing: 35,000 vs 40,000 limit -> 0 over, 87.5% used
    housing = next(c for c in stats.categories if c.category == "Housing")
    assert housing.spent_minor == 3500000
    assert housing.budget_minor == 4000000
    assert housing.over_budget_minor == 0
    assert housing.percent_used == 87.5

    # Category without budget: variance and percent_used must be None
    transport = next(c for c in stats.categories if c.category == "Transport")
    assert transport.budget_minor is None
    assert transport.over_budget_minor is None
    assert transport.percent_used is None


def test_mom_division_by_zero_prevention(temp_db):
    # September: Dining spent = 0, Groceries spent = 10,000
    # October: Dining spent = 5,000, Groceries spent = 12,000
    sep_txs = [
        {
            "occurred_on": "2026-09-05",
            "description": "Metro",
            "amount_minor": 1000000,
            "category": "Groceries",
            "type": TransactionType.EXPENSE.value,
            "source": "csv",
        }
    ]
    oct_txs = [
        {
            "occurred_on": "2026-10-05",
            "description": "Metro",
            "amount_minor": 1200000,
            "category": "Groceries",
            "type": TransactionType.EXPENSE.value,
            "source": "csv",
        },
        {
            "occurred_on": "2026-10-10",
            "description": "Cafe",
            "amount_minor": 500000,
            "category": "Dining",
            "type": TransactionType.EXPENSE.value,
            "source": "csv",
        },
    ]
    database.insert_transactions(sep_txs, db_path=temp_db)
    database.insert_transactions(oct_txs, db_path=temp_db)

    stats = calculate_monthly_stats("2026-10", db_path=temp_db)

    # Groceries: 12,000 vs 10,000 -> mom_change = +2,000, mom_change_pct = +20.0%
    groceries = next(c for c in stats.categories if c.category == "Groceries")
    assert groceries.previous_month_spent_minor == 1000000
    assert groceries.mom_change_minor == 200000
    assert groceries.mom_change_pct == 20.0

    # Dining: prior spend was 0. Must NOT raise ZeroDivisionError, mom_change_pct must be None!
    dining = next(c for c in stats.categories if c.category == "Dining")
    assert dining.previous_month_spent_minor == 0
    assert dining.mom_change_minor == 500000
    assert dining.mom_change_pct is None


def test_sample_fixtures_e2e_finance(temp_db):
    sep_bytes = Path("sample_data/expenses_2026_09.csv").read_bytes()
    oct_bytes = Path("sample_data/expenses_2026_10.csv").read_bytes()

    sep_rows, _ = parse_and_validate_csv(sep_bytes)
    oct_rows, _ = parse_and_validate_csv(oct_bytes)

    commit_csv_import("expenses_2026_09.csv", sep_bytes, sep_rows, db_path=temp_db)
    commit_csv_import("expenses_2026_10.csv", oct_bytes, oct_rows, db_path=temp_db)

    # Configure budgets for October
    database.set_budget("2026-10", "Dining", 1000000, db_path=temp_db)  # Rs 10,000 limit
    database.set_budget("2026-10", "Groceries", 2000000, db_path=temp_db)  # Rs 20,000 limit

    stats = calculate_monthly_stats("2026-10", db_path=temp_db)

    # October Dining: Barbeque Tonight (7500) + Food Delivery (4200) + Brunch (3500) + Cafe Gelato (2000) = 17,200
    dining = next(c for c in stats.categories if c.category == "Dining")
    assert dining.spent_minor == 1720000
    assert dining.budget_minor == 1000000
    assert dining.over_budget_minor == 720000
    assert dining.percent_used == 172.0

    # September Dining was: Cafe Bistro (3200) + Food Delivery (2600) + Coffee Shop (1200) = 7,000
    assert dining.previous_month_spent_minor == 700000
    assert dining.mom_change_minor == 1020000
    assert dining.mom_change_pct == 145.7

    # Largest expenses list
    assert len(stats.largest_expenses) == 5
    assert stats.largest_expenses[0].description == "Apartment Rent"
    assert stats.largest_expenses[0].amount_minor == 3500000
