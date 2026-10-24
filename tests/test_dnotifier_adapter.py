"""Offline boundary checks; these do not establish live integration."""

import asyncio
import unittest
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from dnotifier_adapter import (
    DNotifierAdapter, DNotifierError, InvalidAIOutput, TransportError,
    CapabilityUnavailable, connect_ws, has_smoke_marker, parse_ai_json,
    response_shape, run_async, validate_alert,
    _execute_worker,
)


class FakeNotifier:
    response = {"header": {}, "data": {"text": '{"ok":true}'}}
    disconnected = False

    def __init__(self, **kwargs):
        self.is_connected = False
        self.ai_enabled = True

    async def connect(self):
        self.is_connected = True

    async def send_ai(self, **kwargs):
        type(self).last_ai_kwargs = kwargs
        return self.response

    async def disconnect(self):
        type(self).disconnected = True


class AdapterTests(unittest.TestCase):
    def test_missing_credentials(self):
        with self.assertRaises(DNotifierError):
            DNotifierAdapter("", "")

    def test_shape_does_not_expose_values_or_arbitrary_keys(self):
        shape = response_shape({"secret-value-as-key": "private", "data": {"text": "private"}})
        self.assertNotIn("private", str(shape))
        self.assertNotIn("secret-value-as-key", str(shape))
        self.assertEqual(shape["known_fields"]["data"]["known_fields"]["text"], "str")

    @patch("dnotifier_adapter.DNotifier", FakeNotifier)
    def test_probe_describes_response_and_disconnects(self):
        FakeNotifier.disconnected = False
        result = asyncio.run(DNotifierAdapter("synthetic-app", "synthetic-secret").probe_ai())
        self.assertTrue(result["ai_response_received"])
        self.assertTrue(result["ai_marker_verified"])
        self.assertTrue(FakeNotifier.disconnected)

    def test_error_envelope_is_not_a_verified_ai_reply(self):
        self.assertFalse(has_smoke_marker({"data": {"message": "AI unavailable"}}))

    def test_fenced_reply_is_verified(self):
        self.assertTrue(has_smoke_marker({"data": {"message": '```json\n{"ok":true}\n```'}}))

    @patch("dnotifier_adapter.DNotifier", FakeNotifier)
    def test_provider_and_model_are_forwarded_without_guessing(self):
        asyncio.run(DNotifierAdapter("synthetic-app", "synthetic-secret").probe_ai(
            provider="synthetic-provider", model="synthetic-model"))
        self.assertEqual(FakeNotifier.last_ai_kwargs["provider"], "synthetic-provider")
        self.assertEqual(FakeNotifier.last_ai_kwargs["model"], "synthetic-model")
        self.assertFalse(FakeNotifier.last_ai_kwargs["save_history"])

    @patch("dnotifier_adapter.DNotifier", FakeNotifier)
    def test_swallowed_sdk_error_is_failure(self):
        with patch.object(FakeNotifier, "response", None):
            with self.assertRaises(DNotifierError):
                asyncio.run(DNotifierAdapter("synthetic-app", "synthetic-secret").probe_ai())
        self.assertTrue(FakeNotifier.disconnected)


@pytest.mark.parametrize("envelope", [
    {"data": {"content": '{"summary":"synthetic"}'}},
    {"content": '{"summary":"synthetic"}'},
    {"_responseData": {"metadata": {}, "data": {"content": '{"summary":"synthetic"}'}}},
    {"response": {"metadata": {}, "data": {"text": '```json\n{"summary":"synthetic"}\n```'}}},
    {"data": {"content": {"summary": "synthetic"}}},
])
def test_documented_content_envelopes_normalize_to_json(envelope):
    assert parse_ai_json(envelope) == {"summary": "synthetic"}


@pytest.mark.parametrize("envelope", [
    None, "not a response", {"data": {"message": "provider required"}},
    {"data": {"content": "not JSON"}}, {"data": {"content": "[]"}},
    {"data": {"content": '{"ok":true} {"another":true}'}},
    {"data": {"content": "x" * 64001}},
])
def test_invalid_ai_envelope_does_not_become_success(envelope):
    with pytest.raises(InvalidAIOutput):
        parse_ai_json(envelope)


def test_error_status_wins_over_apparent_content():
    with pytest.raises(TransportError):
        parse_ai_json({"metadata": {"code": 400}, "data": {"content": '{"ok":true}'}})


def alert():
    return {"type": "budget_threshold", "month": "2026-10", "category": "Dining",
            "threshold_percent": 80, "spent_minor": 800000, "budget_minor": 1000000}


@pytest.mark.parametrize("changes", [
    {"spent_minor": 1.5}, {"spent_minor": True}, {"spent_minor": -1},
    {"budget_minor": "10000"}, {"threshold_percent": 90},
    {"threshold_percent": True}, {"month": "2026-13"},
    {"type": "arbitrary-message"}, {"raw_secret": "do not forward"},
])
def test_alert_validation_preserves_integer_transport_contract(changes):
    payload = alert()
    payload.update(changes)
    with pytest.raises(DNotifierError):
        validate_alert(payload)


def test_alert_validation_returns_copy_without_doing_finance_math():
    payload = alert()
    assert validate_alert(payload) == payload
    assert validate_alert(payload) is not payload


def test_sync_wrapper_works_with_and_without_existing_loop():
    async def service(value):
        return value + 1

    assert run_async(service, 1) == 2

    async def existing_loop():
        return run_async(service, 2)

    assert asyncio.run(existing_loop()) == 3


def test_ws_waits_for_auth_callback_not_just_connect_return():
    class SocketDouble:
        is_connected = False

        async def connect(self):
            def acknowledge():
                self.is_connected = True
                self.on_connected()
            asyncio.get_running_loop().call_later(0.01, acknowledge)

    socket = SocketDouble()
    asyncio.run(connect_ws(socket, 1))
    assert socket.is_connected


def test_ws_missing_auth_acknowledgement_times_out():
    class SocketDouble:
        async def connect(self):
            pass

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(connect_ws(SocketDouble(), 0.01))


def test_unverified_image_capability_is_explicit():
    with pytest.raises(CapabilityUnavailable, match="unverified"):
        asyncio.run(DNotifierAdapter("synthetic", "synthetic").extract_invoice_image(b"image", "image/png"))


def test_worker_uses_stdin_not_argv_for_credentials():
    arguments = []

    class ProcessDouble:
        returncode = 0

        async def communicate(self, data):
            request = json.loads(data)
            assert request["secret"] == "synthetic-secret"
            assert request["operation"] == "ai"
            return json.dumps({"ok": True, "result": {"summary": "synthetic"}}).encode(), b""

    async def create(*args, **kwargs):
        arguments.extend(args)
        return ProcessDouble()

    with patch("dnotifier_adapter.asyncio.create_subprocess_exec", create):
        result = asyncio.run(DNotifierAdapter("synthetic-app", "synthetic-secret").request_json("synthetic prompt"))
    assert result == {"summary": "synthetic"}
    assert "synthetic-secret" not in str(arguments)


def test_publisher_failure_is_raised_for_dev1_to_persist():
    class ProcessDouble:
        returncode = 0

        async def communicate(self, data):
            return json.dumps({"ok": False, "error_type": "TransportError",
                               "error": "DNotifier authentication/transport failed."}).encode(), b""

    async def create(*args, **kwargs):
        return ProcessDouble()

    with patch("dnotifier_adapter.asyncio.create_subprocess_exec", create):
        with pytest.raises(TransportError):
            asyncio.run(DNotifierAdapter("synthetic", "synthetic").publish_alert(alert()))


def test_subscriber_filters_unrelated_messages_and_receives_matching_event():
    received = []
    ready = []

    class SocketDouble:
        def __init__(self, **kwargs):
            self.callback = kwargs["on_message"]
            self.is_connected = False

        async def connect(self):
            self.is_connected = True
            self.on_connected()
            def deliver():
                self.callback(SimpleNamespace(payload=SimpleNamespace(to_json=lambda: {"type": "ignore"})))
                payload = dict(alert(), event_id="synthetic-event")
                self.callback(SimpleNamespace(payload=SimpleNamespace(to_json=lambda: payload)))
            asyncio.get_running_loop().call_soon(deliver)

        async def disconnect(self):
            self.is_connected = False

    with patch("dnotifier_adapter.DNotifier", SocketDouble):
        asyncio.run(DNotifierAdapter("synthetic", "synthetic").subscribe_alerts(
            received.append, lambda: ready.append(True), once=True, timeout=1, event_id="synthetic-event"))
    assert ready == [True] and len(received) == 1
    assert received[0]["event_id"] == "synthetic-event"


@pytest.mark.parametrize("cancelled", [False, True])
def test_failed_or_cancelled_worker_is_reaped(cancelled):
    class ProcessDouble:
        returncode = None
        killed = False
        reaped = False

        async def communicate(self, data):
            if cancelled:
                raise asyncio.CancelledError()
            raise asyncio.TimeoutError()

        def kill(self):
            self.killed = True

        async def wait(self):
            self.reaped = True
            self.returncode = -9

    process = ProcessDouble()

    async def create(*args, **kwargs):
        return process

    with patch("dnotifier_adapter.asyncio.create_subprocess_exec", create):
        with pytest.raises(asyncio.CancelledError if cancelled else TransportError):
            asyncio.run(DNotifierAdapter("synthetic", "synthetic").request_json("synthetic"))
    assert process.killed and process.reaped


@pytest.mark.parametrize("disabled,no_response", [(True, False), (False, True)])
def test_direct_worker_failure_closes_sdk_connection(disabled, no_response):
    class NotifierDouble(FakeNotifier):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.ai_enabled = not disabled

        async def send_ai(self, **kwargs):
            return None if no_response else {"data": {"content": '{"ok":true}'}}

    NotifierDouble.disconnected = False
    request = {
        "operation": "ai", "timeout": 1, "app_id": "synthetic", "secret": "synthetic",
        "ai_user_id": "synthetic-ai", "provider": None, "model": None, "payload": "synthetic",
    }
    with patch("dnotifier_adapter.DNotifier", NotifierDouble):
        with pytest.raises(CapabilityUnavailable if disabled else TransportError):
            asyncio.run(_execute_worker(request))
    assert NotifierDouble.disconnected


def test_numeric_one_is_not_the_boolean_smoke_marker():
    assert not has_smoke_marker({"ok": 1})
