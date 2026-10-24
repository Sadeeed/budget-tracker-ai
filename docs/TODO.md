# BudgetPilot — Coding Agent TODO / Execution Checklist

> **Objective:** Implement the BudgetPilot MVP in **3–4 hours** for **Best Use of DNotifier**.  
> **Canonical specification:** [`DESIGN.md`](./DESIGN.md)  
> **Primary demo:** CSV spending history → monthly budgets → AI analysis → invoice upload → alert.  
> **Deliverable:** Executable Streamlit app, SQLite DB, sample data, tests, DNotifier AI + messaging demo.

## Instructions for OpenCode / Codex

- **Implement the application.** Do not merely produce another plan, generic scaffold, or mock screenshots.
- Use the implementation contract in `DESIGN.md`. Work through the checklist **top to bottom**. Run tests after each logical phase.
- Start with a **DNotifier credential + AI smoke test**. The hackathon provides an API key, but the SDK documentation says `app_id` and `secret`; establish the real credentials. Never invent authentication methods.
- The app should still handle local imports/budget arithmetic even if DNotifier is temporarily unavailable. Never fake a successful AI response or notification.
- Use **synthetic sample data** and do not include any private financial data or credentials in git.
- Build for a **single demo user**, PKR currency, and one standard CSV schema. Keep changes small; don't build a general fintech platform.
- Check off tasks here when implemented, with brief notes for any deviations or blockers.

## P0 — Foundation and integration gate (0:00–0:25)

- [ ] Initialize Python project, `.gitignore`, `.env.example`, `requirements.txt`, `README.md`.
- [ ] Create `DNOTIFIER_APP_ID`, `DNOTIFIER_SECRET`, and demo identity variables; no hardcoded secrets.
- [ ] Install the Python SDK and test real AI auth with `DNotifier(transport="http")`, `connect()`, `send_ai(...)`, `disconnect()`.
- [ ] Inspect and document **actual AI response shape** in the adapter; check auth/AI availability via SDK where supported.
- [ ] Verify DNotifier image-processing support, actual SDK payload format, and account/model availability with a real synthetic receipt image; document results or blockers without inventing image parameters.
- [ ] Test a basic `transport="ws"` sender/subscriber pair with `send()` if time; if not, put the messaging smoke test first in P4.
- [ ] Start `streamlit run app.py` and confirm the app bootstraps with no existing DB.

**Gate:** If AI calls fail due to credential formatting, provider setup, or plan status, keep building the deterministic app but document the exact blocker—do not substitute another provider without authorization.

## P1 — Money, schema, and CSV import (0:25–1:15)

- [ ] Create `database.py` and tables from `DESIGN.md`; use transactions for batch imports.
- [ ] Use integer minor units for PKR amount storage and `Decimal` for parsing, never float.
- [ ] Define category enum and `expense/refund/income/transfer` semantics.
- [ ] Provide month selector, overall monthly budget, and per-category budget editor.
- [ ] Implement CSV upload with required `date,description,amount`; `category,type` optional.
- [ ] Validate dates, amounts, types, and categories; show editable rows with per-row errors.
- [ ] Confirm import only on explicit user action; persist `imports.file_sha256` and per-row provenance.
- [ ] Prevent exact same-file reimport and warn on likely cross-file duplicates instead of silently dropping.
- [ ] Add synthetic fixtures for **two months** with clear variance (e.g. dining over budget).
- [ ] Test amount precision, per-month attribution, imports, refunds, and income exclusions.

**Gate:** Importing sample CSVs produces deterministic, verified totals without calling AI.

## P2 — Budget calculations, dashboard, DNotifier analyst (1:15–2:05)

- [ ] Implement `finance.py`: monthly net spend, income, category spent, remaining, overage, and percentages.
- [ ] Compute MoM absolute/percentage changes only when previous-month data is present and denominator > 0.
- [ ] Render overview KPIs, category progress bars, and category breakdown chart.
- [ ] Create prompt in `prompts.py` for grounded monthly budget analysis.
- [ ] Implement DNotifier AI adapter that submits **computed aggregate stats**, not the full CSV.
- [ ] Validate analysis JSON with Pydantic; recover gracefully from invalid/non-JSON model outputs.
- [ ] Show max three significant findings, max three actionable recommendations, and limitations.
- [ ] Persist analysis and its stats snapshot. Mark report **outdated** when budget or transaction data changes.
- [ ] Test that the report never fabricates MoM comparison if history isn't present.

**Gate:** A user can import CSV → see charts → click **Analyze month** → see a **real DNotifier-generated** useful report.

## P3 — Invoice / receipt ingestion (2:05–3:00)

- [ ] Add PDF invoice upload; extract digital text with PyMuPDF.
- [ ] Add PNG/JPG and rendered scanned-PDF pages using **DNotifier as the primary image processor**, behind the SDK adapter and using the verified image payload format.
- [ ] Parse DNotifier image results or sanitized digital-PDF text into the same validated invoice JSON fields (merchant/date/total/invoice number/category/confidence).
- [ ] Only if DNotifier image processing is unsupported, unavailable, or insufficient, optionally use local OCR → DNotifier text parsing. Do not require Tesseract or any local OCR for the primary path; retain manual entry when extraction/AI is unavailable.
- [ ] Display the actual processing path and failure status; never describe local OCR/manual fallback as successful DNotifier image processing.
- [ ] Show **editable review form**. If merchant/date/total are unknown, require the user to enter them.
- [ ] Let user mark invoice **paid** or **unpaid**; unpaid is a commitment, not actual spend.
- [ ] Check for duplicate/related CSV rows using normalized vendor, exact amount, date tolerance and invoice number.
- [ ] Provide **Link existing transaction** vs **Create new expense** selection for potential matches.
- [ ] Confirmed, unmatched paid invoice creates exactly one expense; linked invoice does not double-count.
- [ ] Invalidate previously generated monthly report when a new expense is committed.
- [ ] Add tests for unpaid invoice and linked paid invoice arithmetic.
- [ ] Test DNotifier-first image routing, optional local OCR fallback, and manual review when AI/local OCR is unavailable; no writes before confirmation.

**Gate:** Demo image invoice is processed through a verified real DNotifier image call, reviewed, and reflected in correct budget totals without double counting. If image support is blocked, record the blocker and label any local OCR/manual fallback honestly; it does not prove the DNotifier image gate.

## P4 — DNotifier threshold notifications (3:00–3:30)

- [ ] Implement threshold detector based on **before/after** category spend across transaction commit.
- [ ] Add DB uniqueness guard for 80% and 100% threshold notifications by month/category.
- [ ] Implement DNotifier WS sender to `budgetpilot-user-demo` with structured alert payload.
- [ ] Create `watch_alerts.py` WS subscriber that prints received alerts under the user identity.
- [ ] Persist status `pending/sent/failed`; allow failed sends to retry without duplicating thresholds.
- [ ] Display locally persisted alerts in the overview panel.
- [ ] Demonstrate a newly added paid invoice crossing at least one threshold.
- [ ] Test that re-imports/re-renders don't resend old alerts.

**Gate:** Separate subscriber sees **actual real-time DNotifier message** after a confirmed expense crosses a threshold.

## P5 — QA, demonstration, handoff (3:30–4:00)

- [ ] Run `pytest -q`, fix highest-impact failures, and verify startup from fresh DB.
- [ ] Test with September and October synthetic CSV data.
- [ ] Test receipt confirmation + duplicate matching + alert, then request an updated analysis.
- [ ] Validate no secrets in source, logs, or Streamlit widgets.
- [ ] Write README setup (`pip install -r requirements.txt`; `.env`; run Streamlit; run subscriber), verified DNotifier image support, and optional local OCR fallback setup only if implemented.
- [ ] Add a short **90-second demo script** to README.
- [ ] Record any SDK quirks, unsupported features, known limitations and suggested follow-up work.
- [ ] Ensure DNotifier workflow observability is visible if it was implemented; otherwise explain that the MVP uses direct `send_ai` and `send` calls.

## Stretch (ONLY after all gates pass)

- [ ] Categorize unknown CSV rows in AI batches and allow edits.
- [ ] Add `Workflow` + `define_agent` with `observability=True` for analysis.
- [ ] Add what-if spending reduction slider with **Python-computed**, not model-generated, impact.
- [ ] Expand optional local OCR fallback coverage beyond tested receipt-image/scanned-PDF fixtures, only if needed.
- [ ] Export AI report as Markdown.
- [ ] Basic recurring merchant/subscription identification.

## Manual demo acceptance checklist

- [ ] Clean install and launch succeeds.
- [ ] Two CSV files import without double counting.
- [ ] Current month shows actual/budget/category numbers consistent with fixture.
- [ ] AI generates a monthly review based on real calculated figures, including at least one actionable recommendation.
- [ ] PDF invoice values are confirmed by a user before saving.
- [ ] Image/scanned receipt uses real DNotifier image processing first; any unsupported capability or local OCR/manual fallback is clearly identified.
- [ ] Duplicate invoice can be linked instead of added twice.
- [ ] A previously unmet category threshold triggers a DNotifier message in another process.
- [ ] Unpaid invoice does not inflate actual spending.
- [ ] UI shows stale AI report after financial edits.
- [ ] Application remains usable when AI service is unavailable.

## Cut order if behind schedule

1. Remove UI polish, fancy charts, AI category batching, exports, and what-if simulator.
2. Cut optional local OCR fallback before the primary DNotifier image path. If DNotifier image support is blocked, document it and restrict invoices to **digital PDFs + editable manual fallback** rather than claiming image integration succeeded.
3. Keep one demo month + one comparison month rather than adding full historical analysis.
4. Keep direct DNotifier AI + WS messaging, skip multi-agent workflow wrappers if they block progress.
5. **Never cut financial arithmetic correctness, review-before-save, or honest integration status.**

## Agent's final response should include

- What was built and what was intentionally omitted.
- How to run the app and the notification subscriber.
- Which DNotifier SDK methods were genuinely exercised.
- Test results (actual commands and pass/fail).
- Outstanding blockers, including authentication problems and unverified API features.
