"""SQLite schema and persistence repository helpers for BudgetPilot."""

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Dict, List, Optional, Tuple
from config import DB_PATH

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

def insert_transactions(
    rows: List[Dict[str, Any]],
    db_path: Optional[str] = None,
) -> List[int]:
    """Insert transaction rows atomically in a single transaction."""
    now = utc_now_iso()
    inserted_ids = []
    with get_connection(db_path) as conn:
        for r in rows:
            cursor = conn.execute(
                """
                INSERT INTO transactions (
                    occurred_on, description, merchant, amount_minor, category,
                    type, source, import_id, source_row, invoice_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    r["occurred_on"],
                    r["description"],
                    r.get("merchant"),
                    r["amount_minor"],
                    r["category"],
                    r["type"],
                    r["source"],
                    r.get("import_id"),
                    r.get("source_row"),
                    r.get("invoice_id"),
                    r.get("created_at") or now,
                ),
            )
            inserted_ids.append(cursor.lastrowid)
        conn.commit()
    return inserted_ids


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


def find_potential_duplicates(
    date: str,
    amount_minor: int,
    description: str,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Find transactions with same date and amount, or similar description."""
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            SELECT * FROM transactions
            WHERE occurred_on = ? AND amount_minor = ?;
            """,
            (date, amount_minor),
        )
        exact = [dict(r) for r in cursor.fetchall()]
        if exact:
            return exact

        # Search within 3 days tolerance
        cursor = conn.execute(
            """
            SELECT * FROM transactions
            WHERE abs(julianday(occurred_on) - julianday(?)) <= 3
              AND amount_minor = ?;
            """,
            (date, amount_minor),
        )
        return [dict(r) for r in cursor.fetchall()]


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
) -> int:
    """Save an analysis report snapshot."""
    now = utc_now_iso()
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO reports (
                month, stats_json, analysis_json, transaction_revision, created_at
            )
            VALUES (?, ?, ?, ?, ?);
            """,
            (month, stats_json, analysis_json, transaction_revision, now),
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
) -> Optional[int]:
    """Record an alert event. Returns alert id or None if already exists."""
    now = utc_now_iso()
    sent_at = now if status == "sent" else None
    try:
        with get_connection(db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO alerts (month, category, threshold, amount_minor, status, created_at, sent_at)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (month, category, threshold, amount_minor, status, now, sent_at),
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
