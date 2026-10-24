"""Deterministic financial engine for BudgetPilot.

Performs all financial aggregations, budget variance calculations,
and Month-over-Month (MoM) comparisons using integer minor units.
Never calls external AI services or floats for currency calculations.
"""

from typing import Any, Dict, List, Optional
from datetime import datetime
from dateutil.relativedelta import relativedelta

from config import (
    DEFAULT_CURRENCY,
    OVERALL_BUDGET_KEY,
    STANDARD_EXPENSE_CATEGORIES,
)
from models import (
    CategoryMonthlyStats,
    MonthlyComputedStats,
    TopExpenseItem,
    TransactionType,
)
import database


def get_prior_month_str(month_str: str) -> str:
    """Return the immediate preceding calendar month YYYY-MM."""
    dt = datetime.strptime(month_str, "%Y-%m-%d" if len(month_str) == 10 else "%Y-%m")
    prior_dt = dt - relativedelta(months=1)
    return prior_dt.strftime("%Y-%m")


def calculate_category_spending(transactions: List[Dict[str, Any]]) -> Dict[str, int]:
    """Calculate net expense spending by category for a list of transactions.

    Net spending = sum(expense amounts) - sum(refund amounts).
    Income and transfer transactions are excluded from expense categories.
    """
    category_totals: Dict[str, int] = {}

    for t in transactions:
        tx_type = t["type"]
        category = t["category"]
        amount = t["amount_minor"]

        if tx_type == TransactionType.EXPENSE.value:
            category_totals[category] = category_totals.get(category, 0) + amount
        elif tx_type == TransactionType.REFUND.value:
            category_totals[category] = category_totals.get(category, 0) - amount
        # Income and transfer are ignored for expense category totals

    return category_totals


def calculate_monthly_stats(
    month: str,
    db_path: Optional[str] = None,
    compare_month: Optional[str] = None,
) -> MonthlyComputedStats:
    """Calculate comprehensive, deterministic monthly statistics.

    Args:
        month: Calendar month string 'YYYY-MM'
        db_path: Optional path to SQLite DB
        compare_month: Optional explicit prior month to compare against.
                       Defaults to immediate prior calendar month if transactions exist.

    Returns:
        MonthlyComputedStats model matching the DNotifier AI input contract.
    """
    current_txs = database.get_transactions(month=month, db_path=db_path)
    budgets = database.get_budgets(month=month, db_path=db_path)

    # 1. Total net expenses. Imported income remains ledger data, not the user's cap.
    total_expenses = 0
    total_refunds = 0

    for t in current_txs:
        tx_type = t["type"]
        amount = t["amount_minor"]
        if tx_type == TransactionType.EXPENSE.value:
            total_expenses += amount
        elif tx_type == TransactionType.REFUND.value:
            total_refunds += amount
        # Transfers are not counted in net expenses or income

    net_expenses = total_expenses - total_refunds

    # 2. Overall budget and variance
    overall_budget = budgets.get(OVERALL_BUDGET_KEY)
    overall_variance = None
    overall_percent_used = None

    if overall_budget is not None:
        overall_variance = net_expenses - overall_budget
        if overall_budget > 0:
            overall_percent_used = round((net_expenses / overall_budget) * 100, 1)

    # 3. Previous month transactions for MoM comparison
    prior_month = compare_month or get_prior_month_str(month)
    prior_txs = database.get_transactions(month=prior_month, db_path=db_path)
    has_prior_month = len(prior_txs) > 0
    prior_cat_spending = calculate_category_spending(prior_txs) if has_prior_month else {}

    # 4. Current category net spending
    curr_cat_spending = calculate_category_spending(current_txs)

    # Union of standard categories, categories with budgets, and categories with spending
    all_active_categories = sorted(
        set(STANDARD_EXPENSE_CATEGORIES)
        | set(curr_cat_spending.keys())
        | {cat for cat in budgets.keys() if cat != OVERALL_BUDGET_KEY}
    )

    category_stats_list: List[CategoryMonthlyStats] = []
    for cat in all_active_categories:
        spent = curr_cat_spending.get(cat, 0)
        budget = budgets.get(cat)

        over_budget = None
        percent_used = None
        if budget is not None:
            over_budget = max(0, spent - budget)
            if budget > 0:
                percent_used = round((spent / budget) * 100, 1)

        prev_spent = None
        mom_change = None
        mom_pct = None

        if has_prior_month:
            prev_spent = prior_cat_spending.get(cat, 0)
            mom_change = spent - prev_spent
            if prev_spent > 0:
                mom_pct = round(((spent - prev_spent) / prev_spent) * 100, 1)
            else:
                # DESIGN: "If prior spend is zero, percent change must be null / N/A rather than dividing by zero."
                mom_pct = None

        category_stats_list.append(
            CategoryMonthlyStats(
                category=cat,
                spent_minor=spent,
                budget_minor=budget,
                over_budget_minor=over_budget,
                percent_used=percent_used,
                previous_month_spent_minor=prev_spent,
                mom_change_minor=mom_change,
                mom_change_pct=mom_pct,
            )
        )

    # 5. Largest expenses (top 5 expenses)
    expense_txs = [t for t in current_txs if t["type"] == TransactionType.EXPENSE.value]
    expense_txs_sorted = sorted(expense_txs, key=lambda x: x["amount_minor"], reverse=True)
    largest_expenses = [
        TopExpenseItem(
            description=t["description"],
            amount_minor=t["amount_minor"],
            date=t["occurred_on"],
            category=t["category"],
        )
        for t in expense_txs_sorted[:5]
    ]

    # 6. Data notes
    data_notes = [
        f"Totals calculated deterministically in Python using integer minor units (1 {DEFAULT_CURRENCY} = 100 paisa).",
        "Monthly income is set in the sidebar and is the overall spending cap; CSV income is not added to it."
    ]
    if overall_budget is None:
        data_notes.append("Monthly income is not configured; income_minor=0 is a placeholder, not verified zero income.")
    if has_prior_month:
        data_notes.append(f"Comparison data available for previous month ({prior_month}).")
    else:
        data_notes.append("No prior month transactions found; MoM comparison not available.")

    if not budgets:
        data_notes.append("No budget limits configured for this month.")

    return MonthlyComputedStats(
        month=month,
        currency=DEFAULT_CURRENCY,
        overall_budget_minor=overall_budget,
        net_expenses_minor=net_expenses,
        income_minor=overall_budget if overall_budget is not None else 0,
        overall_variance_minor=overall_variance,
        overall_percent_used=overall_percent_used,
        categories=category_stats_list,
        largest_expenses=largest_expenses,
        data_notes=data_notes,
    )
