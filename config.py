"""Configuration, categories, and environment settings for BudgetPilot."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env if present
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

# Database configuration
DEFAULT_DB_PATH = str(BASE_DIR / "budgetpilot.db")
DB_PATH = os.getenv("BUDGETPILOT_DB_PATH", DEFAULT_DB_PATH)

# Currency settings
DEFAULT_CURRENCY = "PKR"
CURRENCY_SYMBOL = "Rs"

# Standard expense categories as defined in docs/DESIGN.md
STANDARD_EXPENSE_CATEGORIES = [
    "Housing",
    "Groceries",
    "Dining",
    "Transport",
    "Utilities",
    "Shopping",
    "Health",
    "Entertainment",
    "Subscriptions",
    "Education",
    "Other",
]

INCOME_CATEGORY = "Income"
ALL_CATEGORIES = STANDARD_EXPENSE_CATEGORIES + [INCOME_CATEGORY]

# Special key for overall monthly budget
OVERALL_BUDGET_KEY = "__overall__"

# DNotifier Settings
DNOTIFIER_APP_ID = os.getenv("DNOTIFIER_APP_ID")
DNOTIFIER_SECRET = os.getenv("DNOTIFIER_SECRET")
DNOTIFIER_AI_USER_ID = os.getenv("DNOTIFIER_AI_USER_ID", "budgetpilot-ai-demo")
DNOTIFIER_SENDER_ID = os.getenv("DNOTIFIER_SENDER_ID", "budgetpilot-service")
DNOTIFIER_RECEIVER_ID = os.getenv("DNOTIFIER_RECEIVER_ID", "budgetpilot-user-demo")
DNOTIFIER_URL = os.getenv("DNOTIFIER_URL")


def is_dnotifier_configured() -> bool:
    """Return True if both App ID and Secret are provided."""
    return bool(
        DNOTIFIER_APP_ID
        and DNOTIFIER_SECRET
        and DNOTIFIER_APP_ID != "replace_me"
        and DNOTIFIER_SECRET != "replace_me"
    )
