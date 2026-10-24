"""BudgetPilot — Monthly expense and budget tracking assistant.

Built for the DNotifier Hackathon MVP.
Provides deterministic financial tracking, CSV imports, receipt reviews,
grounded AI budget analysis, and real-time threshold notifications.
"""

import json
import hashlib
import io
from datetime import datetime
from typing import List
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
import database
from csv_import import (
    parse_and_validate_csv,
    check_csv_import_status,
    commit_csv_import,
    CSVValidationError,
    find_cross_file_duplicates,
    validate_iso_date,
)
from finance import (
    calculate_monthly_stats,
    get_prior_month_str,
)
from money import format_pkr, minor_to_decimal, parse_to_minor
from models import (
    MonthlyAnalysisReport,
    TransactionType,
)
from notifications import (
    dispatch_alert_sync,
)

from ai_service import AIService
from dnotifier_adapter import DNotifierAdapter, DNotifierError, run_async
from invoice_extract import process_invoice


def publish_alert(payload):
    return DNotifierAdapter.from_environment().publish_alert(payload)


def dispatch_new_alerts(previous_ids):
    for alert in database.get_alerts():
        if alert["id"] not in previous_ids:
            dispatch_alert_sync(alert, publish_alert)


# --- Page Configuration ---
st.set_page_config(
    page_title="BudgetPilot",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Initialize SQLite database
database.init_db()


# --- Helper Functions ---
def get_available_months() -> List[str]:
    """Return available months from DB or current and previous months."""
    db_months = database.get_distinct_months()
    now_month = datetime.now().strftime("%Y-%m")
    prior_month = get_prior_month_str(now_month)
    combined = sorted(set(db_months + ["2026-09", "2026-10", now_month, prior_month]), reverse=True)
    return combined


# --- Sidebar: Navigation, Budgets & Settings ---
st.sidebar.title("🧭 BudgetPilot")
st.sidebar.caption("Deterministic Money. Evidence-Based Insights.")

# DNotifier Status Badge
if config.is_dnotifier_configured():
    st.sidebar.info("DNotifier configured — connection checked on request", icon="🔌")
else:
    st.sidebar.info("○ DNotifier Unconfigured (Local Mode)", icon="ℹ️")

st.sidebar.markdown("---")

# Month Selector
available_months = get_available_months()
default_month_idx = available_months.index(datetime.now().strftime("%Y-%m"))

selected_month = st.sidebar.selectbox(
    "Target Month",
    options=available_months,
    index=default_month_idx,
    help="Select calendar month for tracking and budget analysis",
)

st.sidebar.markdown("### Monthly income")
existing_budgets = database.get_budgets(selected_month)

curr_overall = existing_budgets.get(config.OVERALL_BUDGET_KEY)
new_overall = st.sidebar.text_input(
    f"Monthly income ({config.CURRENCY_SYMBOL})",
    value=str(minor_to_decimal(curr_overall)) if curr_overall is not None else "",
    help="Your overall monthly spending limit. CSV income is not added. Blank clears it; zero is a real zero-income limit.",
    key=f"budget_overall_{selected_month}",
)
if st.sidebar.button("Save monthly income", key="btn_save_overall"):
    try:
        previous_ids = {a["id"] for a in database.get_alerts()}
        if new_overall.strip():
            database.set_budget(selected_month, config.OVERALL_BUDGET_KEY, parse_to_minor(new_overall))
        else:
            database.delete_budget(selected_month, config.OVERALL_BUDGET_KEY)
        dispatch_new_alerts(previous_ids)
        st.rerun()
    except ValueError as exc:
        st.sidebar.error(str(exc))

with st.sidebar.expander(f"Edit {selected_month} Budgets", expanded=False):
    st.markdown("#### Category Limits")
    budget_updates = {}
    for cat in config.STANDARD_EXPENSE_CATEGORIES:
        curr_val = existing_budgets.get(cat)
        val = st.text_input(
            f"{cat} ({config.CURRENCY_SYMBOL})",
            value=str(minor_to_decimal(curr_val)) if curr_val is not None else "",
            key=f"budget_{cat}_{selected_month}",
        )
        budget_updates[cat] = val

    if st.button("Save Category Budgets", key="btn_save_categories"):
        try:
            parsed = {cat: parse_to_minor(val) if val.strip() else None for cat, val in budget_updates.items()}
            for cat, value in parsed.items():
                if value is None:
                    database.delete_budget(selected_month, cat)
                else:
                    database.set_budget(selected_month, cat, value)
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

st.sidebar.markdown("---")
st.sidebar.caption(f"Currency: **PKR** (Minor unit: 1 Rs = 100 paisa)")
st.sidebar.caption(f"Identity: `{config.DNOTIFIER_AI_USER_ID}`")


# --- Main Application Header ---
st.title("BudgetPilot")
st.markdown("Understand your spending with precision. Grounded insights and real-time alerts.")

# Tabs
tab_overview, tab_import, tab_invoices, tab_insights = st.tabs([
    "📊 Overview & KPIs",
    "📥 Import CSV",
    "🧾 Invoices & Receipts",
    "🤖 Monthly AI Insights",
])


# ==========================================
# TAB 1: OVERVIEW & KPIS
# ==========================================
with tab_overview:
    stats = calculate_monthly_stats(selected_month)
    latest_report = database.get_latest_report(selected_month)
    current_revision = database.get_report_revision(selected_month)

    # Report staleness banner
    if latest_report:
        is_stale = latest_report["transaction_revision"] != current_revision
        if is_stale:
            st.warning(
                "⚠️ **Report Outdated**: Transactions, budgets, or comparison-month data changed since the last AI review. "
                "Visit the 'Monthly AI Insights' tab to re-run your grounded analysis.",
                icon="⚠️",
            )
        elif latest_report["origin"] == "dnotifier":
            st.success("✅ **Report Current**: Latest AI insights reflect all current transactions.", icon="✨")
        if latest_report["origin"] != "dnotifier":
            st.warning("Legacy report: AI origin is unverified. Run a new DNotifier review.")

    # Top KPI Metrics
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric(
            label=f"Net Spent ({selected_month})",
            value=format_pkr(stats.net_expenses_minor),
            help="Total expenses minus refunds for the month",
        )

    with col2:
        if stats.overall_budget_minor is not None:
            variance = stats.overall_variance_minor
            var_text = format_pkr(abs(variance))
            if variance > 0:
                st.metric(
                    label="Spending above income",
                    value=f"+{var_text}",
                    delta=f"{stats.overall_percent_used}% used" if stats.overall_percent_used is not None else None,
                    delta_color="inverse",
                )
            else:
                st.metric(
                    label="Income remaining",
                    value=var_text,
                    delta=f"{stats.overall_percent_used}% used" if stats.overall_percent_used is not None else None,
                    delta_color="normal",
                )
        else:
            st.metric(label="Income remaining", value="Not set", help="Set monthly income in the sidebar")

    with col3:
        # Find top over-budget category
        over_budget_cats = [
            c for c in stats.categories
            if c.over_budget_minor is not None and c.over_budget_minor > 0
        ]
        if over_budget_cats:
            top_over = max(over_budget_cats, key=lambda c: c.over_budget_minor)
            st.metric(
                label=f"Top Over-Budget ({top_over.category})",
                value=f"+{format_pkr(top_over.over_budget_minor)}",
                delta=f"{top_over.percent_used}% used",
                delta_color="inverse",
            )
        else:
            st.metric(label="Budget Health", value="No configured overrun" if existing_budgets else "Budgets not set")

    with col4:
        st.metric(
            label="Monthly income",
            value=format_pkr(stats.income_minor) if stats.overall_budget_minor is not None else "Not set",
            help="Set in the sidebar; also your overall spending limit. CSV income is not added.",
        )

    st.markdown("---")

    # Progress and Visualizations
    col_chart, col_prog = st.columns([3, 2])

    with col_chart:
        st.subheader("Category Spending vs Budget")
        chart_data = []
        for c in stats.categories:
            if c.spent_minor > 0 or (c.budget_minor and c.budget_minor > 0):
                chart_data.append({
                    "Category": c.category,
                    "Spent": float(minor_to_decimal(c.spent_minor)),
                    "Budget": float(minor_to_decimal(c.budget_minor)) if c.budget_minor else 0.0,
                })

        if chart_data:
            df_chart = pd.DataFrame(chart_data)
            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=df_chart["Category"],
                y=df_chart["Spent"],
                name="Actual Spent",
                marker_color="#2563EB",
            ))
            fig.add_trace(go.Bar(
                x=df_chart["Category"],
                y=df_chart["Budget"],
                name="Budget Limit",
                marker_color="#94A3B8",
            ))
            fig.update_layout(
                barmode="group",
                margin=dict(l=20, r=20, t=30, b=20),
                height=350,
                yaxis_title=f"Amount ({config.CURRENCY_SYMBOL})",
            )
            st.plotly_chart(fig)
        else:
            st.info("No transaction data or budgets available for this month.")

    with col_prog:
        st.subheader("Category Budget Progress")
        for c in stats.categories:
            if c.budget_minor and c.budget_minor > 0:
                pct = max(0.0, min(1.0, float(c.spent_minor / c.budget_minor)))
                raw_pct = c.percent_used or 0.0
                status_icon = "🟢"
                if raw_pct >= 100.0:
                    status_icon = "🔴"
                elif raw_pct >= 80.0:
                    status_icon = "🟡"

                st.markdown(
                    f"**{status_icon} {c.category}**: {format_pkr(c.spent_minor)} / {format_pkr(c.budget_minor)} "
                    f"(*{raw_pct}%*)"
                )
                st.progress(pct)

    st.markdown("---")

    # Recent Alerts & Top Expenses
    col_alerts, col_top = st.columns(2)

    with col_alerts:
        st.subheader("🔔 Durable Threshold Alerts")
        alerts = database.get_alerts(month=selected_month)
        if alerts:
            for a in alerts:
                badge = "✅ Sent" if a["status"] == "sent" else "⏳ Pending" if a["status"] == "pending" else "⚠️ Failed"
                st.info(
                    (f"**Spending exceeded monthly income**: {format_pkr(a['amount_minor'])} spent / "
                     f"{format_pkr(a['budget_minor'])} income — Status: **{badge}**"
                     if a["category"] == config.OVERALL_BUDGET_KEY and a["budget_minor"] is not None else
                     f"**{a['category']} reached {a['threshold']}%** ({format_pkr(a['amount_minor'])}) — Status: **{badge}**")
                )
                if a["status"] != "sent":
                    if a["budget_minor"] is None:
                        st.caption("Legacy event has no original budget snapshot; cannot safely resend.")
                    elif st.button("Retry alert", key=f"retry_alert_{a['id']}"):
                        dispatch_alert_sync(a, publish_alert)
                        st.rerun()
            st.caption("Sent means accepted by the sender, not confirmed subscriber delivery. Run watch_alerts.py separately.")
        else:
            st.write("No budget threshold alerts triggered for this month.")

    with col_top:
        st.subheader("🏷️ Largest Expenses")
        if stats.largest_expenses:
            for e in stats.largest_expenses:
                st.write(f"- **{e.description}**: {format_pkr(e.amount_minor)} on *{e.date}* (`{e.category}`)")
        else:
            st.write("No expense transactions recorded.")


# ==========================================
# TAB 2: IMPORT CSV
# ==========================================
with tab_import:
    st.subheader("Import Transaction CSV")
    st.markdown(
        "Upload your bank or expense export. Required columns: `date, description, amount` (optional: `category, type`)."
    )

    sample_col1, sample_col2 = st.columns([1, 3])
    with sample_col1:
        # Sample download buttons
        with open("sample_data/expenses_2026_09.csv", "rb") as f_sep:
            st.download_button(
                "Download Sep 2026 Sample",
                data=f_sep.read(),
                file_name="sample_expenses_2026_09.csv",
                mime="text/csv",
            )
        with open("sample_data/expenses_2026_10.csv", "rb") as f_oct:
            st.download_button(
                "Download Oct 2026 Sample",
                data=f_oct.read(),
                file_name="sample_expenses_2026_10.csv",
                mime="text/csv",
            )

    uploaded_file = st.file_uploader("Choose a CSV file", type=["csv"], key="csv_file_uploader")

    if uploaded_file:
        file_bytes = uploaded_file.getvalue()
        filename = uploaded_file.name

        import_status = check_csv_import_status(filename, file_bytes)
        if import_status["is_duplicate_file"]:
            st.error(
                f"❌ **Duplicate File**: This exact file was already imported on {import_status['imported_at']}. "
                "Re-importing is blocked to prevent double-counting transactions.",
                icon="🛑",
            )
        else:
            try:
                # Keep invalid rows editable, and keep money as decimal strings.
                parse_and_validate_csv(file_bytes)  # Structural validation first.
                df_preview = pd.read_csv(io.BytesIO(file_bytes), dtype=str, keep_default_na=False,
                                         encoding="utf-8-sig")
                df_preview.columns = [c.strip().lower() for c in df_preview.columns]
                for column, default in (("category", ""), ("type", "expense")):
                    if column not in df_preview:
                        df_preview[column] = default
                df_preview.insert(0, "Include", True)
                edited_df = st.data_editor(
                    df_preview,
                    column_config={
                        "category": st.column_config.SelectboxColumn("Category", options=config.ALL_CATEGORIES),
                        "type": st.column_config.SelectboxColumn("Type", options=[t.value for t in TransactionType]),
                    },
                    hide_index=True,
                    alt="Editable CSV rows; uncheck Include to exclude a row",
                    key=f"csv_preview_{import_status['file_sha256']}",
                )
                selected = edited_df[edited_df["Include"]].drop(columns="Include")
                approved_rows, errors = parse_and_validate_csv(selected.to_csv(index=False).encode())
                for record, original_idx in zip(approved_rows, selected.index):
                    # Only used for a fully valid selection below.
                    record["source_row"] = int(original_idx) + 1
                for err in errors:
                    st.error(f"Selected row {err['source_row']}: {err['error_message']} — edit or exclude it.")
                possible_duplicates = find_cross_file_duplicates(approved_rows)
                duplicates_approved = False
                if possible_duplicates:
                    st.warning(f"Possible cross-file duplicates at source rows {possible_duplicates}. No rows are silently dropped.")
                    duplicates_approved = st.checkbox("I reviewed these possible duplicates and want to include them",
                                                       key=f"csv_duplicates_{import_status['file_sha256']}")
                if st.button("Confirm and Commit Import", type="primary", key="btn_commit_csv"):
                    if errors or not approved_rows:
                        st.error("Correct or exclude invalid rows before importing at least one row.")
                    elif possible_duplicates and not duplicates_approved:
                        st.error("Review and acknowledge potential duplicates before importing.")
                    else:
                        previous_ids = {a["id"] for a in database.get_alerts()}
                        commit_csv_import(filename, file_bytes, approved_rows)
                        dispatch_new_alerts(previous_ids)
                        st.rerun()

            except (CSVValidationError, ValueError, UnicodeError) as e:
                st.error(f"CSV Format Error: {e}", icon="🚨")


# ==========================================
# TAB 3: INVOICES & RECEIPTS
# ==========================================
with tab_invoices:
    st.subheader("Invoice & Receipt Ingestion")
    st.markdown(
        "Upload a receipt or invoice (PDF, PNG, JPG). Fields can be verified and reviewed before saving."
    )

    uploaded_doc = st.file_uploader(
        "Upload Invoice / Receipt",
        type=["pdf", "png", "jpg", "jpeg"],
        key="invoice_uploader",
        max_upload_size=10,
    )

    doc_bytes = uploaded_doc.getvalue() if uploaded_doc else None
    doc_hash = hashlib.sha256(doc_bytes).hexdigest() if doc_bytes is not None else None
    if "invoice_draft" not in st.session_state or st.session_state.get("invoice_file_hash") != doc_hash:
        st.session_state.invoice_draft = {"processing_path": "manual"}
        st.session_state.invoice_file_hash = doc_hash
        st.session_state.invoice_version = st.session_state.get("invoice_version", 0) + 1
        st.session_state.pop("confirmed_invoice_data", None)

    if uploaded_doc and st.button("Process receipt with DNotifier", key="process_invoice"):
        st.session_state.pop("confirmed_invoice_data", None)
        with st.spinner("Interpreting receipt; review is required before any save..."):
            try:
                extension = uploaded_doc.name.lower().rsplit(".", 1)[-1]
                mime = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(extension, "application/octet-stream")
                result = run_async(process_invoice, doc_bytes, mime,
                                   AIService(DNotifierAdapter.from_environment()))
                st.session_state.invoice_draft = dict(result.draft or {},
                    processing_path=result.processing_path, failure=result.failure,
                    attempted_path=result.attempted_path)
            except DNotifierError as exc:
                st.session_state.invoice_draft = {"processing_path": "manual", "failure": str(exc)}
        st.session_state.invoice_version += 1

    draft = st.session_state.invoice_draft
    if draft.get("failure"):
        st.warning(draft["failure"])
    if draft.get("attempted_path"):
        st.caption(f"Attempted path: {draft['attempted_path']}")
    if draft.get("missing_fields"):
        st.warning("Missing fields: " + ", ".join(draft["missing_fields"]))
    if draft.get("evidence_snippet"):
        st.text(draft["evidence_snippet"])
    st.caption("Images/scans currently require manual entry: DNotifier image support is unverified. No local OCR is installed.")

    # Processing path indicator badge
    path_badge = st.session_state.invoice_draft.get("processing_path", "manual")
    st.caption(f"Processing Path: **`{path_badge}`**")

    # Editable Review Form
    st.markdown("### 📝 Review & Confirm Invoice")
    with st.form(f"invoice_review_form_{st.session_state.invoice_version}"):
        col_m1, col_m2 = st.columns(2)
        with col_m1:
            inv_merchant = st.text_input("Merchant", value=draft.get("merchant") or "", key=f"merchant_{st.session_state.invoice_version}")
            inv_number = st.text_input("Invoice / Receipt Number", value=draft.get("invoice_number") or "", key=f"number_{st.session_state.invoice_version}")
            inv_date = st.text_input("Date (YYYY-MM-DD)", value=draft.get("date") or "", key=f"date_{st.session_state.invoice_version}")

        with col_m2:
            inv_total_str = st.text_input("Total Amount (PKR)", value=draft.get("total") or "", key=f"total_{st.session_state.invoice_version}")
            inv_currency = st.text_input("Currency (PKR only)", value=draft.get("currency") or ("" if "currency" in draft else "PKR"), key=f"currency_{st.session_state.invoice_version}")
            inv_category = st.selectbox(
                "Category",
                options=config.STANDARD_EXPENSE_CATEGORIES,
                index=config.STANDARD_EXPENSE_CATEGORIES.index(draft.get("suggested_category") or "Other"),
                key=f"category_{st.session_state.invoice_version}",
            )
            inv_status = st.radio(
                "Payment Status",
                options=["paid", "unpaid"],
                index=0 if st.session_state.invoice_draft.get("payment_status", "paid") == "paid" else 1,
                help="Unpaid invoices are stored as commitments only and DO NOT affect actual spending.",
            )

        submit_review = st.form_submit_button("Proceed to Match & Save", type="primary")

    if submit_review:
        st.session_state.pop("confirmed_invoice_data", None)
        try:
            total_minor = parse_to_minor(inv_total_str)
            validate_iso_date(inv_date)
            if not inv_merchant.strip() or inv_currency != "PKR":
                raise ValueError("Enter a merchant and confirm PKR currency; currency conversion is not supported.")
            if inv_status not in ("paid", "unpaid") or inv_category not in config.STANDARD_EXPENSE_CATEGORIES:
                raise ValueError("Choose a valid payment status and expense category.")
            st.session_state.confirmed_invoice_data = {
                "merchant": inv_merchant.strip(),
                "invoice_number": inv_number.strip() if inv_number else None,
                "invoice_date": inv_date.strip(),
                "total_minor": total_minor,
                "category": inv_category,
                "payment_status": inv_status,
                "file_sha256": doc_hash,
            }
        except ValueError as e:
            st.error(f"Invalid invoice: {e}")

    # Duplicate Matching and Confirmation
    if "confirmed_invoice_data" in st.session_state:
        inv_data = st.session_state.confirmed_invoice_data
        st.markdown("---")
        st.markdown("### 🔍 Duplicate Check & Link Selection")
        st.write("Reviewed snapshot to save:", inv_data)

        duplicates = database.find_potential_duplicates(
            date=inv_data["invoice_date"],
            amount_minor=inv_data["total_minor"],
            description=inv_data["merchant"],
            invoice_number=inv_data["invoice_number"],
        )

        if duplicates:
            st.warning(
                f"Found {len(duplicates)} existing transaction(s) matching date/amount. "
                "You can link this receipt to avoid double counting, or create a new expense.",
                icon="⚠️",
            )
            for d in duplicates:
                st.write(f"- ID #{d['id']}: **{d['description']}** ({format_pkr(d['amount_minor'])}) on *{d['occurred_on']}*")

            link_choice = st.radio(
                "How would you like to handle this invoice?",
                options=["Link to existing transaction", "Create new expense"],
                key="duplicate_link_choice",
            )

            selected_tx_id = None
            if link_choice == "Link to existing transaction":
                selected_tx_id = st.selectbox(
                    "Select existing transaction to link with:",
                    options=[d["id"] for d in duplicates],
                    format_func=lambda x: f"Transaction #{x}",
                )
        else:
            st.info("No duplicate transactions detected. Ready to commit as new expense.", icon="ℹ️")
            link_choice = "Create new expense"
            selected_tx_id = None

        if st.button("Finalize and Save Receipt", type="primary", key="btn_finalize_invoice"):
            try:
                previous_ids = {a["id"] for a in database.get_alerts()}
                values = {k: v for k, v in inv_data.items() if k != "payment_status"}
                if inv_data["payment_status"] == "unpaid":
                    database.commit_unpaid_invoice(**values)
                elif link_choice == "Link to existing transaction" and selected_tx_id:
                    database.commit_paid_invoice_linked(**values, linked_transaction_id=selected_tx_id)
                else:
                    database.commit_paid_invoice_as_new_expense(**values)
                dispatch_new_alerts(previous_ids)
                del st.session_state["confirmed_invoice_data"]
                st.session_state.invoice_draft = {"processing_path": "manual"}
                st.session_state.invoice_version += 1
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    # Display persisted invoices
    st.markdown("---")
    st.subheader(f"Saved Invoices ({selected_month})")
    month_invoices = database.get_invoices(month=selected_month)
    if month_invoices:
        df_inv = pd.DataFrame([
            {
                "ID": i["id"],
                "Merchant": i["merchant"],
                "Date": i["invoice_date"],
                "Total (PKR)": float(minor_to_decimal(i["total_minor"])),
                "Category": i["category"],
                "Status": i["payment_status"],
                "Linked Tx": i["linked_transaction_id"] or "None",
            }
            for i in month_invoices
        ])
        st.dataframe(df_inv, hide_index=True, alt="Saved invoices with payment and linked-transaction status")
    else:
        st.write("No invoices recorded for this month.")


# ==========================================
# TAB 4: MONTHLY AI INSIGHTS
# ==========================================
with tab_insights:
    st.subheader(f"🤖 Monthly AI Analyst ({selected_month})")
    st.markdown(
        "Grounded budget intelligence powered by **DNotifier AI**. "
        "The model analyzes aggregate Python-computed statistics—never raw bank files."
    )

    stats = calculate_monthly_stats(selected_month)
    latest_report = database.get_latest_report(selected_month)
    current_revision = database.get_report_revision(selected_month)

    is_stale = latest_report and (latest_report["transaction_revision"] != current_revision)
    if is_stale:
        st.warning("⚠️ **Report Outdated**: Financial data has changed since this report was generated.", icon="⚠️")

    # Run Analysis Button
    col_btn, col_info = st.columns([1, 3])
    with col_btn:
        run_analysis = st.button("🚀 Analyze Month with DNotifier AI", type="primary")

    if run_analysis:
        with st.spinner("Generating grounded financial insights via DNotifier AI..."):
            # Prepare small aggregate statistics JSON
            stats_dict = stats.model_dump()

            if config.is_dnotifier_configured():
                try:
                    ai_svc = AIService(DNotifierAdapter.from_environment())
                    analysis_result = run_async(ai_svc.analyze_month, stats_dict)

                    # Validate with Pydantic
                    validated_report = MonthlyAnalysisReport(**analysis_result)

                    database.save_report(
                        month=selected_month,
                        stats_json=json.dumps(stats_dict),
                        analysis_json=validated_report.model_dump_json(),
                        transaction_revision=current_revision,
                        origin="dnotifier",
                    )
                    st.success("Analysis generated successfully!")
                    st.rerun()
                except DNotifierError as e:
                    st.error(f"DNotifier AI execution error: {e}", icon="🚨")
            else:
                st.error("DNotifier is unconfigured. Set app ID and secret privately in .env, then retry. No report was saved; local tracking remains available.")

    # Display Saved Report
    if latest_report:
        st.caption(f"Report origin: {latest_report['origin']} | Created: {latest_report['created_at']}")
        if latest_report["origin"] != "dnotifier":
            st.warning("This legacy report may be a local fallback; it is not verified DNotifier output.")
        with st.expander("Python-computed supporting facts (saved report snapshot)"):
            st.json(json.loads(latest_report["stats_json"]))
        report_data = json.loads(latest_report["analysis_json"])
        st.markdown("---")
        st.markdown(f"### 📋 Executive Summary")
        st.write(report_data.get("summary", "No summary provided."))

        col_f, col_r = st.columns(2)
        with col_f:
            st.markdown("#### 🔍 Evidence-Based Findings (Max 3)")
            for f in report_data.get("findings", []):
                sev_icon = "🔴" if f.get("severity") == "high" else "🟡" if f.get("severity") == "medium" else "🔵"
                with st.container(border=True):
                    st.markdown(f"**{sev_icon} {f.get('title')}**")
                    st.write(f"*{f.get('evidence')}*")
                    if f.get("category"):
                        st.caption(f"Category: `{f.get('category')}`")

        with col_r:
            st.markdown("#### 💡 Actionable Recommendations (Max 3)")
            for r in report_data.get("recommendations", []):
                with st.container(border=True):
                    st.markdown(f"**Action**: {r.get('action')}")
                    st.write(f"**Reason**: {r.get('reason')}")
                    if r.get("priority"):
                        st.caption(f"Priority: `{r.get('priority')}`")

        if report_data.get("limitations"):
            st.markdown("#### ⚠️ Report Limitations")
            for lim in report_data.get("limitations"):
                st.caption(f"- {lim}")
    else:
        st.info("No analysis report generated for this month yet. Click the button above to run an analysis.")
