"""Streamlit Reviewer Dashboard for V.A.U.L.T Auditor Agent."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dashboard.api_client import VaultApiClient

try:
    import streamlit as st
except ImportError:
    st = None  # Handled gracefully if streamlit is not installed


def run_dashboard() -> None:
    if st is None:
        print("Streamlit is not installed. Run with FastAPI static UI: python -m uvicorn app.main:app")
        return

    st.set_page_config(
        page_title="V.A.U.L.T Auditor Dashboard",
        page_icon="🛡️",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    client = VaultApiClient()

    st.sidebar.title("🛡️ V.A.U.L.T")
    st.sidebar.caption("Human-in-the-Loop Financial Filing Auditor")

    page = st.sidebar.radio(
        "Navigation",
        ["Overview & Runs", "Review Queue", "Accuracy Report", "Audit Log", "Settings"],
    )

    use_mock = st.sidebar.checkbox("Use Mock Data", value=client.use_mock)
    client.use_mock = use_mock

    if page == "Overview & Runs":
        render_overview(client)
    elif page == "Review Queue":
        render_review_queue(client)
    elif page == "Accuracy Report":
        render_accuracy_report(client)
    elif page == "Audit Log":
        render_audit_log(client)
    elif page == "Settings":
        render_settings(client)


def render_overview(client: VaultApiClient) -> None:
    st.header("📊 Runs Overview & Launcher")

    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        companies = {
            "Apple Inc. (0000320193)": "0000320193",
            "Microsoft Corp. (0000789019)": "0000789019",
            "Nvidia Corp. (0001045810)": "0001045810",
            "Netflix Inc. (0001065280)": "0001065280",
            "The Coca-Cola Co. (0000021344)": "0000021344",
        }
        selected_company = st.selectbox("Select Target Company", list(companies.keys()))
        selected_cik = companies[selected_company]
    with col2:
        fiscal_year = st.number_input("Fiscal Year", min_value=2020, max_value=2026, value=2023)
    with col3:
        inject = st.selectbox(
            "Inject Deliberate Error",
            ["None", "scale_1000x", "sign_flip", "wrong_column", "missing_digit"],
        )
        inject_err = None if inject == "None" else inject

    if st.button("🚀 Start Audit Run", type="primary"):
        with st.spinner("Extracting and verifying filing..."):
            try:
                res = client.create_run(cik=selected_cik, fiscal_year=int(fiscal_year), inject_error=inject_err)
                st.success(f"Run {res.get('run_id')} started: Status={res.get('status')}")
            except Exception as e:
                st.error(f"Failed to start run: {e}")

    st.divider()

    # List recent runs
    runs = client.list_runs()
    st.subheader(f"Recent Runs ({len(runs)})")
    if runs:
        for r in runs:
            c = r.get("counts", {})
            st.markdown(
                f"**Run ID**: `{r.get('run_id')}` | **Company**: {r.get('company')} (FY{r.get('fiscal_year')}) | "
                f"**Status**: `{r.get('status')}` | Auto-approved: {c.get('auto_approved', 0)} | Needs Review: {c.get('needs_review', 0)}"
            )


def render_review_queue(client: VaultApiClient) -> None:
    st.header("🔍 Human Review Queue")
    pending = client.get_reviews(status="pending")

    if not pending:
        st.success("🎉 No pending reviews! All extracted metrics are auto-approved.")
        return

    st.warning(f"⚠️ Workflow Paused: {len(pending)} metric(s) waiting for human auditor decision.")

    for idx, item in enumerate(pending):
        with st.container(border=True):
            st.subheader(f"#{idx+1}: {item.get('company')} — `{item.get('metric_name')}`")
            col1, col2 = st.columns([1, 1])

            with col1:
                st.write(f"**Extracted Value**: `${item.get('value', 0):,.2f} {item.get('unit', 'USD')}`")
                st.write(f"**Period**: {item.get('period_start')} to {item.get('period_end')}")
                st.write(f"**XBRL Tag**: `{item.get('xbrl_tag')}`")
                st.write(f"**Confidence**: `{item.get('confidence', 0):.2f}`")
                st.progress(float(item.get("confidence", 0.0)))
                bd = item.get("confidence_breakdown", {})
                st.caption(f"Breakdown: Grounding={bd.get('grounding')} | Label={bd.get('label')} | Unit={bd.get('unit')} | YoY={bd.get('yoy')}")

            with col2:
                st.error(f"**Escalation Reason**: {item.get('escalation_reason')}")
                snippet = item.get("source_snippet", "")
                st.markdown(f"**Source Snippet**:\n> `{snippet}`")

            # Actions
            col_a, col_b, col_c, col_d = st.columns([1, 2, 2, 1])
            with col_a:
                reviewer = st.text_input("Reviewer", value="Audit Lead", key=f"rev_{item['metric_id']}")
            with col_b:
                comment = st.text_input("Comment", placeholder="Audit notes...", key=f"comm_{item['metric_id']}")
            with col_c:
                corrected_val = st.number_input(
                    "Corrected Value ($)",
                    value=float(item.get("value", 0.0)),
                    key=f"val_{item['metric_id']}",
                )

            b1, b2, b3 = st.columns(3)
            with b1:
                if st.button("✅ Approve", key=f"app_{item['metric_id']}"):
                    client.submit_review(
                        run_id=item["run_id"],
                        metric_id=item["metric_id"],
                        decision="approve",
                        reviewer=reviewer,
                        comment=comment,
                    )
                    st.rerun()
            with b2:
                if st.button("✏️ Correct", key=f"corr_{item['metric_id']}"):
                    client.submit_review(
                        run_id=item["run_id"],
                        metric_id=item["metric_id"],
                        decision="correct",
                        corrected_value=float(corrected_val),
                        reviewer=reviewer,
                        comment=comment,
                    )
                    st.rerun()
            with b3:
                if st.button("❌ Reject", key=f"rej_{item['metric_id']}"):
                    client.submit_review(
                        run_id=item["run_id"],
                        metric_id=item["metric_id"],
                        decision="reject",
                        reviewer=reviewer,
                        comment=comment,
                    )
                    st.rerun()


def render_accuracy_report(client: VaultApiClient) -> None:
    st.header("🎯 Accuracy & SEC Ground Truth Comparison")
    runs = client.list_runs()
    if not runs:
        st.info("No runs available.")
        return

    run_opts = {f"{r['company']} ({r['run_id']})": r["run_id"] for r in runs}
    selected_key = st.selectbox("Select Run for Accuracy Analysis", list(run_opts.keys()))
    run_id = run_opts[selected_key]

    try:
        report = client.get_accuracy_report(run_id)
        col1, col2, col3 = st.columns(3)
        col1.metric("Overall Extraction Accuracy", f"{report.get('overall_accuracy_pct', 0):.1f}%")
        col2.metric("Escalation Precision", f"{report.get('escalation_precision_pct', 0):.1f}%")
        col3.metric("Auto-Approved Count", report.get("auto_approved_count", 0))

        st.subheader("Metric Comparison vs. SEC Company Facts API")
        breakdown = report.get("metric_breakdown", [])
        if breakdown:
            st.table(breakdown)
    except Exception as e:
        st.warning(f"Accuracy report unavailable: {e}")


def render_audit_log(client: VaultApiClient) -> None:
    st.header("🔒 Cryptographic Audit Log")
    verify = client.verify_audit_log()
    if verify.get("verified"):
        st.success(f"✅ Cryptographic Hash Chain Verified ({verify.get('total_records', 0)} records). No tampering detected.")
    else:
        st.error(f"🚨 TAMPERING DETECTED at record index {verify.get('broken_index')}.")

    records = client.get_audit_log()
    if records:
        st.dataframe(records, use_container_width=True)
    else:
        st.info("Audit log is currently empty.")


def render_settings(client: VaultApiClient) -> None:
    st.header("⚙️ Engine Configuration & Thresholds")
    cfg = client.get_thresholds()

    curr_thresh = float(cfg.get("confidence_threshold", 0.85))
    new_thresh = st.slider("Confidence Approval Threshold", min_value=0.50, max_value=0.99, value=curr_thresh, step=0.01)
    auto_approve = st.toggle("Enable Auto-Approval", value=bool(cfg.get("auto_approve_enabled", True)))

    if st.button("Save Configuration", type="primary"):
        client.update_thresholds({"confidence_threshold": new_thresh, "auto_approve_enabled": auto_approve})
        st.success(f"Configuration saved! Confidence threshold set to {new_thresh:.2f}.")


if __name__ == "__main__":
    run_dashboard()

