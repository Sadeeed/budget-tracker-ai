"""BudgetPilot — Monthly expense and budget tracking assistant.

Built for the DNotifier Hackathon MVP.
Provides deterministic financial tracking, CSV imports, receipt reviews,
grounded AI budget analysis, and real-time threshold notifications.
"""

import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import config
import database
from csv_import import (
    parse_and_validate_csv,
    check_csv_import_status,
    commit_csv_import,
    CSVValidationError,
)
from finance import (
    calculate_monthly_stats,
    get_prior_month_str,
)
from money import format_pkr, minor_to_decimal, parse_to_minor
from models import (
    MonthlyAnalysisReport,
    PaymentStatus,
    ProcessingPath,
    TransactionSource,
    TransactionType,
)
from notifications import (
    build_alert_payload,
    dispatch_alert_sync,
    evaluate_threshold_crossings,
)

# Optional Dev 2 hooks
try:
    from ai_service import AIService
except ImportError:
    AIService = None

try:
    from dnotifier_adapter import publish_alert
except ImportError:
    publish_alert = None


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
    st.sidebar.success("● DNotifier Connected", icon="🔌")
else:
    st.sidebar.info("○ DNotifier Unconfigured (Local Mode)", icon="ℹ️")

st.sidebar.markdown("---")

# Month Selector
available_months = get_available_months()
default_month_idx = 0
if "2026-10" in available_months:
    default_month_idx = available_months.index("2026-10")

selected_month = st.sidebar.selectbox(
    "Target Month",
    options=available_months,
    index=default_month_idx,
    help="Select calendar month for tracking and budget analysis",
)

st.sidebar.markdown("### 🎯 Monthly Budgets")
existing_budgets = database.get_budgets(selected_month)

with st.sidebar.expander(f"Edit {selected_month} Budgets", expanded=False):
    # Overall Cap
    curr_overall = existing_budgets.get(config.OVERALL_BUDGET_KEY, 0)
    new_overall = st.number_input(
        f"Overall Cap ({config.CURRENCY_SYMBOL})",
        min_value=0,
        value=int(curr_overall / 100),
        step=5000,
        key=f"budget_overall_{selected_month}",
    )
    if st.button("Save Overall Cap", key="btn_save_overall"):
        database.set_budget(selected_month, config.OVERALL_BUDGET_KEY, new_overall * 100)
        st.success("Overall cap saved!")
        st.rerun()

    st.markdown("#### Category Limits")
    budget_updates = {}
    for cat in config.STANDARD_EXPENSE_CATEGORIES:
        curr_val = existing_budgets.get(cat, 0)
        val = st.number_input(
            f"{cat} ({config.CURRENCY_SYMBOL})",
            min_value=0,
            value=int(curr_val / 100),
            step=1000,
            key=f"budget_{cat}_{selected_month}",
        )
        budget_updates[cat] = val

    if st.button("Save Category Budgets", key="btn_save_categories"):
        for cat, val in budget_updates.items():
            if val > 0:
                database.set_budget(selected_month, cat, val * 100)
        st.success("Category budgets updated!")
        st.rerun()

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
    current_revision = database.get_transaction_revision(selected_month)

    # Report staleness banner
    if latest_report:
        is_stale = latest_report["transaction_revision"] != current_revision
        if is_stale:
            st.warning(
                "⚠️ **Report Outdated**: New financial data or invoices have been added since the last AI review. "
                "Visit the 'Monthly AI Insights' tab to re-run your grounded analysis.",
                icon="⚠️",
            )
        else:
            st.success("✅ **Report Current**: Latest AI insights reflect all current transactions.", icon="✨")

    # Top KPI Metrics
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric(
            label=f"Net Spent ({selected_month})",
            value=format_pkr(stats.net_expenses_minor),
            help="Total expenses minus refunds for the month",
        )

    with col2:
        if stats.overall_budget_minor:
            variance = stats.overall_variance_minor
            var_text = format_pkr(abs(variance))
            if variance > 0:
                st.metric(
                    label="Overall Budget Overrun",
                    value=f"+{var_text}",
                    delta=f"{stats.overall_percent_used}% used",
                    delta_color="inverse",
                )
            else:
                st.metric(
                    label="Overall Budget Remaining",
                    value=var_text,
                    delta=f"{stats.overall_percent_used}% used",
                    delta_color="normal",
                )
        else:
            st.metric(label="Overall Budget", value="Not set", help="Configure in sidebar")

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
            st.metric(label="Budget Health", value="All in budget", delta="100% on track")

    with col4:
        st.metric(
            label="Total Income",
            value=format_pkr(stats.income_minor),
            help="Income transactions tracked separately from spending",
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
                pct = min(1.0, float(c.spent_minor / c.budget_minor))
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
                    f"**{a['category']} reached {a['threshold']}%** ({format_pkr(a['amount_minor'])}) — Status: **{badge}**"
                )
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
                valid_rows, errors = parse_and_validate_csv(file_bytes)

                if errors:
                    st.warning(f"⚠️ {len(errors)} row(s) had validation errors and will be excluded:", icon="⚠️")
                    for err in errors:
                        st.write(f"- Row {err['source_row']}: {err['error_message']}")

                if valid_rows:
                    st.success(f"Validated {len(valid_rows)} transaction row(s). Preview below before confirming import.")

                    # Editable preview table
                    df_preview = pd.DataFrame([
                        {
                            "Date": r["occurred_on"],
                            "Description": r["description"],
                            "Amount (PKR)": float(minor_to_decimal(r["amount_minor"])),
                            "Category": r["category"],
                            "Type": r["type"],
                        }
                        for r in valid_rows
                    ])

                    edited_df = st.data_editor(
                        df_preview,
                        column_config={
                            "Category": st.column_config.SelectboxColumn(
                                "Category",
                                options=config.ALL_CATEGORIES,
                                required=True,
                            ),
                            "Type": st.column_config.SelectboxColumn(
                                "Type",
                                options=[t.value for t in TransactionType],
                                required=True,
                            ),
                        },
                        hide_index=True,
                        key="csv_preview_editor",
                    )

                    if st.button("Confirm and Commit Import", type="primary", key="btn_commit_csv"):
                        # Re-apply any edits made in the data editor
                        approved_rows = []
                        for idx, row in edited_df.iterrows():
                            orig = valid_rows[idx]
                            approved_rows.append({
                                "source_row": orig["source_row"],
                                "occurred_on": str(row["Date"]),
                                "description": str(row["Description"]),
                                "merchant": str(row["Description"]),
                                "amount_minor": parse_to_minor(row["Amount (PKR)"]),
                                "category": str(row["Category"]),
                                "type": str(row["Type"]),
                                "source": TransactionSource.CSV.value,
                            })

                        import_id, inserted_ids = commit_csv_import(filename, file_bytes, approved_rows)
                        st.success(f"🎉 Successfully imported {len(inserted_ids)} transactions (Import ID #{import_id})!")
                        st.rerun()

            except CSVValidationError as e:
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
    )

    # Initialize review session state
    if "invoice_draft" not in st.session_state:
        st.session_state.invoice_draft = {
            "merchant": "Al-Fatah Department Store",
            "invoice_number": "INV-2026-10-8842",
            "date": "2026-10-26",
            "total": "7906.00",
            "category": "Groceries",
            "payment_status": "paid",
            "processing_path": "digital_text",
        }

    if uploaded_doc:
        doc_bytes = uploaded_doc.getvalue()
        doc_name = uploaded_doc.name
        is_pdf = doc_name.lower().endswith(".pdf")

        # Extraction logic
        if is_pdf:
            try:
                import pymupdf
                pdf_doc = pymupdf.open(stream=doc_bytes, filetype="pdf")
                extracted_text = ""
                for page in pdf_doc:
                    extracted_text += page.get_text()
                pdf_doc.close()

                if extracted_text.strip():
                    st.session_state.invoice_draft["processing_path"] = "digital_text"
                    st.info("Extracted digital text via PyMuPDF.")
            except Exception as e:
                st.session_state.invoice_draft["processing_path"] = "manual"
                st.warning(f"Digital text extraction fallback to manual review: {e}")

    # Processing path indicator badge
    path_badge = st.session_state.invoice_draft.get("processing_path", "manual")
    st.caption(f"Processing Path: **`{path_badge}`**")

    # Editable Review Form
    st.markdown("### 📝 Review & Confirm Invoice")
    with st.form("invoice_review_form"):
        col_m1, col_m2 = st.columns(2)
        with col_m1:
            inv_merchant = st.text_input("Merchant", value=st.session_state.invoice_draft.get("merchant", ""))
            inv_number = st.text_input("Invoice / Receipt Number", value=st.session_state.invoice_draft.get("invoice_number", ""))
            inv_date = st.text_input("Date (YYYY-MM-DD)", value=st.session_state.invoice_draft.get("date", selected_month + "-15"))

        with col_m2:
            inv_total_str = st.text_input("Total Amount (PKR)", value=st.session_state.invoice_draft.get("total", "0.00"))
            inv_category = st.selectbox(
                "Category",
                options=config.STANDARD_EXPENSE_CATEGORIES,
                index=config.STANDARD_EXPENSE_CATEGORIES.index(st.session_state.invoice_draft.get("category", "Groceries")),
            )
            inv_status = st.radio(
                "Payment Status",
                options=["paid", "unpaid"],
                index=0 if st.session_state.invoice_draft.get("payment_status", "paid") == "paid" else 1,
                help="Unpaid invoices are stored as commitments only and DO NOT affect actual spending.",
            )

        submit_review = st.form_submit_button("Proceed to Match & Save", type="primary")

    if submit_review:
        try:
            total_minor = parse_to_minor(inv_total_str)
            st.session_state.confirmed_invoice_data = {
                "merchant": inv_merchant.strip(),
                "invoice_number": inv_number.strip() if inv_number else None,
                "invoice_date": inv_date.strip(),
                "total_minor": total_minor,
                "category": inv_category,
                "payment_status": inv_status,
            }
        except ValueError as e:
            st.error(f"Invalid monetary value: {e}")

    # Duplicate Matching and Confirmation
    if "confirmed_invoice_data" in st.session_state:
        inv_data = st.session_state.confirmed_invoice_data
        st.markdown("---")
        st.markdown("### 🔍 Duplicate Check & Link Selection")

        duplicates = database.find_potential_duplicates(
            date=inv_data["invoice_date"],
            amount_minor=inv_data["total_minor"],
            description=inv_data["merchant"],
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
                options=["Create new expense", "Link to existing transaction"],
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
            if inv_data["payment_status"] == "unpaid":
                # Unpaid commitment
                inv_id = database.commit_unpaid_invoice(
                    merchant=inv_data["merchant"],
                    invoice_date=inv_data["invoice_date"],
                    total_minor=inv_data["total_minor"],
                    category=inv_data["category"],
                    invoice_number=inv_data["invoice_number"],
                )
                st.success(f"✅ Saved as unpaid commitment (Invoice #{inv_id}). No change to actual spending.")
            elif link_choice == "Link to existing transaction" and selected_tx_id:
                # Link to existing
                inv_id = database.commit_paid_invoice_linked(
                    merchant=inv_data["merchant"],
                    invoice_date=inv_data["invoice_date"],
                    total_minor=inv_data["total_minor"],
                    category=inv_data["category"],
                    linked_transaction_id=selected_tx_id,
                    invoice_number=inv_data["invoice_number"],
                )
                st.success(f"🔗 Linked invoice #{inv_id} to existing transaction #{selected_tx_id}. Spending unchanged.")
            else:
                # Create new expense
                inv_month = inv_data["invoice_date"][:7]
                stats_before = calculate_monthly_stats(inv_month)
                cat_before = next(
                    (c.spent_minor for c in stats_before.categories if c.category == inv_data["category"]),
                    0,
                )

                inv_id, tx_id = database.commit_paid_invoice_as_new_expense(
                    merchant=inv_data["merchant"],
                    invoice_date=inv_data["invoice_date"],
                    total_minor=inv_data["total_minor"],
                    category=inv_data["category"],
                    invoice_number=inv_data["invoice_number"],
                )
                st.success(f"🎉 Created new expense transaction #{tx_id} from invoice #{inv_id}!")

                # Evaluate threshold crossing
                budgets = database.get_budgets(inv_month)
                cat_budget = budgets.get(inv_data["category"])
                cat_after = cat_before + inv_data["total_minor"]

                alerts = evaluate_threshold_crossings(
                    month=inv_month,
                    category=inv_data["category"],
                    before_spent_minor=cat_before,
                    after_spent_minor=cat_after,
                    budget_minor=cat_budget,
                )

                for alert in alerts:
                    st.warning(
                        f"🚨 **Budget Alert**: {alert['category']} crossed {alert['threshold']}% limit!",
                        icon="🚨",
                    )
                    # Attempt dispatch if publisher is available
                    dispatch_alert_sync(alert, publish_alert)

            del st.session_state["confirmed_invoice_data"]
            st.rerun()

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
        st.dataframe(df_inv, hide_index=True)
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
    current_revision = database.get_transaction_revision(selected_month)

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

            if AIService and config.is_dnotifier_configured():
                try:
                    import asyncio
                    ai_svc = AIService()
                    # Call async method safely
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    analysis_result = loop.run_until_complete(ai_svc.analyze_month(stats_dict))

                    # Validate with Pydantic
                    validated_report = MonthlyAnalysisReport(**analysis_result)

                    database.save_report(
                        month=selected_month,
                        stats_json=json.dumps(stats_dict),
                        analysis_json=validated_report.model_dump_json(),
                        transaction_revision=current_revision,
                    )
                    st.success("Analysis generated successfully!")
                    st.rerun()
                except Exception as e:
                    st.error(f"DNotifier AI execution error: {e}", icon="🚨")
            else:
                # Deterministic fallback when DNotifier credentials/adapter not yet wired
                st.info("Generating deterministic fallback analysis (DNotifier AI not configured).")
                findings = []
                recommendations = []

                # Grounded findings from computed stats
                for c in stats.categories:
                    if c.over_budget_minor and c.over_budget_minor > 0:
                        findings.append({
                            "title": f"{c.category} exceeded limit",
                            "severity": "high",
                            "evidence": f"Rs {format_pkr(c.spent_minor)} spent vs {format_pkr(c.budget_minor)} limit; {format_pkr(c.over_budget_minor)} over.",
                            "category": c.category,
                        })
                        recommendations.append({
                            "action": f"Set a mid-month checkpoint for {c.category}.",
                            "reason": f"{c.category} is {c.percent_used}% of its limit.",
                            "category": c.category,
                            "priority": "high",
                        })

                fallback_report = MonthlyAnalysisReport(
                    summary=f"Analysis for {selected_month}: Net expenses {format_pkr(stats.net_expenses_minor)}.",
                    findings=findings[:3],
                    recommendations=recommendations[:3],
                    limitations=["Calculated strictly from local aggregate numbers."],
                )

                database.save_report(
                    month=selected_month,
                    stats_json=json.dumps(stats_dict),
                    analysis_json=fallback_report.model_dump_json(),
                    transaction_revision=current_revision,
                )
                st.rerun()

    # Display Saved Report
    if latest_report:
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
