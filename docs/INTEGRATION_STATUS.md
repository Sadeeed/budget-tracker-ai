# DNotifier integration status

## Integrated Dev 1 + Dev 2 verification (2026-10-24 — current)

This section supersedes historical statements below that UI wiring is unfinished.
Image capability remains blocked/unverified; no new image-success claim is made.

### Corrected design deviations and boundary mismatches

- `app.py` now constructs `AIService(DNotifierAdapter.from_environment())`, calls
  Dev 2's `run_async` wrapper, and routes explicit upload processing through
  `process_invoice`. No invented receipt values or default receipt date/total.
- AI schemas now accept Dev 1's `overall_percent_used` and largest-expense
  `category`, retaining strict validation rather than dropping financial facts.
- The publisher receives only the agreed structured alert fields. The first
  integrated live attempt exposed that Dev 1's extra `message` field was rejected
  by Dev 2's strict transport validator. It was removed, regression-tested against
  that validator, and the live demo rerun successfully. The failed attempt is not
  counted as delivery proof.
- Credentials display **configured**, not connected. AI errors are recoverable;
  no new local fallback report is saved. Legacy reports are preserved and labelled
  `legacy_unverified` through an additive SQLite migration.
- Report fingerprints include budgets and both current/prior-month transaction
  facts. CSV amounts stay strings until Decimal conversion; categories/edited
  values require validation, invalid rows stay editable, and cross-file duplicate
  warnings require explicit acknowledgement.
- Import provenance, expense writes and resulting pending alerts are atomic.
  Invoice writes validate required fields, guard repeated file/merchant-number
  identity, and validate links. Paid linked/unpaid receipts do not add spending.
- Threshold comparisons use integer cross-products. Alert records retain their
  original budget; explicit retries use Dev 2's async wrapper and never resend
  an already-sent record on an ordinary rerun. Sender success is not a delivery ACK.
  Legacy alerts without original budget snapshots cannot safely be resent.

### Actual verification

Python **3.10.22**, `.venv`, root constraints applied:

```bash
.venv/bin/python -m pip install -c constraints.txt -r requirements.txt
.venv/bin/python -m pip check
.venv/bin/streamlit skills --yes
.venv/bin/python -m pytest -q
.venv/bin/python dnotifier_adapter.py --smoke-ai
.venv/bin/python ai_service.py --smoke-month
.venv/bin/python invoice_extract.py --smoke-invoice
.venv/bin/python scripts/smoke_demo.py --live
```

- Installation and dependency check passed. Previously missing local dependencies
  were installed, including python-dateutil (now explicitly declared).
- Official Streamlit skills installed and loaded with best-practices,
  session-state and testing references; installation was not treated as usage.
- **131 offline tests passed**, including fresh-DB AppTest startup, real local PDF
  extraction, service-schema compatibility, UI CSV and receipt confirmation,
  linking/unpaid invariants, upload-state resets, failure recovery, duplicate
  protection, schema migration, atomic rollback, snapshots and async alert retries.
  Default tests use explicit SDK doubles, never live credentials as success proof.
- Live AI smoke authenticated, AI enabled, requested marker verified;
  normalized success remains `data.content: str`.
- Monthly service smoke exited 0 with grounded synthetic monetary figures.
- Invoice service smoke exited 0: all five source comparisons passed, including
  digital PDF extraction, grand total selection and preserved missing dates.
- **Integrated live headless demo exited 0.** Fresh database:
  `/tmp/opencode/budgetpilot-demo-yeiyahkq/demo.db`. Two CSVs were imported using
  actual Streamlit commit actions, a real monthly report saved, a synthetic digital
  PDF interpreted, and the editable draft checked against source before explicit
  review/finalize actions. Exactly **85000 paisa (Rs 850)** was added. A durable
  Groceries 100% event used the actual SDK WebSocket publisher and was matched
  against receipt by a **separate authenticated subscriber process**. The old
  report became stale and a second real analysis saved a current report.
  Three real AI requests; **only the file chooser was injected**, not services,
  PDF extraction, SDK responses, arithmetic, persistence or subscriber traffic.
  The demo uses its own temporary DB, not the user's local database.

### Remaining limits

Manual/browser usability acceptance is not claimed by headless AppTest proof.
Image processing stays unverified; image/scanned-PDF routing exposes manual
fallback, not successful vision. Local OCR, category AI batching and workflow
observability remain intentionally omitted; direct SDK `send_ai`/`send` only.
Grounding checks are bounded guards, not complete semantic verification of prose.
Network failures after an uncertain send may require a manual retry and can cause
duplicate delivery; there is no exactly-once receiver acknowledgement protocol.

---

## Live invoice text/PDF service verification (latest)

Executed under Python 3.10 with the configured `open_ai/gpt-4o-mini`:

```bash
.venv/bin/python invoice_extract.py --smoke-invoice
```

Exit **0**; all five explicit checks passed across **three real AI requests**:

1. Direct text draft matched synthetic source merchant, invoice `DEMO-1042`,
   date `2026-10-12`, currency PKR, Groceries category, and total `4850.00`.
2. An in-memory digital PDF passed through actual PyMuPDF text extraction and
   `process_invoice`, producing the same correct draft via **digital text**.
3. The parser chose **grand total Rs 4,850.00**, not subtotal Rs 4,500.00 or tax
   Rs 350.00 (source-checked by both complete-document comparisons).
4. A separate incomplete receipt retained `date=null` and `invoice_number=null`
   and listed both missing fields; it did not substitute today's date.
5. The PDF result retained `requires_review=True`.

These are source-checked service results, not merely schema-validation success.
The missing-fields response reported high confidence despite incomplete fields;
confidence is advisory only and never overrides review/required-field checks.
No invoice/expense records were written, no raw uploads were persisted, and
image/OCR processing was not used. All fixture content is synthetic.

The probe is explicit opt-in and not called by the default test suite. Two new
offline tests verify that its comparison fails on a subtotal substituted for the
grand total, using doubles only. Full UI review/save, duplicate matching,
paid/unpaid totals, durable alerts, and fresh-DB demo remain integration work.
Image verification stays deferred as requested.

## Live monthly-analysis service verification (latest)

Executed `.venv/bin/python ai_service.py --smoke-month` with the configured
`open_ai/gpt-4o-mini`. This opt-in command constructs synthetic aggregate facts
using Python integer sums and invokes the actual service, isolated SDK worker,
response normalizer, and report validators. It does not import CSVs or write DB
records. Image verification remains deferred by the user.

Initial live runs exposed a real grounding defect: the model relabelled paisa as
rupees, overstating amounts 100-fold, despite the original unit instruction.
One run also invented a consistent-spending pattern from one month. Those runs
are **not** counted as acceptable monthly reviews.

Corrective changes: Python now formats monetary values into Rs strings before
sending the aggregate view (e.g. `spent_minor=120000` becomes
`spent_pkr="Rs 1,200.00"`). The public input/storage statistics contract remains
integer minor units, unchanged. Prompts prohibit unsupported recurrence claims.
A deterministic output check rejects `Rs`-prefixed amounts absent from the input
monetary facts, including the observed 100-fold error. This guard checks amount
membership, not complete semantic attribution or every currency notation.

The subsequent live request exited **0** and returned a validated report with:

- Dining: **Rs 1,200.00 spent vs Rs 1,000.00 budget**.
- Groceries: **Rs 800.00 spent vs Rs 1,000.00 budget**.
- Overall expenses equal the **Rs 2,000.00** budget.
- Explicit unavailable previous-month comparison limitations.
- Recommendations to track Dining and plan Groceries spending.

The figures and overall/category distinction were manually checked against the
synthetic Python input. This verifies one live monthly service report, not the
full CSV → database → Streamlit path or general model reliability. The advice
is still basic; fixture-specific success does not guarantee every future report.
Next service gate: real invoice text/PDF interpretation. Then Dev 1 integration
and an expense-triggered subscriber demo remain.

## Live image-format experiment (latest, 2026-10-24)

Found an official payload example outside GitBook in DNotifier's
[Gemini multimodal article](https://www.dnotifier.com/blog/gemini-multimodal-edge-explained/):
`message={text: ..., attachments: [{type: "image", url: photoUrl}]}`.
This documents attachment field names for Gemini, not OpenAI compatibility or
inline data URL support. The PyPI-linked GitHub repository tree request returned
404; the Python changelog supplied no additional AI-image contract.

Added an explicit `--smoke-image` CLI experiment in `dnotifier_adapter.py`.
It renders a synthetic receipt locally with PyMuPDF. A fresh random code and
total appear **only in the PNG**, not the textual prompt. The PNG is supplied
using the documented attachment structure with an **experimental** inline
`data:image/png;base64,...` URL; support for that URL scheme is the hypothesis,
not an asserted documented feature. No image was uploaded to public hosting,
no OCR was used, and SDK logging/history were disabled.

Executed real HTTP calls:

```bash
.venv/bin/python dnotifier_adapter.py --smoke-image
DNOTIFIER_MODEL=gpt-4o .venv/bin/python dnotifier_adapter.py --smoke-image
```

Both `open_ai/gpt-4o-mini` and `open_ai/gpt-4o` returned parsed JSON with null
code/total: `image_facts_verified=false`, `model_reported_no_readable_image=true`.
Both probes exited **1**. This candidate did **not** deliver verifiable readable
image facts. It does not establish whether attachments were ignored, inline URLs
were unsupported, or OpenAI routing lacks multimodal support. It also does not
test publicly hosted HTTPS images or Gemini. Production image support remains
disabled and manual fallback remains honest.

Next: obtain DNotifier confirmation for OpenAI attachment handling and inline
image URLs, or a documented upload mechanism. A publicly reachable synthetic
image could separately test HTTPS attachments; no such hosting was provisioned.
Do not switch providers or upload user receipts publicly as a workaround.

## Image-support verification attempt (2026-10-24)

**Result: blocked/unverified, not proven unsupported. No live image request was
sent because no supported AI-image payload contract was found.**

Checked official documentation:

- [AI overview](https://dnotifier.gitbook.io/product-docs/ai/overview)
- [Python chat messages](https://dnotifier.gitbook.io/product-docs/ai/chat-style-messages/python)
- [Types and payloads](https://dnotifier.gitbook.io/product-docs/reference/types-and-payloads)
- [Python structured payloads](https://dnotifier.gitbook.io/product-docs/realtime-communication/structured-payloads/python)

The documented image example is `send(..., data={"type": "image", "content": ...})`
for peer messaging, not AI interpretation. Documented AI shapes cover simple text
and role-based messages with string content; they do not specify image URLs,
base64 inputs, or multimodal content blocks. SDK 1.0.4 runtime inspection found
no public image/vision/multimodal method. `_build_ai_send_data` forwards arbitrary
messages and injects provider/model overrides; this alone establishes neither
backend multimodal acceptance nor a supported image format.

Per the project's no-speculative-SDK rule, no guessed OpenAI-style image blocks
or realtime image fields were sent to `send_ai`. Obtain DNotifier's supported
AI-image schema/account capability confirmation, then verify it using a synthetic
receipt with image-only facts. Existing manual fallback remains active. Changing
to a vision-capable model alone would not verify DNotifier's image transport.

Rechecked `.venv/bin/python -m pytest -q`: **89 passed** under Python 3.10.

## Latest update — verified OpenAI models (2026-10-24)

### Selected project default

Configured the ignored project `.env` with `DNOTIFIER_PROVIDER=open_ai` and
`DNOTIFIER_MODEL=gpt-4o-mini`. Mini is the cost/latency-oriented MVP choice for
bounded JSON invoice extraction and short reviews of Python-computed statistics;
authoritative financial arithmetic remains outside the model. Both candidates
passed connectivity probes, but task-quality/latency/cost comparisons have not
been measured. Reconsider `gpt-4o` if representative task evaluations show
insufficient extraction or grounded-report quality. No automatic model fallback
was added. This selection does not verify DNotifier image support.

**The basic live AI gate now passes.** Both requests below authenticated with
AI enabled and returned the requested synthetic `{"ok":true}` content, verified
by the probe (not merely a nonempty error envelope). Both exited **0**:

```bash
DNOTIFIER_PROVIDER=open_ai DNOTIFIER_MODEL=gpt-4o .venv/bin/python dnotifier_adapter.py --smoke-ai
DNOTIFIER_PROVIDER=open_ai DNOTIFIER_MODEL=gpt-4o-mini .venv/bin/python dnotifier_adapter.py --smoke-ai
```

Observed success shape for both: `metadata` dictionary (one field), `data`
dictionary (five fields), and **`data.content: str`** containing the verified
JSON reply. No credentials or raw response envelopes were printed. Overrides
were process-only; `.env` was not modified. Successful smoke replies do not yet
prove a real monthly report, invoice interpretation, or image processing.

The user's spelling `openi_ai` was corrected to the official documented provider
**`open_ai`**; documented model IDs are **`gpt-4o`** and **`gpt-4o-mini`**.

### Available-model discovery

- Official [OpenAI integration](https://dnotifier.gitbook.io/product-docs/ai/multi-models/open-ai-integration)
  directs users to **Projects → AI Studio → Providers → OpenAI** to see the
  connection's available model list.
- The [multi-model reference](https://dnotifier.gitbook.io/product-docs/ai/multi-models)
  says model IDs must match that portal connection. It does not document a
  programmatic list-models operation.
- The [Python AI-method reference](https://dnotifier.gitbook.io/product-docs/reference/ai-methods/python)
  does not expose a model-listing method. Runtime inspection of DNotifier 1.0.4's
  class found only `get_plan_limits` among model/provider/plan-related methods;
  no public `list_models` / provider-discovery method was found. Auth's
  `current_model` is a default selection, not an available-model inventory.
- No undocumented endpoint or direct upstream API was guessed or called.

**Earlier provider/model blockers below are historical and superseded by this
update.** Image support and full app integration remain unresolved.

## Step 1 — SDK gate (2026-10-24)

**Status: live authentication verified; useful AI reply blocked on provider selection.
Image processing remains unverified. Real separate-process WS delivery is now verified
(see the Dev 2 implementation section).**

### Verified locally

- Interpreter: existing `.venv`, CPython **3.10.22** on Linux.
- Installed SDK: **dnotifier 1.0.4**, using root `constraints.txt`.
- The venv initially lacked pip. Installed SDK and pip using:
  `uv pip install --python .venv/bin/python -c constraints.txt dnotifier pip`.
- `.venv/bin/python -m pip check`: **passed**, no broken requirements.
- Added `python-dotenv==1.2.4` for project-local credential loading:
  `uv pip install --python .venv/bin/python -c constraints.txt python-dotenv`.
  Dev 1 must include this dependency in the shared requirements at handoff.
- `.venv/bin/python -m unittest discover -s tests -v`: **7 offline tests passed**.
  Test doubles are not live evidence. Full app dependency/startup verification
  and `pytest -q` have not been performed in this step.

### Installed SDK surface inspected

- Constructor requires `app_id`, `secret`, `transport`, and `user_id`.
- `connect()` authenticates and requires a token in the authentication response.
- `send_ai(sender_id, message, save_history=True, session_id=None,
  provider=None, model=None)` supports HTTP RPC.
- **Important:** `send_ai` catches exceptions, prints the raw exception, and
  returns `None`. The probe suppresses SDK stdout and treats `None` as failure.
- `_normalize_rpc_response` recognizes a `metadata`/`data` envelope directly or
  inside a `response` dictionary. This is source inspection, **not an observed
  live success response shape**. A live error envelope is described below.
- `send(sender_id, data, receiver_id=None, receiver_ids=None,
  save_history=True)` is present. A returned WS send ID does not prove delivery.
- `disconnect()` is present; the probe invokes it in a `finally` block.
- AI message construction forwards arbitrary message data when no provider/model
  override is supplied. This does **not** establish a supported image schema or
  account/model capability. No image payload has been invented or sent.

### Initial credential blocker (resolved)

Initially there was no project `.env` file, and the executable smoke probe found neither
required credential in its process environment:

```console
.venv/bin/python dnotifier_adapter.py --smoke-ai
{"status": "blocked", "reason": "Both DNOTIFIER_APP_ID and DNOTIFIER_SECRET are required."}
```

Exit status: **1**. No authentication or AI network request was attempted.
An API key alone has not been assumed equivalent to `app_id` plus `secret`.

### Live result after adding project credentials

The user supplied an ignored `.env` with both required keys. EnvSitter verified
valid syntax and nonempty values without exposing them. The CLI now loads this
file through `python-dotenv`, with existing process variables taking precedence.

Real `connect()`, `send_ai(...)`, and `disconnect()` calls were exercised.
Authentication succeeded and `ai_enabled` was **true**. The live RPC returned:

- `metadata`: dictionary with one field (values withheld).
- `data`: dictionary with two fields, including `message: str`.
- Sanitized diagnostic message keywords: **provider**, **required**.
- Requested synthetic `{"ok":true}` reply: **not received**.
- Probe exit status: **1**, intentionally not counted as successful AI inference.

The probe now checks the reply marker: merely receiving a nonempty backend error
envelope must not count as live AI success. No raw response values or secrets
were printed or retained.

Official [multi-model documentation](https://dnotifier.gitbook.io/product-docs/ai/multi-models)
requires configuring the connection under **Projects → AI Studio → Providers**,
then selecting its `provider` and `model`. Although those docs describe a project
default when overrides are omitted, this application's live request required a
provider. No provider/model was guessed and no portal configuration was changed.

### User-requested OpenAI / sol trial

Ran one real synthetic request with process-only overrides (no `.env` changes):

```bash
DNOTIFIER_PROVIDER=open_ai DNOTIFIER_MODEL=sol .venv/bin/python dnotifier_adapter.py --smoke-ai
```

Authentication succeeded and AI remained enabled. The returned message contained
the diagnostic keywords **model**, **not**; the requested `{"ok":true}` reply was
absent. Exit status: **1**. This did not verify `sol` as a usable DNotifier model
or establish successful inference. Select a model ID actually available to the
configured OpenAI connection; the coding-session model name is not proof of a
matching upstream API model ID.

### Safe next action

Set `DNOTIFIER_PROVIDER` and `DNOTIFIER_MODEL` in the project's `.env` to the
actual configured connection's provider string and model ID. The CLI forwards
these using the installed SDK's verified keyword parameters. These are not
upstream provider secrets; keep upstream API keys in DNotifier's portal.

Then run:

```bash
.venv/bin/python dnotifier_adapter.py --smoke-ai
```

Once the documented credentials are available, run in a private terminal:

```bash
.venv/bin/python dnotifier_adapter.py --smoke-ai --prompt-credentials
```

Both inputs are hidden and never saved by this command. Alternatively, supply
`DNOTIFIER_APP_ID` and `DNOTIFIER_SECRET` through the process environment.
Optional `DNOTIFIER_AI_USER_ID` defaults to `budgetpilot-ai-demo`.
The command automatically loads only the project-local `.env`. Do not paste credentials
into chat, source code, or shell command arguments.

The probe opts into one synthetic AI request with `save_history=False` and SDK
logging disabled. It prints a bounded type/known-envelope-field description,
not response values, credentials, tokens, or raw provider errors. Receiving an
envelope alone does not establish a valid analysis or invoice response.
The probe's stdout redirection is CLI-only and must not be used as a concurrent
Streamlit invocation strategy.

### Remaining step-1 gates

1. Authentication is verified. Obtain a real AI reply after selecting the
   configured provider/model; inspect its sanitized success structure.
2. Verify the actual image input contract and account/model support; use a real
   synthetic receipt call. Dev 1 owns receipt fixtures.
3. **Verified below:** real WS sender/subscriber transport. Expense-triggered
   threshold delivery still awaits Dev 1's persistence/rules integration.
4. Agree application service signatures, error contracts, and async UI wrapper
   with Dev 1 before UI wiring.

Dev 1 retains ownership of dependency/config/shared-model files and `docs/TODO.md`.
No canonical checklist items were marked complete by Dev 2.

## Dev 2 implementation and handoff (2026-10-24)

### Delivered within owned files

- `dnotifier_adapter.py`: environment-backed construction, HTTP JSON requests,
  documented content-envelope normalization, timeouts, safe recoverable errors,
  explicit unavailable image capability, WS publishing, and subscriber support.
- `ai_service.py`: `AIService.analyze_month(computed_stats)` and both invoice
  interpretation methods; strict Pydantic contracts, bounded aggregate/document
  inputs, best-effort PII redaction, max-three findings/recommendations, and
  explicit missing-history/budget limitations. Null invoice values remain null.
- `prompts.py`: grounded analysis and untrusted-document extraction prompts.
- `invoice_extract.py`: actual PyMuPDF digital-text extraction and bounded
  single-page scan rendering; image-first service routing and manual recovery
  with `processing_path`, `attempted_path`, `failure`, `draft`, and
  `requires_review=True`. Uploads are not persisted.
- `watch_alerts.py`: separate subscriber, optional bounded/one-message modes,
  and an opt-in synthetic end-to-end transport smoke command.
- Tests cover schema failures, malformed JSON, missing fields, no-history and
  no-budget guards, real local PDF extraction/rendering, image/manual routing,
  encrypted/corrupt uploads, integer alert payloads, WS auth acknowledgement,
  async invocation, and child-process cleanup on failure/cancellation.

### Live messaging evidence

Executed under Python 3.10.22:

```bash
.venv/bin/python watch_alerts.py --smoke-delivery
```

Actual result:

```json
{"synthetic_transport_smoke": true, "separate_subscriber_received": true, "event_id": "849b7a2e-745f-4e00-bf96-8a0c9523ccfb", "expense_committed": false}
```

Exit status **0**. A separate receiver process authenticated as
`budgetpilot-user-demo`; the sender used `budgetpilot-service`. The receiver
validated the actual payload and matched its unique event ID. Both used the
installed SDK's real WebSocket transport. `connect()`, `send()`, the inbound
`on_message` / `payload.to_json()` path, and `disconnect()` were exercised.

SDK `connect()` returns before WS authentication acknowledgement, so the adapter
waits for `on_connected` before sending. This is a real delivery proof, **not**
proof that a persisted invoice crossed a deterministic threshold. The synthetic
probe makes no database changes and uses `save_history=False`.

### Live service failure evidence

The implemented `AIService.extract_invoice` was also invoked through the async
wrapper and isolated SDK worker using only synthetic receipt text. It returned
the safe application exception:

```text
InvalidAIOutput: No AI content returned. Check DNotifier's configured provider/model, then retry.
```

This confirms recoverable live backend failure handling, **not** successful
invoice interpretation. The user's `open_ai` / `sol` trial remains unsuccessful;
no alternative model or provider was silently selected.

### Dev 1 integration contract

```python
from ai_service import AIService
from dnotifier_adapter import DNotifierAdapter, DNotifierError, run_async
from invoice_extract import process_invoice

adapter = DNotifierAdapter.from_environment()
service = AIService(adapter)

# Call on explicit UI actions; catch DNotifierError to retain editable input.
report = run_async(service.analyze_month, computed_stats)
result = run_async(process_invoice, uploaded_bytes, mime_type, service)

# Only after Dev 1 commits the expense and records the durable pending alert:
run_async(adapter.publish_alert, structured_alert_payload)
```

`run_async` takes an async callable plus its arguments, not a coroutine or cached
SDK client. It works with or without an already-running loop. SDK objects are
created and closed per operation, never cached across reruns. Because SDK 1.0.4
prints raw caught AI exceptions, app-facing SDK calls run in an isolated child
process with credentials sent over stdin (not arguments). The child captures SDK
stdout and returns only normalized content or fixed application errors; the app
does not redirect global stdout. This adds process-startup overhead intentionally
to protect Streamlit logs/threads. The subscriber is standalone, not an app task.

Recoverable exception types are `DNotifierError` (base), `TransportError`,
`InvalidAIOutput`, and `CapabilityUnavailable`. No provider exception text or
Pydantic input dump is propagated to the UI.

Dev 1 must still validate edited receipt values, convert decimal strings using
its money helpers, resolve duplicates/linking, distinguish paid/unpaid, commit
records, invalidate reports, and evaluate/persist/retry threshold events. A
successful publisher call means **sent**, not a per-event delivery acknowledgement.
There are no automatic publisher retries here, so Dev 1 retains retry ownership.

The current validation schemas live privately in `ai_service.py` because the
shared `models.py` does not exist yet. Coordinate consolidation with Dev 1;
these services do not take ownership of shared financial models or calculations.

### Dependency handoff (Dev 1 owns requirements/constraints)

Installed with the Python 3.10 interpreter and root constraints:

```bash
uv pip install --python .venv/bin/python -c constraints.txt pydantic PyMuPDF pytest
```

Actual versions: `pydantic==2.14.1`, `PyMuPDF==1.28.2`, `pytest==9.1.1`.
Also required: `dnotifier==1.0.4` and `python-dotenv==1.2.4` installed earlier.
Include these in the shared dependency setup, with `-c constraints.txt`.
The implementation uses Pydantic **v2** APIs.

### Run and verification commands

```bash
# Normal separate subscriber; leave running during the app demo.
.venv/bin/python watch_alerts.py

# Receive one real valid alert and exit (timeout is optional).
.venv/bin/python watch_alerts.py --once --timeout 60

# Opt-in real synthetic transport check using a separate receiver process.
.venv/bin/python watch_alerts.py --smoke-delivery

# Default suite is offline and never reads project credentials.
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check
```

Latest verification: **89 tests passed**, dependency check passed, all under
Python **3.10.22**. No Streamlit UI was implemented/reviewed/tested in this work;
app startup and fresh-DB acceptance remain Dev 1's work.

### Honest limits and remaining gates

- A successful real monthly analysis / text invoice reply remains blocked on a
  usable model and configured DNotifier provider. Service unit tests use explicit
  doubles only, never default/mock success in the application.
- Image payload/account support remains **unverified**. The image adapter raises
  `CapabilityUnavailable`; the router exposes manual fallback, not image success.
  It does not fabricate a multimodal payload or send receipt images via a guessed
  API. A verified implementation and real image call are still required.
- Optional local OCR, CSV AI categorization, and workflow observability are
  intentionally omitted; core services use direct SDK calls.
- PDF uploads are limited to 10 MiB, 1–4 pages, and 16,000 digital-text characters.
  Single-page scans are rendered at bounded resolution. Multi-page scanned/mixed
  PDFs require manual review rather than silently dropping pages or combining
  potentially different totals. Encrypted/malformed PDFs also recover to manual.
- PII redaction is best-effort text redaction, not comprehensive name/address
  detection or image-region redaction. Use synthetic demo documents.
- Narrative grounding guards catch obvious missing-budget/history claims and
  unknown categories; they are **not a proof that all model prose/numbers are
  correct**. Dev 1 must display computed supporting facts alongside the report.
- No UI, SQLite, invoice commit, financial arithmetic, duplicate matching, report
  staleness, or durable threshold lifecycle has been implemented by Dev 2. None
  of their full-product acceptance gates is claimed complete by this handoff.
