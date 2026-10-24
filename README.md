# BudgetPilot

Single-user, PKR budget tracking for the DNotifier hackathon. Python owns financial
facts; DNotifier interprets digital receipt text, reviews aggregate statistics and
sends threshold alerts. All shipped financial data is synthetic.

## Setup (Python 3.10.x only)

```bash
python3.10 -m venv .venv
.venv/bin/python -m pip install -c constraints.txt -r requirements.txt
.venv/bin/python -m pip check
.venv/bin/streamlit skills --yes
```

If `.env` does not already exist, copy `.env.example` to `.env`. Enter the real
`DNOTIFIER_APP_ID` and `DNOTIFIER_SECRET` privately there; a standalone API key is
not assumed interchangeable. Select a provider/model enabled in your project's
DNotifier AI Studio (`open_ai` / `gpt-4o-mini` was verified here). Never commit
credentials. Process environment variables override `.env`.

```bash
# Dashboard
.venv/bin/streamlit run app.py

# Separate terminal: actual alert receiver; leave running during the demo
.venv/bin/python watch_alerts.py
```

The default SQLite file is `budgetpilot.db`. Use `BUDGETPILOT_DB_PATH` to select a
different/fresh file without deleting existing data. Existing schemas receive
additive migrations. Legacy reports are retained as `legacy_unverified` because
their AI/local-fallback origin cannot be reconstructed. Legacy pending alerts
without a budget snapshot are displayed but cannot safely be resent.

## What works

- Import normalized CSVs with `date,description,amount`; `category,type` are
  optional columns. Use exact ISO dates and nonnegative amounts; types are
  `expense`, `refund`, `income`, `transfer`. Edit or explicitly exclude invalid
  rows; missing/unknown categories require review. Amounts remain decimal strings
  until conversion to integer paisa. Financial calculations never use AI totals.
- Exact-file imports are blocked. Potential cross-file duplicates require
  acknowledgement; legitimate repeated purchases are not silently deleted.
  Provenance, transactions and resulting pending alerts commit atomically.
- Set **Monthly income (Rs)** directly in the sidebar and click **Save monthly
  income**. This is the overall spending cap for the selected month. Existing
  overall-cap settings retain their values as monthly income; imported salary
  rows remain ledger history and are **not added** to this setting. Blank clears
  it; zero is a real zero-income limit. Category budgets remain separate.
- Spending, income remaining/overrun and month comparison statistics use Python
  integer paisa. Category percentage alerts require a positive category limit.
  When total net spending is **strictly greater** than monthly income, one overall
  alert is queued for that month (including income zero). Exactly equal does not
  alert. Saving income below existing spending also triggers this notification.
- Explicit DNotifier monthly review: strict response validation, saved supporting
  statistics and AI provenance. Changes to budgets, current transactions or prior
  month transactions mark the report outdated. AI failure never saves a local
  fallback as an AI report. Grounding guards are not proof of every narrative claim.
- Upload a digital PDF and click **Process receipt with DNotifier**. PyMuPDF
  extracts text, DNotifier returns a draft, and editable fields must be reviewed
  before a second explicit save. Missing merchant/date/total are not invented.
  Matching uses amount, merchant similarity, date tolerance and available invoice
  number. Link existing paid expenses without double counting; unpaid invoices
  remain commitments. File hashes and merchant/invoice-number identity prevent
  saving the same identifiable receipt twice, including concurrent submissions.
- A new expense queues 80%/100% crossings in the same DB transaction. Sending
  happens afterward: SDK failures do not undo expenses. Pending/failed events can
  be explicitly retried in Overview; successful events are not resent on rerun.
  Retries use the original budget snapshot. `sent` is sender success, not a
  subscriber-delivery acknowledgement; ambiguous network failures may duplicate
  delivery if manually retried. The subscriber is a separate CLI process.
  Overall income alerts use `category="__overall__"` / `threshold_percent=100` in
  the same structured payload and retry lifecycle; they are not category alerts.

## Honest DNotifier status / limits

Real authentication, `connect()`, HTTP `send_ai()`, digital-PDF/text interpretation,
monthly reviews, WebSocket `send()` and separate-process receipt were exercised.
`disconnect()` runs during cleanup. A headless fresh-DB demo verified two UI CSV
imports → real AI review → real PDF draft → explicit paid confirmation → exactly
Rs 850 additional spend → durable 100% alert received separately → updated review.
Only the test file chooser was injected; no AI/SDK responses were mocked in that
live run. It is not a manual browser-usability acceptance test.

**Image processing remains blocked/unverified.** Prior synthetic image experiments
did not recover image-only facts. PNG/JPG and scanned PDFs route to the DNotifier
image capability first, which currently reports unavailability and preserves
manual entry. They are not claimed as successful image interpretation. No local
OCR, alternate AI provider, CSV AI categorizer or workflow observability is added;
the MVP uses direct `send_ai` and `send` calls. Ordinary local tracking/manual
entry remains usable without AI.

Uploads are not stored; only reviewed metadata is persisted. PDF processing is
bounded to 10 MiB, 1–4 pages and 16,000 text characters; multi-page mixed/scanned
documents require manual review. Text PII redaction is best effort, not production
privacy protection. Use synthetic demo documents. No bank login, payment features
or production authentication are included.

See [design](docs/DESIGN.md), [integration plan](docs/PARALLEL_PLAN.md),
[checklist](docs/TODO.md) and [SDK evidence](docs/INTEGRATION_STATUS.md).

## 90-second demo (allow extra time for live AI latency)

1. Start the separate subscriber and dashboard with a fresh DB. Select `2026-10`.
2. Import `sample_data/expenses_2026_09.csv` and `expenses_2026_10.csv`, reviewing
   and explicitly confirming each. October net spend is **Rs 97,300**; Groceries
   is **Rs 24,000**, Dining **Rs 17,200**. Re-upload October: exact-file rejection.
3. Set Dining to `10000`, Groceries to `30000`. Run **Analyze Month with DNotifier
   AI** and inspect its saved Python supporting facts.
4. Upload `sample_data/sample_invoice.pdf`, click **Process receipt with
   DNotifier**, verify its source values (Rs 7,906, October, Groceries), mark paid,
   submit review, and finalize as a new expense. Net spend becomes **Rs 105,206**.
5. Show the stale-report warning and the Groceries 100% event in the separate
   subscriber. Review again to refresh insights. A linked/unpaid receipt does not
   increase actuals. The PNG fixture needs manual entry, not verified image AI.

For an overall-income demo instead, set sidebar monthly income to **300000** and
import `sample_data/expenses_2026_10_over_300k.csv` in a fresh database. Its spending
totals **Rs 320,000**, exceeding income by **Rs 20,000**; a real subscriber must be
running to observe live delivery. This also works if income is saved after import.

## Verification commands

```bash
# Offline: no live SDK requests, including headless Streamlit regression tests
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check

# Opt-in real calls; synthetic data only (may incur usage)
.venv/bin/python dnotifier_adapter.py --smoke-ai
.venv/bin/python ai_service.py --smoke-month
.venv/bin/python invoice_extract.py --smoke-invoice
.venv/bin/python watch_alerts.py --smoke-delivery

# Integrated headless live demo: three real AI calls plus a persisted-expense
# alert sent to a separate receiver; writes only a new temporary demo database
.venv/bin/python scripts/smoke_demo.py --live
```

Verified on Python **3.10.22**: constrained dependency installation and `pip check`
passed; **139 tests passed**. The integrated live demo exited 0. Streamlit skills
were installed and loaded: developing-with-streamlit with best-practices,
session-state and testing references. A manual browser walkthrough remains open.
