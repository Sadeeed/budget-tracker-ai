"""SQLite schema and persistence repository helpers for BudgetPilot."""

from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import json
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple
from config import DB_PATH, STANDARD_EXPENSE_CATEGORIES

DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS budgets (
      id INTEGER PRIMARY KEY,
      month TEXT NOT NULL,                   -- YYYY-MM
      category TEXT NOT NULL,                -- '__overall__' for overall cap
      limit_minor INTEGER NOT NULL CHECK(limit_minor >= 0),
      UNIQUE(month, category)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS imports (
      id INTEGER PRIMARY KEY,
      filename TEXT NOT NULL,
      file_sha256 TEXT NOT NULL UNIQUE,
      imported_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS invoices (
      id INTEGER PRIMARY KEY,
      invoice_number TEXT,
      merchant TEXT NOT NULL,
      invoice_date TEXT NOT NULL,
      total_minor INTEGER NOT NULL CHECK(total_minor >= 0),
      category TEXT NOT NULL,
      payment_status TEXT NOT NULL CHECK(payment_status IN ('paid','unpaid')),
      file_sha256 TEXT,
      linked_transaction_id INTEGER,
      created_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS transactions (
      id INTEGER PRIMARY KEY,
      occurred_on TEXT NOT NULL,
      description TEXT NOT NULL,
      merchant TEXT,
      amount_minor INTEGER NOT NULL CHECK(amount_minor >= 0),
      category TEXT NOT NULL,
      type TEXT NOT NULL CHECK(type IN ('expense','refund','income','transfer')),
      source TEXT NOT NULL CHECK(source IN ('csv','invoice','manual')),
      import_id INTEGER,
      source_row INTEGER,
      invoice_id INTEGER,
      created_at TEXT NOT NULL,
      UNIQUE(import_id, source_row)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS reports (
      id INTEGER PRIMARY KEY,
      month TEXT NOT NULL,
      stats_json TEXT NOT NULL,
      analysis_json TEXT NOT NULL,
      transaction_revision INTEGER NOT NULL,
      created_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS alerts (
      id INTEGER PRIMARY KEY,
      month TEXT NOT NULL,
      category TEXT NOT NULL,
      threshold INTEGER NOT NULL CHECK(threshold IN (80,100)),
      amount_minor INTEGER NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('pending','sent','failed')),
      created_at TEXT NOT NULL,
      sent_at TEXT,
      UNIQUE(month, category, threshold)
    );
    """,
]


def utc_now_iso() -> str:
    """Return current UTC time in ISO format."""
    return datetime.now(timezone.utc).isoformat()


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Create and return a configured sqlite3 connection with Row factory."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: Optional[str] = None) -> None:
    """Initialize database tables idempotently."""
    with get_connection(db_path) as conn:
        for stmt in DDL_STATEMENTS:
            conn.execute(stmt)
        # Additive migrations retain existing demo data and label old reports honestly.
        if "origin" not in {r[1] for r in conn.execute("PRAGMA table_info(reports)")}:
            conn.execute("ALTER TABLE reports ADD COLUMN origin TEXT NOT NULL DEFAULT 'legacy_unverified'")
        if "budget_minor" not in {r[1] for r in conn.execute("PRAGMA table_info(alerts)")}:
            conn.execute("ALTER TABLE alerts ADD COLUMN budget_minor INTEGER")
        conn.commit()


# --- Budgets ---

def set_budget(month: str, category: str, limit_minor: int, db_path: Optional[str] = None) -> None:
    """Insert or update a budget limit for a month and category."""
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO budgets (month, category, limit_minor)
            VALUES (?, ?, ?)
            ON CONFLICT(month, category) DO UPDATE SET limit_minor=excluded.limit_minor;
            """,
            (month, category, limit_minor),
        )
        conn.commit()


def get_budgets(month: str, db_path: Optional[str] = None) -> Dict[str, int]:
    """Return category -> limit_minor map for the specified month."""
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            "SELECT category, limit_minor FROM budgets WHERE month = ?;",
            (month,),
        )
        return {row["category"]: row["limit_minor"] for row in cursor.fetchall()}


def delete_budget(month, category, db_path=None):
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM budgets WHERE month = ? AND category = ?", (month, category))


# --- Imports ---

def get_import_by_hash(file_sha256: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Look up an import record by its SHA-256 hash."""
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            "SELECT id, filename, file_sha256, imported_at FROM imports WHERE file_sha256 = ?;",
            (file_sha256,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def create_import(filename: str, file_sha256: str, db_path: Optional[str] = None) -> int:
    """Record an imported file."""
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO imports (filename, file_sha256, imported_at)
            VALUES (?, ?, ?);
            """,
            (filename, file_sha256, utc_now_iso()),
        )
        conn.commit()
        return cursor.lastrowid


# --- Transactions ---

def commit_import(filename, file_sha256, rows, db_path=None):
    """File provenance, financial rows and pending alerts succeed or roll back together."""
    with get_connection(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM imports WHERE file_sha256 = ?", (file_sha256,)).fetchone():
            raise ValueError("This file has already been imported.")
        import_id = conn.execute(
            "INSERT INTO imports(filename, file_sha256, imported_at) VALUES (?, ?, ?)",
            (filename, file_sha256, utc_now_iso()),
        ).lastrowid
        ids = _insert_transactions(conn, [dict(row, import_id=import_id) for row in rows])
        return import_id, ids


def _category_total(conn, month, category):
    return conn.execute(
        """SELECT COALESCE(SUM(CASE type WHEN 'expense' THEN amount_minor
           WHEN 'refund' THEN -amount_minor ELSE 0 END), 0) FROM transactions
           WHERE substr(occurred_on, 1, 7) = ? AND category = ?""", (month, category)
    ).fetchone()[0]


def _queue_crossings(conn, month, category, before, after):
    """Queue in the same write transaction as the expense; no SDK calls here."""
    row = conn.execute("SELECT limit_minor FROM budgets WHERE month = ? AND category = ?",
                       (month, category)).fetchone()
    if row is None or row[0] <= 0:
        return
    for threshold in (80, 100):
        if before * 100 < row[0] * threshold <= after * 100:
            conn.execute(
                """INSERT INTO alerts(month, category, threshold, amount_minor, status,
                   created_at, budget_minor) VALUES (?, ?, ?, ?, 'pending', ?, ?)
                   ON CONFLICT(month, category, threshold) DO NOTHING""",
                (month, category, threshold, after, utc_now_iso(), row[0]),
            )


def _insert_transactions(conn, rows):
    now = utc_now_iso()
    ids = []
    for r in rows:
        month, category = r["occurred_on"][:7], r["category"]
        before = _category_total(conn, month, category)
        cursor = conn.execute(
            """INSERT INTO transactions(occurred_on, description, merchant, amount_minor,
               category, type, source, import_id, source_row, invoice_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (r["occurred_on"], r["description"], r.get("merchant"), r["amount_minor"],
             category, r["type"], r["source"], r.get("import_id"), r.get("source_row"),
             r.get("invoice_id"), r.get("created_at") or now),
        )
        ids.append(cursor.lastrowid)
        if r["type"] == "expense":
            _queue_crossings(conn, month, category, before, before + r["amount_minor"])
    return ids

def insert_transactions(
    rows: List[Dict[str, Any]],
    db_path: Optional[str] = None,
) -> List[int]:
    """Insert transaction rows atomically in a single transaction."""
    with get_connection(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        return _insert_transactions(conn, rows)


def get_transactions(
    month: Optional[str] = None,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fetch transactions, optionally filtered by occurred_on YYYY-MM."""
    with get_connection(db_path) as conn:
        if month:
            cursor = conn.execute(
                """
                SELECT * FROM transactions
                WHERE substr(occurred_on, 1, 7) = ?
                ORDER BY occurred_on ASC, id ASC;
                """,
                (month,),
            )
        else:
            cursor = conn.execute(
                "SELECT * FROM transactions ORDER BY occurred_on ASC, id ASC;"
            )
        return [dict(r) for r in cursor.fetchall()]


def get_distinct_months(db_path: Optional[str] = None) -> List[str]:
    """Return sorted distinct calendar months (YYYY-MM) present in transactions."""
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            SELECT DISTINCT substr(occurred_on, 1, 7) AS month
            FROM transactions
            ORDER BY month ASC;
            """
        )
        return [r["month"] for r in cursor.fetchall()]


def get_transaction_revision(month: str, db_path: Optional[str] = None) -> int:
    """Calculate a monotonic revision for transactions in a given month.

    Sum of transaction IDs + count in this month, so any insert/delete changes the value.
    """
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            SELECT COUNT(*) AS cnt, COALESCE(SUM(id), 0) AS id_sum
            FROM transactions
            WHERE substr(occurred_on, 1, 7) = ?;
            """,
            (month,),
        )
        row = cursor.fetchone()
        return int(row["cnt"]) * 100000 + int(row["id_sum"])


def get_report_revision(month: str, db_path: Optional[str] = None) -> int:
    """Fingerprint all report facts, including comparison-month data and budget edits."""
    from finance import get_prior_month_str
    facts = [get_transactions(month, db_path),
             get_transactions(get_prior_month_str(month), db_path), get_budgets(month, db_path)]
    return int(hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()[:15], 16)


def normalize_vendor(value):
    return " ".join(re.findall(r"\w+", value.casefold()))


def find_potential_duplicates(
    date: str,
    amount_minor: int,
    description: str,
    db_path: Optional[str] = None,
    invoice_number: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Find transactions with same date and amount, or similar description."""
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            SELECT t.* FROM transactions t LEFT JOIN invoices i ON t.invoice_id = i.id
            WHERE t.type = 'expense' AND t.amount_minor = ? AND
              (abs(julianday(t.occurred_on) - julianday(?)) <= 3
               OR (? IS NOT NULL AND i.invoice_number = ?));
            """,
            (amount_minor, date, invoice_number, invoice_number),
        )
        vendor = normalize_vendor(description)
        return [dict(r) for r in cursor.fetchall() if SequenceMatcher(
            None, vendor, normalize_vendor(r["merchant"] or r["description"])
        ).ratio() >= 0.5]


def _validate_invoice(conn, merchant, invoice_date, total_minor, category,
                      invoice_number, file_sha256):
    try:
        valid_date = datetime.strptime(invoice_date, "%Y-%m-%d").strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        raise ValueError("Invoice date must be YYYY-MM-DD.") from None
    if valid_date != invoice_date or not merchant.strip():
        raise ValueError("Enter a merchant and an exact YYYY-MM-DD date.")
    if type(total_minor) is not int or not 0 <= total_minor <= 9223372036854775807:
        raise ValueError("Invoice total must be nonnegative integer minor units within SQLite range.")
    if category not in STANDARD_EXPENSE_CATEGORIES:
        raise ValueError("Choose an allowed expense category.")
    for row in conn.execute("SELECT merchant, invoice_number, file_sha256 FROM invoices"):
        if (file_sha256 and file_sha256 == row["file_sha256"]) or (
            invoice_number and invoice_number == row["invoice_number"]
            and normalize_vendor(merchant) == normalize_vendor(row["merchant"])
        ):
            raise ValueError("This receipt is already saved; no additional expense was created.")


# --- Invoices ---

def create_invoice(
    merchant: str,
    invoice_date: str,
    total_minor: int,
    category: str,
    payment_status: str,
    invoice_number: Optional[str] = None,
    file_sha256: Optional[str] = None,
    linked_transaction_id: Optional[int] = None,
    db_path: Optional[str] = None,
) -> int:
    """Persist an invoice record."""
    now = utc_now_iso()
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO invoices (
                invoice_number, merchant, invoice_date, total_minor,
                category, payment_status, file_sha256, linked_transaction_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                invoice_number,
                merchant,
                invoice_date,
                total_minor,
                category,
                payment_status,
                file_sha256,
                linked_transaction_id,
                now,
            ),
        )
        conn.commit()
        return cursor.lastrowid


def link_invoice_to_transaction(
    invoice_id: int,
    transaction_id: int,
    db_path: Optional[str] = None,
) -> None:
    """Update mutual linkage between an invoice and a transaction."""
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE invoices SET linked_transaction_id = ? WHERE id = ?;",
            (transaction_id, invoice_id),
        )
        conn.execute(
            "UPDATE transactions SET invoice_id = ? WHERE id = ?;",
            (invoice_id, transaction_id),
        )
        conn.commit()


def get_invoices(
    month: Optional[str] = None,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Retrieve invoices, optionally filtered by month YYYY-MM."""
    with get_connection(db_path) as conn:
        if month:
            cursor = conn.execute(
                """
                SELECT * FROM invoices
                WHERE substr(invoice_date, 1, 7) = ?
                ORDER BY invoice_date DESC, id DESC;
                """,
                (month,),
            )
        else:
            cursor = conn.execute("SELECT * FROM invoices ORDER BY invoice_date DESC, id DESC;")
        return [dict(r) for r in cursor.fetchall()]


def commit_paid_invoice_as_new_expense(
    merchant: str,
    invoice_date: str,
    total_minor: int,
    category: str,
    invoice_number: Optional[str] = None,
    file_sha256: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Tuple[int, int]:
    """Atomically commit a paid invoice and create exactly ONE new expense transaction."""
    now = utc_now_iso()
    with get_connection(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _validate_invoice(conn, merchant, invoice_date, total_minor, category, invoice_number, file_sha256)
        before = _category_total(conn, invoice_date[:7], category)
        # 1. Insert invoice
        inv_cursor = conn.execute(
            """
            INSERT INTO invoices (
                invoice_number, merchant, invoice_date, total_minor,
                category, payment_status, file_sha256, created_at
            )
            VALUES (?, ?, ?, ?, ?, 'paid', ?, ?);
            """,
            (invoice_number, merchant, invoice_date, total_minor, category, file_sha256, now),
        )
        invoice_id = inv_cursor.lastrowid

        # 2. Insert transaction
        desc = f"Invoice: {merchant}" + (f" (#{invoice_number})" if invoice_number else "")
        tx_cursor = conn.execute(
            """
            INSERT INTO transactions (
                occurred_on, description, merchant, amount_minor, category,
                type, source, invoice_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, 'expense', 'invoice', ?, ?);
            """,
            (invoice_date, desc, merchant, total_minor, category, invoice_id, now),
        )
        transaction_id = tx_cursor.lastrowid

        # 3. Link invoice to new transaction
        conn.execute(
            "UPDATE invoices SET linked_transaction_id = ? WHERE id = ?;",
            (transaction_id, invoice_id),
        )
        _queue_crossings(conn, invoice_date[:7], category, before, before + total_minor)
        conn.commit()
        return invoice_id, transaction_id


def commit_paid_invoice_linked(
    merchant: str,
    invoice_date: str,
    total_minor: int,
    category: str,
    linked_transaction_id: int,
    invoice_number: Optional[str] = None,
    file_sha256: Optional[str] = None,
    db_path: Optional[str] = None,
) -> int:
    """Commit a paid invoice linked to an existing transaction (0 change to spend)."""
    now = utc_now_iso()
    with get_connection(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _validate_invoice(conn, merchant, invoice_date, total_minor, category, invoice_number, file_sha256)
        target = conn.execute("SELECT * FROM transactions WHERE id = ?", (linked_transaction_id,)).fetchone()
        if (target is None or target["type"] != "expense" or target["amount_minor"] != total_minor
                or target["invoice_id"] is not None):
            raise ValueError("Select an unlinked expense with the same amount.")
        if (abs((datetime.strptime(target["occurred_on"], "%Y-%m-%d") - datetime.strptime(invoice_date, "%Y-%m-%d")).days) > 3
                or SequenceMatcher(None, normalize_vendor(merchant),
                                   normalize_vendor(target["merchant"] or target["description"])).ratio() < 0.5):
            raise ValueError("Linked expense must match the merchant and be within three days.")
        cursor = conn.execute(
            """
            INSERT INTO invoices (
                invoice_number, merchant, invoice_date, total_minor,
                category, payment_status, file_sha256, linked_transaction_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, 'paid', ?, ?, ?);
            """,
            (
                invoice_number,
                merchant,
                invoice_date,
                total_minor,
                category,
                file_sha256,
                linked_transaction_id,
                now,
            ),
        )
        invoice_id = cursor.lastrowid
        conn.execute(
            "UPDATE transactions SET invoice_id = ? WHERE id = ?;",
            (invoice_id, linked_transaction_id),
        )
        conn.commit()
        return invoice_id


def commit_unpaid_invoice(
    merchant: str,
    invoice_date: str,
    total_minor: int,
    category: str,
    invoice_number: Optional[str] = None,
    file_sha256: Optional[str] = None,
    db_path: Optional[str] = None,
) -> int:
    """Commit an unpaid invoice (commitment only, no transaction created)."""
    now = utc_now_iso()
    with get_connection(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _validate_invoice(conn, merchant, invoice_date, total_minor, category, invoice_number, file_sha256)
        cursor = conn.execute(
            """
            INSERT INTO invoices (
                invoice_number, merchant, invoice_date, total_minor,
                category, payment_status, file_sha256, created_at
            )
            VALUES (?, ?, ?, ?, ?, 'unpaid', ?, ?);
            """,
            (invoice_number, merchant, invoice_date, total_minor, category, file_sha256, now),
        )
        conn.commit()
        return cursor.lastrowid


# --- Reports ---

def save_report(
    month: str,
    stats_json: str,
    analysis_json: str,
    transaction_revision: int,
    db_path: Optional[str] = None,
    origin: str = "legacy_unverified",
) -> int:
    """Save an analysis report snapshot."""
    now = utc_now_iso()
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO reports (
                month, stats_json, analysis_json, transaction_revision, created_at, origin
            )
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            (month, stats_json, analysis_json, transaction_revision, now, origin),
        )
        conn.commit()
        return cursor.lastrowid


def get_latest_report(month: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieve the latest saved report for a month."""
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            SELECT * FROM reports
            WHERE month = ?
            ORDER BY id DESC LIMIT 1;
            """,
            (month,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


# --- Alerts ---

def create_alert(
    month: str,
    category: str,
    threshold: int,
    amount_minor: int,
    status: str = "pending",
    db_path: Optional[str] = None,
    budget_minor: Optional[int] = None,
) -> Optional[int]:
    """Record an alert event. Returns alert id or None if already exists."""
    now = utc_now_iso()
    sent_at = now if status == "sent" else None
    try:
        with get_connection(db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO alerts (month, category, threshold, amount_minor, status, created_at, sent_at, budget_minor)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (month, category, threshold, amount_minor, status, now, sent_at, budget_minor),
            )
            conn.commit()
            return cursor.lastrowid
    except sqlite3.IntegrityError:
        # (month, category, threshold) is UNIQUE; already recorded
        return None


def get_alert(
    month: str,
    category: str,
    threshold: int,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Check if an alert exists for month/category/threshold."""
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            SELECT * FROM alerts
            WHERE month = ? AND category = ? AND threshold = ?;
            """,
            (month, category, threshold),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def update_alert_status(
    alert_id: int,
    status: str,
    sent_at: Optional[str] = None,
    db_path: Optional[str] = None,
) -> None:
    """Update status ('sent' or 'failed') and sent_at timestamp."""
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE alerts SET status = ?, sent_at = ? WHERE id = ?;",
            (status, sent_at, alert_id),
        )
        conn.commit()


def get_alerts(
    month: Optional[str] = None,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Retrieve alerts, optionally filtered by month."""
    with get_connection(db_path) as conn:
        if month:
            cursor = conn.execute(
                "SELECT * FROM alerts WHERE month = ? ORDER BY id DESC;",
                (month,),
            )
        else:
            cursor = conn.execute("SELECT * FROM alerts ORDER BY id DESC;")
        return [dict(r) for r in cursor.fetchall()]
