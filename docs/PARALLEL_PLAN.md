# BudgetPilot — Two-Developer Parallel Build

> **Specification:** [`DESIGN.md`](./DESIGN.md)  
> **Completion checklist:** [`TODO.md`](./TODO.md)  
> **Timebox:** 3–4 hours, with both developers working concurrently.

## 1. Ownership

| Area | Dev 1 — Local app and financial correctness | Dev 2 — DNotifier and invoice interpretation |
|---|---|---|
| Foundation | Project setup, dependencies, config, SQLite, Streamlit startup | Real SDK authentication, AI/image capability verification, WS smoke test |
| Transactions | Money parsing, CSV validation/import, duplicate review | Optional AI category suggestions only after core gates pass |
| Budgets | Monthly budgets, deterministic statistics, dashboard | Grounded monthly analysis prompt and validated AI response |
| Invoices | Review UI, duplicate matching, paid/unpaid persistence, link/create transaction | Digital PDF extraction, DNotifier-first image processing, validated invoice draft, optional local OCR fallback |
| Alerts | Before/after threshold rules, durable event state, retries, UI | SDK publisher, separate live subscriber, proof of delivery |
| Verification | Financial/import/persistence tests and integrated app demo | Adapter/extraction tests and live SDK evidence |

**Single writer per file.** Dev 2 supplies callable services; Dev 1 wires them into Streamlit and persistence. Dev 2 must not write financial records or recalculate authoritative totals.

### File ownership

Paths below follow the suggested layout in `DESIGN.md`; agree on the actual project root before starting.

**Dev 1 owns:**
- `app.py`, `config.py`, `database.py`, `models.py`, `money.py`
- `csv_import.py`, `finance.py`, `notifications.py` (rules and durable event lifecycle)
- `sample_data/`, including synthetic CSVs and receipt fixtures
- `tests/test_finance.py`, `tests/test_import.py`, `tests/test_invoices.py`, `tests/test_alerts.py`
- `requirements.txt`, `.env.example`, `.gitignore`, `README.md`

**Dev 2 owns:**
- `ai_service.py`, `invoice_extract.py`, `prompts.py`, `watch_alerts.py`
- `dnotifier_adapter.py` — narrow SDK transport/auth/response normalization, including AI image input and WS publishing
- `tests/test_ai_service.py`, `tests/test_invoice_extract.py`, `tests/test_dnotifier_adapter.py`
- `docs/INTEGRATION_STATUS.md` — installed SDK version, verified methods/payload shapes, live results, blockers; no secrets or raw payloads

Dev 2 sends dependency/config/schema needs to Dev 1 rather than editing shared foundation files. Dev 1 owns updates to `TODO.md` and the final README; each developer reports completed checklist items and evidence at checkpoints.

## 2. Shared contract — agree in the first 15 minutes

Use the existing contracts in `DESIGN.md`; do not build an extra framework. Dev 1 publishes the shared Pydantic models early, using Dev 2's input where needed.

| Boundary | Contract |
|---|---|
| Financial statistics → AI | `AIService.analyze_month(computed_stats: dict) -> dict`, async; statistics and result match the design's monthly contracts. No raw CSV or AI-computed definitive totals. |
| Digital PDF → parser | `invoice_extract.py` extracts digital text; `AIService.extract_invoice(extracted_text: str) -> dict`, async, returns the validated invoice draft contract. |
| Image → parser | `AIService.extract_invoice_image(image_bytes: bytes, mime_type: str) -> dict`, async, returns the same draft contract. Scanned PDF pages are rendered locally; all SDK image specifics stay in the adapter. |
| Optional fallback | Image processing tries DNotifier first. Dev 2 exposes the selected processing path and any failure separately from the draft. Local OCR is optional; manual review remains available without AI/OCR. |
| Invoice draft → storage | Dev 1 validates edited fields, converts decimal strings using `Decimal` to integer minor units, checks duplicates, and commits only after explicit confirmation. |
| Threshold event → publisher | `DNotifierAdapter.publish_alert(payload: dict) -> None`, async; uses the structured payload in design section 6. Raises on send failure; Dev 1 retains pending/failed state. A successful send is not proof of subscriber delivery. |
| Failure → UI | Agree on recoverable exception types for unavailable capability, transport/auth failure, and invalid AI output. Dev 1 displays an actionable error and preserves editable input; no silent success or DB write. |

Dev 2 owns the Streamlit-safe invocation wrapper for async service calls; Dev 1 owns UI lifecycle and calls the agreed wrapper. Agree on this once rather than implementing competing event-loop strategies.

Invoice processing must report one of **digital text**, **DNotifier image**, **local OCR fallback**, or **manual**. Missing/ambiguous fields stay missing until reviewed. Do not invent a multimodal SDK format to satisfy this interface.

## 3. Parallel execution schedule

### 0:00–0:25 — Foundation and SDK gate (P0)

**Dev 1**
- Bootstrap dependencies/config, `.env.example`, SQLite, shared models, and a runnable Streamlit shell.
- Establish minor-unit money helpers and agree on repository functions needed by invoice/alert flows.
- Supply a synthetic receipt image/PDF and the statistics payload shape to Dev 2.

**Dev 2**
- Begin with real DNotifier authentication and one real `send_ai` call.
- Inspect actual response shapes; verify image support with the synthetic receipt.
- Establish adapter interfaces and try a real WS sender/subscriber pair.
- Report exact dependency/env requirements and blockers immediately.

**Checkpoint 1:** App boots; boundary contracts are agreed; live AI/image status is explicit. If credentials or image support are blocked, Dev 1 continues local work while Dev 2 documents the blocker and implements honest failure/manual-fallback behavior. Do not substitute another AI provider.

### 0:25–1:15 — Independent implementation (P1 + service portion of P2/P3)

**Dev 1**
- Complete CSV preview, validation, explicit import, provenance, duplicate checks, budgets, and two-month fixtures.
- Implement financial statistics and unit tests without AI dependencies.

**Dev 2**
- Implement monthly analysis and invoice prompts, adapter response normalization, and schema validation.
- Implement digital PDF text extraction and DNotifier-first image processing.
- Test malformed responses, missing fields, unavailable image capability, and optional OCR/manual fallback routing.

**Checkpoint 2:** Two CSVs produce tested statistics; Dev 2 services accept those statistics and fixture invoices. Test doubles are allowed in unit tests, but never count as live integration evidence.

### 1:15–2:05 — First integrated AI path (P2)

**Dev 1**
- Finish dashboard KPIs/charts and wire monthly analysis into Streamlit.
- Save report snapshots and invalidate reports on transaction/budget changes.
- Start invoice review form and persistence/linking logic.

**Dev 2**
- Support the real monthly-analysis integration and resolve SDK-specific failures.
- Finish invoice draft processing and expose processing/failure status for the UI.
- Deliver working WS publisher/subscriber services.

**Checkpoint 3:** CSV → dashboard → real DNotifier monthly review works, or the live blocker is explicitly recorded. Invoice services are ready for UI wiring.

### 2:05–3:00 — Invoice and alert integration (P3/P4)

**Dev 1**
- Wire invoice upload, editable fields, paid/unpaid choice, duplicate candidates, and link/create confirmation.
- Prove unpaid/linked invoices do not increase spend; new paid invoices increase it exactly once.
- Implement before/after threshold detection, unique alert records, and pending/sent/failed lifecycle; invoke Dev 2's publisher after a valid expense commit.

**Dev 2**
- Run the separate subscriber and validate real delivery during integration.
- Fix image/parser/publisher integration issues only within owned files.
- Add optional local OCR only if needed and time permits; do not delay manual fallback or core gates for it.

**Checkpoint 4:** A reviewed paid invoice changes totals and crosses a threshold; a separate subscriber receives the real event. Record image-processing evidence separately from text-parser and notification evidence.

### 3:00–4:00 — QA and handoff (P5)

**Dev 1**
- Run the full suite and fresh-DB startup; exercise duplicate import/invoice, unpaid invoice, staleness, and AI downtime paths.
- Finish README setup/demo commands and update the canonical checklist with verified completions.

**Dev 2**
- Recheck real AI, image processing, and WS delivery; document exact SDK methods, actual results, unsupported features, and blockers.
- Verify secrets/raw receipt payloads are absent from logs and source; provide subscriber/demo instructions to Dev 1.

**Together:** Run the minimum end-to-end demo once from a clean DB. Do not add stretch features while any core gate is unresolved.

## 4. Mapping back to TODO.md

| Checklist section | Lead | Partner contribution |
|---|---|---|
| P0 Foundation/integration gate | Split by file ownership | Dev 1 setup; Dev 2 all live SDK verification |
| P1 Money/schema/CSV | Dev 1 | Dev 2 none required |
| P2 Dashboard/analyst | Dev 1 integration | Dev 2 prompt, AI service, validation, live AI proof |
| P3 Invoice ingestion | Dev 1 review/save | Dev 2 extraction/image processing/fallback, service tests |
| P4 Notifications | Dev 1 rules/state | Dev 2 publisher/subscriber and live-delivery proof |
| P5 QA/demo | Dev 1 handoff | Dev 2 integration evidence and SDK limitations |
| Stretch | Only after all core gates pass | Agree ownership before starting |

The canonical checklist remains authoritative. This plan changes execution order only by allowing independent service work in parallel; it does not waive any gate or mark planned work complete.

## 5. Integration rules and stop condition

- Use separate branches/checkouts if available; neither developer edits the other's files without an explicit handoff. Exchange changes at each checkpoint rather than waiting until the final hour.
- Dev 1 integrates foundation/contracts first, Dev 2 services second, then UI wiring; agree schema/signature changes before implementation.
- No live DNotifier calls in the default unit-test run. Mark/gate live smoke tests explicitly; report them separately from `pytest -q` results.
- Cut polish, stretch features, workflow wrappers, and optional local OCR first. Never cut deterministic arithmetic, review-before-save, duplicate protection, or honest status reporting.
- **Stop when:** fresh startup, two-month import/budgets, real monthly AI review, reviewed invoice with correct totals, and real separate-subscriber alert are verified; image processing is either genuinely verified or explicitly listed as blocked. A blocked image gate is not a completed feature.
