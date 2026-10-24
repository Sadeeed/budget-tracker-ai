"""Narrow SDK boundary and explicit, opt-in live authentication/AI probe.

Run ``python dnotifier_adapter.py --smoke-ai`` with credentials supplied in
the process environment or the project-local dotenv file, or add
``--prompt-credentials`` for private terminal entry. No financial records are written.
"""

import argparse
import asyncio
from contextlib import redirect_stdout
from getpass import getpass
import io
import json
import logging
import os
from pathlib import Path
import re
import sys
from concurrent.futures import ThreadPoolExecutor

from dnotifier import DNotifier
from dotenv import load_dotenv


class DNotifierError(RuntimeError):
    """Recoverable SDK failure; deliberately excludes raw provider messages."""


class CapabilityUnavailable(DNotifierError):
    """An account or SDK capability is not verified/available."""


class InvalidAIOutput(DNotifierError):
    """AI returned no usable, schema-valid JSON."""


class TransportError(DNotifierError):
    """Authentication, timeout, or transport failure; safe to show in the UI."""


def run_async(async_function, *args, **kwargs):
    """Sync UI boundary; create SDK/process resources inside the new loop.

    Pass a callable, not an existing coroutine or loop-bound SDK client.
    Does not import Streamlit or retain resources across Streamlit reruns.
    """
    def run():
        return asyncio.run(async_function(*args, **kwargs))

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return run()
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(run).result()


class DNotifierAdapter:
    def __init__(self, app_id: str, secret: str, timeout: float = 30.0,
                 provider: str = None, model: str = None,
                 ai_user_id: str = "budgetpilot-ai-demo",
                 sender_id: str = "budgetpilot-service",
                 receiver_id: str = "budgetpilot-user-demo"):
        if not app_id or not secret:
            raise DNotifierError("Both DNOTIFIER_APP_ID and DNOTIFIER_SECRET are required.")
        self._app_id = app_id
        self._secret = secret
        self._timeout = timeout
        self._provider = provider
        self._model = model
        self._ai_user_id = ai_user_id
        self._sender_id = sender_id
        self._receiver_id = receiver_id

    @classmethod
    def from_environment(cls):
        load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
        return cls(
            os.getenv("DNOTIFIER_APP_ID", ""), os.getenv("DNOTIFIER_SECRET", ""),
            provider=os.getenv("DNOTIFIER_PROVIDER") or None,
            model=os.getenv("DNOTIFIER_MODEL") or None,
            ai_user_id=os.getenv("DNOTIFIER_AI_USER_ID", "budgetpilot-ai-demo"),
            sender_id=os.getenv("DNOTIFIER_SENDER_ID", "budgetpilot-service"),
            receiver_id=os.getenv("DNOTIFIER_RECEIVER_ID", "budgetpilot-user-demo"),
        )

    async def request_json(self, prompt: str) -> dict:
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 24000:
            raise InvalidAIOutput("AI input must be nonempty and at most 24,000 characters.")
        return await self._worker("ai", prompt)

    async def extract_invoice_image(self, image_bytes: bytes, mime_type: str) -> dict:
        # An application-owned method is not evidence of SDK multimodal support.
        raise CapabilityUnavailable(
            "DNotifier image payload/account support is unverified. Use digital PDF text or manual review."
        )

    async def publish_alert(self, payload: dict) -> None:
        validate_alert(payload)
        await self._worker("publish", payload)

    async def subscribe_alerts(self, on_alert, on_ready, timeout=None,
                               once=False, event_id=None) -> None:
        """Standalone CLI subscriber only; do not run inside Streamlit.

        The CLI owns stdout isolation. Only validated budget alert fields reach
        its callback; unrelated/malformed messages are ignored.
        """
        done = asyncio.Event()
        received = False

        def message_received(message):
            nonlocal received
            try:
                payload = message.payload.to_json()
                if isinstance(payload, str):
                    payload = json.loads(payload)
                alert = validate_alert(payload)
                if event_id is not None and alert.get("event_id") != event_id:
                    return
            except (DNotifierError, ValueError, TypeError, AttributeError):
                return
            received = True
            on_alert(alert)
            if once:
                done.set()

        notifier = DNotifier(
            app_id=self._app_id, secret=self._secret, transport="ws",
            user_id=self._receiver_id, logs=False, on_message=message_received,
            on_disconnected=lambda **kwargs: done.set(),
        )
        try:
            await connect_ws(notifier, self._timeout)
            on_ready()
            await asyncio.wait_for(done.wait(), timeout)
            if not (once and received):
                raise TransportError("DNotifier subscriber disconnected. Restart it to receive new alerts.")
        except DNotifierError:
            raise
        except asyncio.TimeoutError:
            raise TransportError("No matching alert arrived before the subscriber timeout.") from None
        except Exception:
            raise TransportError("DNotifier subscriber failed. Check credentials/connectivity and retry.") from None
        finally:
            try:
                await asyncio.wait_for(notifier.disconnect(), self._timeout)
            except Exception:
                pass

    async def _worker(self, operation: str, payload):
        # SDK 1.0.4 prints raw caught errors. Isolate it in a child process rather
        # than redirecting process-wide stdout in a running Streamlit app.
        request = {
            "operation": operation, "payload": payload,
            "app_id": self._app_id, "secret": self._secret,
            "timeout": self._timeout, "provider": self._provider,
            "model": self._model, "ai_user_id": self._ai_user_id,
            "sender_id": self._sender_id, "receiver_id": self._receiver_id,
        }
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable, str(Path(__file__).resolve()), "--worker",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(
                process.communicate(json.dumps(request).encode()), self._timeout * 3 + 5)
            if process.returncode != 0 or len(stdout) > 131072:
                raise TransportError("DNotifier worker failed; retry or check account configuration.")
            envelope = json.loads(stdout)
            if not envelope.get("ok"):
                errors = {"InvalidAIOutput": InvalidAIOutput,
                          "CapabilityUnavailable": CapabilityUnavailable}
                error_type = errors.get(envelope.get("error_type"), TransportError)
                # Worker returns fixed application messages only, never provider text.
                raise error_type(envelope["error"])
            return envelope.get("result")
        except (DNotifierError, asyncio.CancelledError):
            raise
        except Exception:
            raise TransportError("DNotifier request failed or timed out. Retry after checking connectivity.") from None
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()

    async def probe_ai(self, user_id: str = "budgetpilot-ai-demo",
                       provider: str = None, model: str = None) -> dict:
        """Return only response structure, never response values or auth tokens."""
        notifier = DNotifier(
            app_id=self._app_id,
            secret=self._secret,
            transport="http",
            user_id=user_id,
            logs=False,
        )
        stage = "authentication"
        # SDK 1.0.4 prints caught send_ai errors. Discard those messages: they
        # are not a safe user-facing error channel. Use only in a CLI probe,
        # since redirect_stdout is process-wide, not a Streamlit strategy.
        with redirect_stdout(io.StringIO()):
            try:
                await asyncio.wait_for(notifier.connect(), self._timeout)
                if not notifier.is_connected:
                    raise DNotifierError("Authentication did not establish a connection.")
                stage = "AI request"
                response = await asyncio.wait_for(
                    notifier.send_ai(
                        sender_id=user_id,
                        message={"text": 'Synthetic connectivity check. Reply only with {"ok":true}.'},
                        save_history=False,
                        provider=provider,
                        model=model,
                    ),
                    self._timeout,
                )
                if response is None:
                    raise DNotifierError("SDK returned no AI response; live AI is not verified.")
                try:
                    marker_verified = has_smoke_marker(parse_ai_json(response))
                except DNotifierError:
                    marker_verified = False
                return {
                    "authenticated": True,
                    "ai_enabled": bool(notifier.ai_enabled),
                    "ai_response_received": True,
                    "ai_marker_verified": marker_verified,
                    "diagnostics": response_diagnostics(response),
                    "response_shape": response_shape(response),
                }
            except DNotifierError:
                raise
            except Exception as exc:
                raise DNotifierError(
                    f"DNotifier {stage} failed ({type(exc).__name__}); raw error suppressed."
                ) from None
            finally:
                try:
                    await asyncio.wait_for(notifier.disconnect(), self._timeout)
                except Exception:
                    pass

    async def probe_image_format(self) -> dict:
        """Opt-in experiment; does NOT enable production image support.

        Official attachment structure: DNotifier's Gemini multimodal article.
        Whether an inline data URL works with OpenAI is the hypothesis tested.
        """
        import base64
        import secrets
        import pymupdf

        token = secrets.token_hex(6).upper()
        total = f"{secrets.randbelow(8000) + 1000}.37"
        with pymupdf.open() as document:
            page = document.new_page(width=600, height=300)
            page.insert_text((40, 60), "SYNTHETIC RECEIPT", fontsize=24)
            page.insert_text((40, 120), f"CODE: {token}", fontsize=24)
            page.insert_text((40, 180), f"TOTAL PKR {total}", fontsize=24)
            image = page.get_pixmap().tobytes("png")
        message = {
            "text": 'Read the attached receipt image. Return JSON only with code and total (decimal string). If no image is accessible return {"code":null,"total":null}. Do not guess.',
            "attachments": [{"type": "image", "url": "data:image/png;base64," + base64.b64encode(image).decode()}],
        }
        notifier = DNotifier(app_id=self._app_id, secret=self._secret,
                             transport="http", user_id=self._ai_user_id, logs=False)
        with redirect_stdout(io.StringIO()):
            try:
                await asyncio.wait_for(notifier.connect(), self._timeout)
                response = await asyncio.wait_for(notifier.send_ai(
                    sender_id=self._ai_user_id, provider=self._provider, model=self._model,
                    message=message, save_history=False), self._timeout)
                try:
                    result = parse_ai_json(response)
                    verified = result.get("code") == token and result.get("total") == total
                    unavailable = result.get("code") is None and result.get("total") is None
                except DNotifierError:
                    verified, unavailable = False, False
                return {"image_facts_verified": verified,
                        "model_reported_no_readable_image": unavailable,
                        "candidate": "attachments/type=image/url=data:image/png;base64",
                        "provider": self._provider, "model": self._model,
                        "diagnostics": response_diagnostics(response),
                        "response_shape": response_shape(response)}
            except Exception:
                raise TransportError("Image format probe failed or timed out; image support is unverified.") from None
            finally:
                try:
                    await asyncio.wait_for(notifier.disconnect(), self._timeout)
                except Exception:
                    pass


def validate_alert(payload: dict) -> dict:
    """Validate transport data only; threshold math and persistence belong to Dev 1."""
    required = {"type", "month", "category", "threshold_percent", "spent_minor", "budget_minor"}
    if not isinstance(payload, dict) or not required <= payload.keys():
        raise DNotifierError("Alert payload is missing required fields.")
    if payload["type"] != "budget_threshold":
        raise DNotifierError("Unsupported alert type.")
    if not isinstance(payload["month"], str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", payload["month"]):
        raise DNotifierError("Alert month must be YYYY-MM.")
    if not isinstance(payload["category"], str) or not 1 <= len(payload["category"]) <= 40:
        raise DNotifierError("Alert category is required.")
    if type(payload["threshold_percent"]) is not int or payload["threshold_percent"] not in (80, 100):
        raise DNotifierError("Alert threshold must be 80 or 100.")
    for key in ("spent_minor", "budget_minor"):
        if type(payload[key]) is not int or payload[key] < 0:
            raise DNotifierError("Alert money fields must be nonnegative integer minor units.")
    allowed = required | {"event_id"}
    if payload.keys() - allowed:
        raise DNotifierError("Alert payload contains unsupported fields.")
    if "event_id" in payload and (not isinstance(payload["event_id"], str) or len(payload["event_id"]) > 80):
        raise DNotifierError("Invalid alert event ID.")
    return dict(payload)


def parse_ai_json(response) -> dict:
    """Normalize documented content envelopes; never interpret an error message as content."""
    if not isinstance(response, dict):
        raise InvalidAIOutput("DNotifier returned an unsupported AI response shape.")
    root = response.get("_responseData", response.get("response", response))
    if not isinstance(root, dict):
        raise InvalidAIOutput("DNotifier returned an unsupported AI response shape.")
    for container in (response, root, root.get("metadata", {}), root.get("data", {})):
        if not isinstance(container, dict):
            continue
        for field in ("code", "statusCode", "responseCode", "_responseCode"):
            code = container.get(field)
            if isinstance(code, int) and code >= 400:
                raise TransportError("DNotifier rejected the AI request. Check provider/model and account configuration.")
        if container.get("success") is False or container.get("status") in ("error", "failed"):
            raise TransportError("DNotifier rejected the AI request. Check provider/model and account configuration.")
    data = root.get("data", root)
    content = None
    for container in (data, root):
        if isinstance(container, dict):
            content = container.get("content", container.get("text"))
            if content is not None:
                break
    if content is None:
        raise InvalidAIOutput("No AI content returned. Check DNotifier's configured provider/model, then retry.")
    if isinstance(content, dict):
        return content
    if not isinstance(content, str) or len(content) > 64000:
        raise InvalidAIOutput("AI content is missing or too large. Retry or use manual review.")
    text = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n?(.*?)\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        result = json.loads(text)
    except (ValueError, TypeError):
        raise InvalidAIOutput("AI did not return valid JSON. Retry or use manual review.") from None
    if not isinstance(result, dict):
        raise InvalidAIOutput("AI must return a JSON object. Retry or use manual review.")
    return result


async def connect_ws(notifier, timeout: float):
    """SDK connect() returns before WS auth acknowledgement; wait for callback."""
    ready = asyncio.Event()
    notifier.on_connected = ready.set
    await asyncio.wait_for(notifier.connect(), timeout)
    await asyncio.wait_for(ready.wait(), timeout)
    if not notifier.is_connected:
        raise TransportError("DNotifier WebSocket authentication did not complete.")


async def _execute_worker(request: dict):
    operation = request["operation"]
    timeout = request["timeout"]
    notifier = DNotifier(
        app_id=request["app_id"], secret=request["secret"],
        transport="http" if operation == "ai" else "ws",
        user_id=request["ai_user_id"] if operation == "ai" else request["sender_id"],
        logs=False,
    )
    try:
        if operation == "ai":
            await asyncio.wait_for(notifier.connect(), timeout)
            if not notifier.is_connected:
                raise TransportError("DNotifier authentication did not complete.")
            if not notifier.ai_enabled:
                raise CapabilityUnavailable("AI is not enabled for this DNotifier account/plan.")
            response = await asyncio.wait_for(notifier.send_ai(
                sender_id=request["ai_user_id"], message={"text": request["payload"]},
                provider=request["provider"], model=request["model"], save_history=False), timeout)
            if response is None:
                raise TransportError("DNotifier returned no response. Check connection/provider configuration.")
            return parse_ai_json(response)
        if operation == "publish":
            validate_alert(request["payload"])
            await connect_ws(notifier, timeout)
            await asyncio.wait_for(notifier.send(
                sender_id=request["sender_id"], receiver_id=request["receiver_id"],
                data=request["payload"], save_history=False), timeout)
            return None
        raise TransportError("Unsupported DNotifier operation.")
    except DNotifierError:
        raise
    except Exception:
        raise TransportError("DNotifier authentication/transport failed. Check credentials and connectivity.") from None
    finally:
        try:
            await asyncio.wait_for(notifier.disconnect(), timeout)
        except Exception:
            pass


def _worker_main() -> int:
    logging.disable(logging.CRITICAL)
    # Child-process-only redirection: never affects the app's stdout or threads.
    with redirect_stdout(io.StringIO()):
        try:
            request = json.loads(sys.stdin.buffer.read(131072))
            result = asyncio.run(_execute_worker(request))
            envelope = {"ok": True, "result": result}
        except DNotifierError as exc:
            envelope = {"ok": False, "error_type": type(exc).__name__, "error": str(exc)}
        except Exception:
            envelope = {"ok": False, "error_type": "TransportError", "error": "DNotifier worker failed."}
    print(json.dumps(envelope))
    return 0


def response_diagnostics(value, depth: int = 0) -> list:
    """Return only fixed error categories and numeric/bool status fields."""
    if depth >= 6:
        return []
    result = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"status", "code", "statusCode", "responseCode", "_responseCode", "success"} and type(item) in {int, bool}:
                result.append({key: item})
            if key == "message" and isinstance(item, str):
                text = item.lower()
                diagnostic_words = {
                    "provider", "model", "default", "configured", "configuration",
                    "required", "missing", "invalid", "not", "found", "enabled",
                    "disabled", "supported", "selected", "available", "no", "set",
                }
                result.append({"message_keywords": sorted(set(re.findall(r"[a-z]+", text)) & diagnostic_words)})
                for category, words in {
                    "provider_or_model": ("provider", "model"),
                    "quota_or_plan": ("quota", "limit", "plan", "credit", "balance"),
                    "permission_or_auth": ("permission", "unauthorized", "forbidden", "auth"),
                    "invalid_input": ("invalid", "required", "missing", "format"),
                    "server_failure": ("error", "failed", "unavailable"),
                }.items():
                    if any(word in text for word in words):
                        result.append({"message_category": category})
            result.extend(response_diagnostics(item, depth + 1))
    elif isinstance(value, list):
        for item in value[:5]:
            result.extend(response_diagnostics(item, depth + 1))
    return result


def has_smoke_marker(value, depth: int = 0) -> bool:
    """Verify the synthetic reply, rather than counting an error envelope as AI."""
    if depth >= 8:
        return False
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("```") and text.endswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            return has_smoke_marker(json.loads(text), depth + 1)
        except (ValueError, TypeError):
            return False
    if isinstance(value, dict):
        if set(value) == {"ok"} and value["ok"] is True:
            return True
        return any(has_smoke_marker(item, depth + 1) for key, item in value.items()
                   if key in {"data", "response", "_responseData", "message", "text", "content", "result", "choices"})
    if isinstance(value, list):
        return any(has_smoke_marker(item, depth + 1) for item in value)
    return False


def response_shape(value, depth: int = 0):
    """Describe types and common envelope fields without exposing arbitrary keys."""
    if depth >= 5:
        return type(value).__name__
    if isinstance(value, dict):
        allowed = {"header", "metadata", "data", "response", "_responseData", "message", "text", "content", "result", "choices"}
        return {
            "type": "dict",
            "field_count": len(value),
            "known_fields": {
                key: response_shape(item, depth + 1)
                for key, item in value.items() if key in allowed
            },
        }
    if isinstance(value, list):
        return {"type": "list", "length": len(value),
                "first_item": response_shape(value[0], depth + 1) if value else None}
    return type(value).__name__


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--smoke-ai", action="store_true",
                        help="Opt in to one live synthetic AI request (may incur usage).")
    mode.add_argument("--smoke-image", action="store_true",
                      help="Test documented attachments with an experimental inline PNG URL; does not enable images.")
    parser.add_argument("--prompt-credentials", action="store_true")
    args = parser.parse_args()
    # CLI only: prevent third-party HTTP/debug logs from revealing auth data.
    logging.disable(logging.CRITICAL)
    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
    app_id = os.getenv("DNOTIFIER_APP_ID", "")
    secret = os.getenv("DNOTIFIER_SECRET", "")
    if args.prompt_credentials:
        app_id = app_id or getpass("DNotifier application ID (hidden): ")
        secret = secret or getpass("DNotifier application secret (hidden): ")
    try:
        adapter = DNotifierAdapter(app_id, secret,
                                  provider=os.getenv("DNOTIFIER_PROVIDER") or None,
                                  model=os.getenv("DNOTIFIER_MODEL") or None)
        if args.smoke_image:
            result = asyncio.run(adapter.probe_image_format())
            print(json.dumps(result, indent=2))
            return 0 if result["image_facts_verified"] else 1
        result = asyncio.run(adapter.probe_ai(
            user_id=os.getenv("DNOTIFIER_AI_USER_ID", "budgetpilot-ai-demo"),
            provider=os.getenv("DNOTIFIER_PROVIDER") or None,
            model=os.getenv("DNOTIFIER_MODEL") or None,
        ))
    except DNotifierError as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}))
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result["ai_marker_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(_worker_main() if sys.argv[1:] == ["--worker"] else main())
