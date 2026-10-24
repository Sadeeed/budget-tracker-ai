"""CSV parser, normalizer, validator, and import management for BudgetPilot."""

import csv
from datetime import datetime
import hashlib
import io
import re
from typing import Any, Dict, List, Optional, Tuple

from config import ALL_CATEGORIES, STANDARD_EXPENSE_CATEGORIES, INCOME_CATEGORY
from money import parse_to_minor
from models import TransactionType, TransactionSource
import database


REQUIRED_COLUMNS = {"date", "description", "amount"}
VALID_TYPES = {t.value for t in TransactionType}


class CSVValidationError(Exception):
    """Raised when CSV structure or row contents are fundamentally invalid."""
    pass


def compute_file_sha256(content_bytes: bytes) -> str:
    """Compute SHA-256 hash of file content."""
    return hashlib.sha256(content_bytes).hexdigest()


def validate_iso_date(date_str: str) -> str:
    """Validate and return normalized ISO YYYY-MM-DD date.

    Raises ValueError if invalid. Does NOT coerce unparseable dates to today.
    """
    cleaned = date_str.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cleaned):
        raise ValueError("Invalid date format. Must be exact ISO format YYYY-MM-DD.")
    try:
        dt = datetime.strptime(cleaned, "%Y-%m-%d")
        return dt.strftime("%Y-%m-%d")
    except ValueError as e:
        raise ValueError(
            f"Invalid date format '{cleaned}'. Must be exact ISO format YYYY-MM-DD."
        ) from e


def normalize_category(category_raw: Optional[str], tx_type: str) -> str:
    """Normalize known categories; preserve unknown/missing values for review."""
    if tx_type == TransactionType.INCOME.value:
        return INCOME_CATEGORY

    if not category_raw or not category_raw.strip():
        return ""

    cat = category_raw.strip()
    # Case-insensitive match against allowed categories
    for standard in ALL_CATEGORIES:
        if standard.lower() == cat.lower():
            return standard

    return cat


def parse_and_validate_csv(
    file_bytes: bytes,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Parse CSV bytes into validated rows and list of row errors.

    Returns:
        (valid_rows, errors)
        Each valid_row has:
          source_row, occurred_on, description, amount_minor, category, type, source
        Each error has:
          source_row, error_message, raw_row
    """
    try:
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = file_bytes.decode("latin-1")
        except Exception as e:
            raise CSVValidationError(f"Could not decode CSV file: {e}") from e

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise CSVValidationError("CSV file is empty or missing headers.")

    fieldnames_clean = {f.strip().lower(): f for f in reader.fieldnames if f}
    missing_cols = REQUIRED_COLUMNS - set(fieldnames_clean.keys())
    if missing_cols:
        raise CSVValidationError(
            f"CSV is missing required column(s): {', '.join(sorted(missing_cols))}. "
            "Required: date, description, amount (optional: category, type)."
        )

    date_col = fieldnames_clean["date"]
    desc_col = fieldnames_clean["description"]
    amount_col = fieldnames_clean["amount"]
    category_col = fieldnames_clean.get("category")
    type_col = fieldnames_clean.get("type")

    valid_rows: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for row_idx, row in enumerate(reader, start=1):
        raw_date = row.get(date_col, "") or ""
        raw_desc = row.get(desc_col, "")
        raw_amount = row.get(amount_col, "")
        raw_cat = row.get(category_col, "") if category_col else ""
        raw_type = row.get(type_col, "") if type_col else ""

        # Validate date
        try:
            occurred_on = validate_iso_date(raw_date)
        except ValueError as e:
            errors.append({"source_row": row_idx, "error_message": str(e), "raw": row})
            continue

        # Validate description
        description = raw_desc.strip() if raw_desc else ""
        if not description:
            errors.append({
                "source_row": row_idx,
                "error_message": "Description cannot be empty.",
                "raw": row,
            })
            continue

        # Validate amount
        try:
            amount_minor = parse_to_minor(raw_amount)
        except ValueError as e:
            errors.append({"source_row": row_idx, "error_message": str(e), "raw": row})
            continue

        # Validate type
        tx_type = raw_type.strip().lower() if raw_type and raw_type.strip() else TransactionType.EXPENSE.value
        if tx_type not in VALID_TYPES:
            errors.append({
                "source_row": row_idx,
                "error_message": f"Invalid type '{tx_type}'. Must be one of: {', '.join(sorted(VALID_TYPES))}.",
                "raw": row,
            })
            continue

        # Category normalization
        category = normalize_category(raw_cat, tx_type)
        allowed = [INCOME_CATEGORY] if tx_type == "income" else STANDARD_EXPENSE_CATEGORIES
        if category not in allowed:
            errors.append({"source_row": row_idx, "error_message": "Choose an allowed category before import.", "raw": row})
            continue

        valid_rows.append({
            "source_row": row_idx,
            "occurred_on": occurred_on,
            "description": description,
            "merchant": description,
            "amount_minor": amount_minor,
            "category": category,
            "type": tx_type,
            "source": TransactionSource.CSV.value,
        })

    return valid_rows, errors


def check_csv_import_status(
    filename: str,
    file_bytes: bytes,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Check file hash for existing import and inspect potential duplicate rows."""
    file_sha256 = compute_file_sha256(file_bytes)
    existing_import = database.get_import_by_hash(file_sha256, db_path=db_path)

    if existing_import:
        return {
            "is_duplicate_file": True,
            "file_sha256": file_sha256,
            "imported_at": existing_import["imported_at"],
            "existing_import_id": existing_import["id"],
        }

    return {
        "is_duplicate_file": False,
        "file_sha256": file_sha256,
        "imported_at": None,
        "existing_import_id": None,
    }


def commit_csv_import(
    filename: str,
    file_bytes: bytes,
    approved_rows: List[Dict[str, Any]],
    db_path: Optional[str] = None,
) -> Tuple[int, List[int]]:
    """Record the import and persist approved rows with provenance in a transaction."""
    file_sha256 = compute_file_sha256(file_bytes)
    existing = database.get_import_by_hash(file_sha256, db_path=db_path)
    if existing:
        raise ValueError(
            f"File '{filename}' with hash {file_sha256[:8]} has already been imported on {existing['imported_at']}."
        )

    if not approved_rows:
        raise CSVValidationError("Select at least one row to import.")
    for r in approved_rows:
        validate_iso_date(r["occurred_on"])
        allowed = [INCOME_CATEGORY] if r["type"] == "income" else STANDARD_EXPENSE_CATEGORIES
        if (not r["description"].strip() or r["category"] not in allowed or r["type"] not in VALID_TYPES
                or type(r["amount_minor"]) is not int or not 0 <= r["amount_minor"] <= 9223372036854775807):
            raise CSVValidationError("Edited rows contain invalid description, category, type or amount.")
    return database.commit_import(filename, file_sha256, approved_rows, db_path)


def find_cross_file_duplicates(rows, db_path=None):
    """Warn only: repeated legitimate purchases must remain user-controlled."""
    def identity(row):
        return (row["occurred_on"], database.normalize_vendor(row["description"]),
                row["amount_minor"], row["type"])
    existing = {identity(row) for row in database.get_transactions(db_path=db_path)}
    return [row["source_row"] for row in rows if identity(row) in existing]
