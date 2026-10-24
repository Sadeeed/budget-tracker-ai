"""Threshold alert evaluation rules, durable persistence, and publisher dispatching."""

import asyncio
from datetime import datetime, timezone
import inspect
import logging
from typing import Any, Callable, Dict, List, Optional

import database
from money import format_pkr

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

    # Threshold values in minor units
    threshold_80_minor = int(budget_minor * 0.8)
    threshold_100_minor = budget_minor

    triggered: List[Dict[str, Any]] = []

    # 1. Check 80% crossing
    if before_spent_minor < threshold_80_minor and after_spent_minor >= threshold_80_minor:
        alert_id = database.create_alert(
            month=month,
            category=category,
            threshold=80,
            amount_minor=after_spent_minor,
            status="pending",
            db_path=db_path,
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
        "budget_minor": alert.get("budget_minor", 0),
        "message": (
            f"Alert: {alert['category']} spending reached {alert['threshold']}% of monthly budget "
            f"({format_pkr(alert['amount_minor'])} spent in {alert['month']})."
        ),
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
    if publisher is None:
        return False

    payload = build_alert_payload(alert)
    try:
        res = publisher(payload)
        if inspect.isawaitable(res):
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            if loop.is_running():
                # In an already running event loop, schedule as task
                future = asyncio.ensure_future(res)
                # We can't await synchronously in a running loop without blocking,
                # but we can attach a done callback:
                def on_done(fut):
                    try:
                        fut.result()
                        database.update_alert_status(
                            alert_id, "sent", datetime.now(timezone.utc).isoformat(), db_path=db_path
                        )
                    except Exception as ex:
                        logger.error(f"Async publish failed: {ex}")
                        database.update_alert_status(alert_id, "failed", db_path=db_path)

                future.add_done_callback(on_done)
                return True
            else:
                loop.run_until_complete(res)

        now = datetime.now(timezone.utc).isoformat()
        database.update_alert_status(alert_id, "sent", now, db_path=db_path)
        return True
    except Exception as e:
        logger.warning(f"Failed to publish alert {alert_id}: {e}")
        database.update_alert_status(alert_id, "failed", db_path=db_path)
        return False
