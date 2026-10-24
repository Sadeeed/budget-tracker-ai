"""Threshold alert evaluation rules, durable persistence, and publisher dispatching."""

from datetime import datetime, timezone
import inspect
import logging
from typing import Any, Callable, Dict, List, Optional

import database
from dnotifier_adapter import run_async

logger = logging.getLogger(__name__)


def evaluate_threshold_crossings(
    month: str,
    category: str,
    before_spent_minor: int,
    after_spent_minor: int,
    budget_minor: Optional[int],
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Determine if spending crossed 80% or 100% of the category budget.

    Persists an alert record with status='pending' if eligible and not already recorded.
    Returns list of newly triggered alert records.
    """
    if budget_minor is None or budget_minor <= 0:
        return []

    # Cross-multiply: no rounding down or floating point at fractional-paisa boundaries.
    threshold_100_minor = budget_minor

    triggered: List[Dict[str, Any]] = []

    # 1. Check 80% crossing
    if before_spent_minor * 100 < budget_minor * 80 <= after_spent_minor * 100:
        alert_id = database.create_alert(
            month=month,
            category=category,
            threshold=80,
            amount_minor=after_spent_minor,
            status="pending",
            db_path=db_path,
            budget_minor=budget_minor,
        )
        if alert_id:
            triggered.append({
                "id": alert_id,
                "month": month,
                "category": category,
                "threshold": 80,
                "amount_minor": after_spent_minor,
                "budget_minor": budget_minor,
                "status": "pending",
            })

    # 2. Check 100% crossing
    if before_spent_minor < threshold_100_minor and after_spent_minor >= threshold_100_minor:
        alert_id = database.create_alert(
            month=month,
            category=category,
            threshold=100,
            amount_minor=after_spent_minor,
            status="pending",
            db_path=db_path,
            budget_minor=budget_minor,
        )
        if alert_id:
            triggered.append({
                "id": alert_id,
                "month": month,
                "category": category,
                "threshold": 100,
                "amount_minor": after_spent_minor,
                "budget_minor": budget_minor,
                "status": "pending",
            })

    return triggered


def build_alert_payload(alert: Dict[str, Any]) -> Dict[str, Any]:
    """Construct the standardized alert payload for DNotifier messaging."""
    return {
        "type": "budget_threshold",
        "month": alert["month"],
        "category": alert["category"],
        "threshold_percent": alert["threshold"],
        "spent_minor": alert["amount_minor"],
        "budget_minor": alert.get("budget_minor"),
    }


def dispatch_alert_sync(
    alert: Dict[str, Any],
    publisher: Optional[Callable[[Dict[str, Any]], Any]] = None,
    db_path: Optional[str] = None,
) -> bool:
    """Dispatch an alert payload to the DNotifier publisher.

    Safely handles both sync and async publishers without failing the caller.
    Updates DB status to 'sent' or 'failed'.
    """
    alert_id = alert["id"]
    saved = next((a for a in database.get_alerts(db_path=db_path) if a["id"] == alert_id), None)
    if saved and saved["status"] == "sent":
        return True
    if saved:
        alert = saved
    if publisher is None:
        return False

    # Old events did not store the budget snapshot. Keep them pending rather than
    # fabricate an original limit from a potentially edited budget.
    if alert.get("budget_minor") is None:
        return False
    payload = build_alert_payload(alert)
    try:
        async def invoke():
            result = publisher(payload)
            if inspect.isawaitable(result):
                await result
        run_async(invoke)

        now = datetime.now(timezone.utc).isoformat()
        database.update_alert_status(alert_id, "sent", now, db_path=db_path)
        return True
    except Exception:
        logger.warning("Failed to publish alert %s; retained for explicit retry.", alert_id)
        database.update_alert_status(alert_id, "failed", db_path=db_path)
        return False
