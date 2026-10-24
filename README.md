# BudgetPilot — DNotifier Hackathon MVP

BudgetPilot is a monthly expense and budget tracking assistant built for the **DNotifier Hackathon**. It combines deterministic financial arithmetic in Python with grounded monthly AI reviews and real-time threshold notifications.

---

## Key Features

1. **Deterministic Financial Engine**:
   - Zero floating-point drift: all monetary values are parsed with `Decimal` and stored in integer minor units (1 PKR = 100 paisa).
   - Strict transaction semantics: `net_expenses = sum(expenses) - sum(refunds)`. Transfers and income are tracked separately.
   - Guarded Month-over-Month (MoM) calculations with division-by-zero protection.

2. **Idempotent CSV Import**:
   - Schema validation for `date, description, amount` (optional: `category, type`).
   - File SHA-256 deduplication prevents accidental duplicate file re-imports.
   - Row-level provenance (`import_id`, `source_row`) stored in SQLite.

3. **Invoice & Receipt Review Workflow**:
   - Digital text extraction via PyMuPDF for PDFs.
   - Clear processing status reporting (`digital_text`, `dnotifier_image`, `local_ocr_fallback`, `manual`).
   - Pre-commit editable review form.
   - Paid vs Unpaid status: unpaid invoices are recorded as commitments only (0 change to actual spend).
   - Duplicate transaction candidate matching: choose to **Link to existing transaction** (0 change to spend) or **Create new expense**.

4. **DNotifier AI Grounded Monthly Review**:
   - Never sends raw CSV bank dumps to the model; submits small, structured Python-computed statistics.
   - Schema-validated output with up to 3 evidence-backed findings and 3 actionable recommendations.
   - Automatic staleness detection: reports are marked **Outdated** when new transactions or budget edits occur.

5. **Durable Budget Threshold Notifications**:
   - Detects when category spend crosses **80%** or **100%** of monthly limits.
   - DB uniqueness constraint: `UNIQUE(month, category, threshold)` ensures each alert is queued at most once.
   - Persisted lifecycle: `pending` -> `sent` / `failed`.

---

## Runtime & Installation

The application requires **Python 3.10.x** and uses `constraints.txt` for verified compatibility.

```bash
# 1. Clone the repository and enter directory
cd budget-tracker-ai-1

# 2. Create Python 3.10 virtual environment
python3.10 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies with constraints applied
pip install --upgrade pip
pip install -r requirements.txt

# 4. (Optional) Configure environment variables for live DNotifier integration
cp .env.example .env
# Edit .env with your DNOTIFIER_APP_ID and DNOTIFIER_SECRET
```

---

## Running the Application

### 1. Launch the Streamlit Dashboard
```bash
source .venv/bin/activate
streamlit run app.py
```
Open `http://localhost:8501` in your browser.

### 2. Run the Notification Subscriber (Optional for live WS demo)
```bash
source .venv/bin/activate
python watch_alerts.py
```

---

## 90-Second Demo Walkthrough

1. **Boot from Clean Database**:
   - Launch `streamlit run app.py`. Notice the app initializes tables cleanly.
2. **Import Spending History**:
   - Go to the **📥 Import CSV** tab.
   - Click **Download Sep 2026 Sample** and **Download Oct 2026 Sample**.
   - Upload `expenses_2026_09.csv`, preview the validated rows, and click **Confirm and Commit Import**.
   - Upload `expenses_2026_10.csv` and commit.
   - Try re-uploading `expenses_2026_10.csv` again: observe the **Duplicate File** rejection banner preventing double-counting.
3. **Configure Budgets**:
   - In the sidebar, expand **Edit 2026-10 Budgets**.
   - Set **Dining** limit to `10000` (Rs 10,000) and **Groceries** to `20000` (Rs 20,000). Click **Save Category Budgets**.
4. **Inspect Overview & KPIs**:
   - Switch to the **📊 Overview & KPIs** tab.
   - Observe the **Top Over-Budget Category** card highlighting Dining (+Rs 7,200 overrun, 172% used).
   - See the category budget progress bars color-coded (red for >100% overrun).
5. **Add Receipt via Invoices Tab**:
   - Switch to the **🧾 Invoices & Receipts** tab.
   - Upload `sample_data/sample_invoice.pdf` (or `sample_data/sample_receipt.png`).
   - Note the **Processing Path** badge (`digital_text`).
   - Review the extracted fields (Al-Fatah Department Store, Rs 7,906, Groceries, Paid).
   - Click **Proceed to Match & Save**.
   - Notice the duplicate check runs; click **Finalize and Save Receipt**.
   - Notice a **Budget Alert** banner appears if thresholds are crossed!
6. **Verify Totals & Stale Status**:
   - Return to the **Overview** tab: observe net spending increased by exactly Rs 7,906.
   - Notice the banner now warns: **⚠️ Report Outdated: New financial data has been added**.
7. **Run Monthly AI Review**:
   - Switch to the **🤖 Monthly AI Insights** tab.
   - Click **🚀 Analyze Month with DNotifier AI**.
   - View the grounded findings (citing exact figures like Rs 17,200 spent on Dining vs Rs 10,000 budget) and actionable recommendations.

---

## Running Automated Tests

Run the full unit and integration test suite:

```bash
source .venv/bin/activate
pytest -q
```

### Test Coverage Summary:
- `tests/test_finance.py`: Net expenses, refund offsets, income separation, transfer exclusion, MoM division-by-zero prevention, top expenses.
- `tests/test_import.py`: CSV parsing, ISO date validation, negative amount rejection, file SHA-256 idempotency, row provenance.
- `tests/test_invoices.py`: Unpaid invoice zero-spend invariant, paid linked invoice duplicate prevention, paid unmatched expense creation.
- `tests/test_alerts.py`: 80% and 100% threshold crossings, multi-threshold single transactions, duplicate alert prevention, durable status transitions.
