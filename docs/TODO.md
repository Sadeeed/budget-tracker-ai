# BudgetPilot — Coding Agent TODO / Execution Checklist

> **Objective:** Implement the BudgetPilot MVP in **3–4 hours** for **Best Use of DNotifier**.  
> **Canonical specification:** [`DESIGN.md`](./DESIGN.md)  
> **Primary demo:** CSV spending history → monthly budgets → AI analysis → invoice upload → alert.  
> **Deliverable:** Executable Streamlit app, SQLite DB, sample data, tests, DNotifier AI + messaging demo.
> **Two-developer execution:** [`PARALLEL_PLAN.md`](./PARALLEL_PLAN.md) — file ownership, shared contracts, and integration checkpoints.

## Instructions for OpenCode / Codex

- **Implement the application.** Do not merely produce another plan, generic scaffold, or mock screenshots.
- Use the implementation contract in `DESIGN.md`. Work through the checklist **top to bottom**. Run tests after each logical phase.
- Start with a **DNotifier credential + AI smoke test**. The hackathon provides an API key, but the SDK documentation says `app_id` and `secret`; establish the real credentials. Never invent authentication methods.
- The app should still handle local imports/budget arithmetic even if DNotifier is temporarily unavailable. Never fake a successful AI response or notification.
- Use **synthetic sample data** and do not include any private financial data or credentials in git.
- Build for a **single demo user**, PKR currency, and one standard CSV schema. Keep changes small; don't build a general fintech platform.
- Before any Streamlit implementation/review/testing, load applicable installed Streamlit agent skills through the harness or read their `SKILL.md` instructions. Follow them within MVP scope; do not treat installation alone as usage. If unavailable, record the blocker and use official Streamlit docs.
- Check off tasks here when implemented, with brief notes for any deviations or blockers.
- For two developers, follow `PARALLEL_PLAN.md` ownership and checkpoints. Independent service tasks may run in parallel; keep the gates below as integration acceptance criteria.

## P0 — Foundation and integration gate (0:00–0:25)

- [x] Initialize Python project, `.gitignore`, `.env.example`, `requirements.txt`, `README.md`.
- [x] Create a Python 3.10 virtual environment (`python3.10 -m venv .venv`), confirm its interpreter version, and include `-c constraints.txt` in `requirements.txt`. Install all dependencies under Python 3.10, run `python -m pip check`, and smoke-test imports; document incompatibilities rather than upgrading the Python target.
- [x] After installing Streamlit, discover existing Streamlit agent skills; if missing and supported, install project-local skills with `streamlit skills --yes`. Load applicable skills before UI work and record which were used, or document CLI/skill availability blockers. See the [official skills CLI documentation](https://docs.streamlit.io/develop/api-reference/cli/skills).
- [x] Create `DNOTIFIER_APP_ID`, `DNOTIFIER_SECRET`, and demo identity variables; no hardcoded secrets.
- [x] Install the Python SDK and test real AI auth with `DNotifier(transport="http")`, `connect()`, `send_ai(...)`, `disconnect()`. *(Reverified during integration.)*
- [x] Inspect and document **actual AI response shape** in the adapter; check auth/AI availability via SDK where supported. *(Verified `data.content: str`; see INTEGRATION_STATUS.)*
- [ ] Verify DNotifier image-processing support, actual SDK payload format, and account/model availability with a real synthetic receipt image; document results or blockers without inventing image parameters. *(Dev 2)*
- [x] Test a basic `transport="ws"` sender/subscriber pair with `send()` if time; if not, put the messaging smoke test first in P4. *(Separate-process delivery verified; integrated expense delivery also passed.)*
- [x] Start `streamlit run app.py` and confirm the app bootstraps with no existing DB.

**Gate:** If AI calls fail due to credential formatting, provider setup, or plan status, keep building the deterministic app but document the exact blocker—do not substitute another provider without authorization.

## P1 — Money, schema, and CSV import (0:25–1:15)

User amendment (2026-10-24): the sidebar monthly income is the overall spending
cap; CSV income is retained as history, not added. See the amendment in DESIGN.md.

- [x] Create `database.py` and tables from `DESIGN.md`; use transactions for batch imports.
- [x] Use integer minor units for PKR amount storage and `Decimal` for parsing, never float.
- [x] Define category enum and `expense/refund/income/transfer` semantics.
- [x] Provide month selector, overall monthly budget, and per-category budget editor.
- [x] Replace the overall-cap UI with sidebar monthly income; persist per month, reflect it in income/remaining KPIs and AI statistics without adding imported salary.
- [x] Implement CSV upload with required `date,description,amount`; `category,type` optional.
- [x] Validate dates, amounts, types, and categories; show editable rows with per-row errors.
- [x] Confirm import only on explicit user action; persist `imports.file_sha256` and per-row provenance.
- [x] Prevent exact same-file reimport and warn on likely cross-file duplicates instead of silently dropping.
- [x] Add synthetic fixtures for **two months** with clear variance (e.g. dining over budget).
- [x] Test amount precision, per-month attribution, imports, refunds, and income exclusions.

**Gate:** Importing sample CSVs produces deterministic, verified totals without calling AI.

## P2 — Budget calculations, dashboard, DNotifier analyst (1:15–2:05)

- [x] Implement `finance.py`: monthly net spend, income, category spent, remaining, overage, and percentages.
- [x] Compute MoM absolute/percentage changes only when previous-month data is present and denominator > 0.
- [x] Render overview KPIs, category progress bars, and category breakdown chart.
- [x] Create prompt in `prompts.py` for grounded monthly budget analysis. *(Dev 2, integrated.)*
- [x] Implement DNotifier AI adapter that submits **computed aggregate stats**, not the full CSV. *(Dev 2, integrated; real UI action verified headlessly.)*
- [x] Validate analysis JSON with Pydantic; recover gracefully from invalid/non-JSON model outputs.
- [x] Show significant findings, actionable recommendations, and limitations. User amendment: Insights allows one shared limit of 1–10 (default 3) applied separately to both findings and recommendations, enforced before saving; changes apply only on explicit analysis.
- [x] Persist analysis and its stats snapshot. Mark report **outdated** when budget or transaction data changes.
- [x] Test that the report never fabricates MoM comparison if history isn't present.

Shared-limit amendment verification: Python 3.10.22, `.venv/bin/python -m pytest -q`
passed **158 tests** (including prompt/limit validation for both lists and headless Streamlit
selection of 6, save, display, and over-limit rejection). A real `.venv/bin/python ai_service.py --smoke-month`
request passed before this change; configurable limits were verified offline with doubles,
not claimed as a new live integration test. Loaded Streamlit session-state/testing skills.

**Gate:** A user can import CSV → see charts → click **Analyze month** → see a **real DNotifier-generated** useful report.

## P3 — Invoice / receipt ingestion (2:05–3:00)

- [x] Add PDF invoice upload; extract digital text with PyMuPDF.
- [ ] Add PNG/JPG and rendered scanned-PDF pages using **DNotifier as the primary image processor**, behind the SDK adapter and using the verified image payload format. *(Dev 2)*
- [x] Parse DNotifier image results or sanitized digital-PDF text into the same validated invoice JSON fields (merchant/date/total/invoice number/category/confidence). *(Digital-PDF/text live verification passed; image capability remains blocked.)*
- [ ] Only if DNotifier image processing is unsupported, unavailable, or insufficient, optionally use local OCR → DNotifier text parsing. Do not require Tesseract or any local OCR for the primary path; retain manual entry when extraction/AI is unavailable. *(Dev 2)*
- [x] Display the actual processing path and failure status; never describe local OCR/manual fallback as successful DNotifier image processing.
- [x] Show **editable review form**. If merchant/date/total are unknown, require the user to enter them.
- [x] Let user mark invoice **paid** or **unpaid**; unpaid is a commitment, not actual spend.
- [x] Check for duplicate/related CSV rows using normalized vendor, exact amount, date tolerance and invoice number.
- [x] Provide **Link existing transaction** vs **Create new expense** selection for potential matches.
- [x] Confirmed, unmatched paid invoice creates exactly one expense; linked invoice does not double-count.
- [x] Invalidate previously generated monthly report when a new expense is committed.
- [x] Add tests for unpaid invoice and linked paid invoice arithmetic.
- [x] Test DNotifier-first image routing, optional local OCR fallback, and manual review when AI/local OCR is unavailable; no writes before confirmation.

**Gate:** Demo image invoice is processed through a verified real DNotifier image call, reviewed, and reflected in correct budget totals without double counting. If image support is blocked, record the blocker and label any local OCR/manual fallback honestly; it does not prove the DNotifier image gate.

## P4 — DNotifier threshold notifications (3:00–3:30)

- [x] Implement threshold detector based on **before/after** category spend across transaction commit.
- [x] Add DB uniqueness guard for 80% and 100% threshold notifications by month/category.
- [x] Implement DNotifier WS sender to `budgetpilot-user-demo` with structured alert payload. *(Integrated; expense-triggered delivery passed.)*
- [x] Create `watch_alerts.py` WS subscriber that prints received alerts under the user identity. *(Real separate process verified.)*
- [x] Persist status `pending/sent/failed`; allow failed sends to retry without duplicating thresholds.
- [x] Display locally persisted alerts in the overview panel.
- [x] Notify once per month when overall net spending strictly exceeds sidebar income (including zero); also check saving income below existing spend. Reuse durable alerts, real publisher and explicit retries.
- [x] Demonstrate a newly added paid invoice crossing at least one threshold.
- [x] Test that re-imports/re-renders don't resend old alerts.

**Gate:** Separate subscriber sees **actual real-time DNotifier message** after a confirmed expense crosses a threshold.

## P5 — QA, demonstration, handoff (3:30–4:00)

- [x] Run `pytest -q` under Python 3.10, fix highest-impact failures, and verify startup from fresh DB.
- [x] Test with September and October synthetic CSV data.
- [x] Test receipt confirmation + duplicate matching + alert, then request an updated analysis.
- [x] Validate no secrets in source, logs, or Streamlit widgets.
- [x] Write README setup (`pip install -r requirements.txt`; `.env`; run Streamlit; run subscriber), verified DNotifier image support, and optional local OCR fallback setup only if implemented. *(Image limitation explicitly recorded; no OCR installed.)*
- [x] Add a short **90-second demo script** to README.
- [x] Record any SDK quirks, unsupported features, known limitations and suggested follow-up work.
- [x] Ensure DNotifier workflow observability is visible if it was implemented; otherwise explain that the MVP uses direct `send_ai` and `send` calls. *(Direct calls; workflows omitted.)*

## Stretch (ONLY after all gates pass)

- [ ] Categorize unknown CSV rows in AI batches and allow edits.
- [ ] Add `Workflow` + `define_agent` with `observability=True` for analysis.
- [ ] Add what-if spending reduction slider with **Python-computed**, not model-generated, impact.
- [ ] Expand optional local OCR fallback coverage beyond tested receipt-image/scanned-PDF fixtures, only if needed.
- [ ] Export AI report as Markdown.
- [ ] Basic recurring merchant/subscription identification.

## Manual demo acceptance checklist

Integration evidence (2026-10-24): constrained Python 3.10.22 install and pip check
passed; 131 offline tests passed. `scripts/smoke_demo.py --live` passed a fresh-DB
**headless** Streamlit two-import → live AI → reviewed PDF → persisted paid expense
→ real separate-subscriber delivery → refreshed-report flow. Only the file chooser
was injected. These automated checks do not mark the manual/browser checklist below
complete. DNotifier image support stays blocked/unverified, optional OCR remains
omitted. Earlier Dev 1 completion claims were corrected during integration: invented
receipt defaults/local AI fallback removed, strict service boundaries wired, budget
and comparison staleness fixed, CSV atomicity/review/duplicates and durable retries
regression-tested. Details: `INTEGRATION_STATUS.md` and `tests/test_integration.py`.

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
