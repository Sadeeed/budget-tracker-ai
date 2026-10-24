"""Separate, real DNotifier subscriber; synthetic delivery probe is explicitly opt-in."""

import argparse
import asyncio
from contextlib import redirect_stdout
from datetime import date
import io
import json
import logging
from pathlib import Path
import sys
from uuid import uuid4

from dnotifier_adapter import DNotifierAdapter, DNotifierError, TransportError


async def smoke_delivery(adapter: DNotifierAdapter) -> dict:
    """Use a distinct receiver process and require a matching unique event ID."""
    event_id = str(uuid4())
    subscriber = await asyncio.create_subprocess_exec(
        sys.executable, str(Path(__file__).resolve()), "--once", "--timeout", "45",
        "--event-id", event_id, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        line = await asyncio.wait_for(subscriber.stdout.readline(), 40)
        ready = json.loads(line)
        if ready.get("status") != "ready":
            raise TransportError("Separate subscriber did not authenticate; delivery is unverified.")
        # Transport-only fixture: no expense was committed and no threshold math
        # is claimed. Production threshold events come from Dev 1's durable rules.
        await adapter.publish_alert({
            "type": "budget_threshold", "month": date.today().strftime("%Y-%m"),
            "category": "Dining", "threshold_percent": 80,
            "spent_minor": 800000, "budget_minor": 1000000, "event_id": event_id,
        })
        output, _ = await asyncio.wait_for(subscriber.communicate(), 50)
        events = [json.loads(line) for line in output.splitlines() if line]
        if subscriber.returncode != 0 or not any(
            event.get("status") == "received" and event.get("alert", {}).get("event_id") == event_id
            for event in events
        ):
            raise TransportError("Send finished but separate-subscriber receipt was not verified.")
        return {"synthetic_transport_smoke": True, "separate_subscriber_received": True,
                "event_id": event_id, "expense_committed": False}
    except DNotifierError:
        raise
    except Exception:
        raise TransportError("Separate-subscriber delivery test failed or timed out.") from None
    finally:
        if subscriber.returncode is None:
            subscriber.kill()
            await subscriber.wait()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Exit after one valid alert.")
    parser.add_argument("--timeout", type=float, help="Optional receive timeout in seconds.")
    parser.add_argument("--event-id", help="Only receive this event (used by smoke test).")
    parser.add_argument("--smoke-delivery", action="store_true",
                        help="Send one synthetic event to a separate subscriber process; no DB writes.")
    args = parser.parse_args()
    if args.timeout is not None and args.timeout <= 0:
        parser.error("--timeout must be positive")
    logging.disable(logging.CRITICAL)
    output = sys.stdout

    def emit(value):
        print(json.dumps(value), file=output, flush=True)

    try:
        adapter = DNotifierAdapter.from_environment()
        # This is a dedicated CLI process, not process-wide redirection in the UI.
        with redirect_stdout(io.StringIO()):
            if args.smoke_delivery:
                result = asyncio.run(smoke_delivery(adapter))
            else:
                asyncio.run(adapter.subscribe_alerts(
                    on_alert=lambda alert: emit({"status": "received", "alert": alert}),
                    on_ready=lambda: emit({"status": "ready"}), timeout=args.timeout,
                    once=args.once, event_id=args.event_id,
                ))
                result = {"status": "finished"}
    except KeyboardInterrupt:
        return 130
    except DNotifierError as exc:
        emit({"status": "failed", "reason": str(exc)})
        return 1
    emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
