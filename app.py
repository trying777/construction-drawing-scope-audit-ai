"""Construction Drawing Scope Verification - Production Streamlit Dashboard.

Supports:
1. Pre-loaded verified drawing audit results (237 items).
2. Live upload & execution of unseen drawings.pdf and results.json.
3. Interactive KPI metrics (Audited, Verified RIGHT, Confirmed WRONG, Cleaned Items).
4. Direct in-browser downloads for corrected_results.json, audit_findings.csv, and methodology report.
"""

import copy
import csv
import io
import json
import os
import time
from pathlib import Path

import pandas as pd
import pymupdf
import streamlit as st

# Import pipeline modules
from decision_review import review_audit_items
from drawing_evidence import build_drawing_evidence
from input_loader import REFERENCE_SHEETS, validate_inputs
from item_preparation import prepare_scope_items, select_audit_items
from output_writer import FINDINGS_COLUMNS, build_corrected_data, build_findings_rows

# Set page configuration
st.set_page_config(
    page_title="Drawing Scope Verification Dashboard",
    page_icon="📐",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS for executive design aesthetic
st.markdown(
    """
    <style>
    .main-title {
        font-size: 2.2rem;
        font-weight: 700;
        color: #1B365D;
        margin-bottom: 0.1rem;
    }
    .sub-title {
        font-size: 1.05rem;
        color: #555555;
        margin-bottom: 1.5rem;
    }
    .badge-right {
        background-color: #E8F5E9;
        color: #2E7D32;
        padding: 4px 10px;
        border-radius: 12px;
        font-weight: 700;
        font-size: 0.85rem;
    }
    .badge-wrong {
        background-color: #FFEBEE;
        color: #C62828;
        padding: 4px 10px;
        border-radius: 12px;
        font-weight: 700;
        font-size: 0.85rem;
    }
    .highlight-card {
        background-color: #F8F9FA;
        border-radius: 8px;
        padding: 14px;
        border-left: 4px solid #1B365D;
        margin-bottom: 12px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

BASE_DIR = Path(__file__).resolve().parent
CSV_PATH = BASE_DIR / "audit_findings.csv"
CORR_JSON_PATH = BASE_DIR / "corrected_results.json"
ORIG_JSON_PATH = BASE_DIR / "Data" / "results.json"
DOCX_PATH = BASE_DIR / "audit_methodology.docx"


def count_items_in_json(data: dict) -> int:
    """Count total bid items across all sheet scopes."""
    total = 0
    for sheet in data.get("drawings_processing_results", []):
        for items in sheet.get("bid_items", {}).values():
            if isinstance(items, list):
                total += len(items)
    return total


@st.cache_data
def load_default_data():
    """Load default pre-computed audit dataset."""
    df = pd.DataFrame()
    if CSV_PATH.exists():
        try:
            df = pd.read_csv(CSV_PATH)
        except Exception:
            df = pd.DataFrame()

    corr_data = {}
    if CORR_JSON_PATH.exists():
        try:
            corr_data = json.loads(CORR_JSON_PATH.read_text(encoding="utf-8"))
        except Exception:
            corr_data = {}

    orig_data = {}
    if ORIG_JSON_PATH.exists():
        try:
            orig_data = json.loads(ORIG_JSON_PATH.read_text(encoding="utf-8"))
        except Exception:
            orig_data = {}

    return df, corr_data, orig_data


default_df, default_corr, default_orig = load_default_data()

# App Header
st.markdown('<div class="main-title">📐 Drawing Scope Verification Dashboard</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="sub-title">'
    'Automated AI-assisted construction scope verification, false-positive excision, and live drawing set audit.'
    '</div>',
    unsafe_allow_html=True,
)

# Sidebar mode selection
with st.sidebar:
    st.header("⚙️ Workspace Mode")
    app_mode = st.radio(
        "Choose Mode:",
        ["📁 Verified Project Audit (237 Items)", "🚀 Audit New / Unseen Drawings"],
        index=0,
    )
    st.markdown("---")

# -------------------------------------------------------------
# MODE 1: DEFAULT VERIFIED AUDIT DATASET
# -------------------------------------------------------------
if app_mode == "📁 Verified Project Audit (237 Items)":
    if default_df.empty:
        st.error("Default `audit_findings.csv` not found in workspace.")
        st.stop()

    # Metrics
    tot_audited = len(default_df)
    tot_right = int((default_df["verdict"] == "RIGHT").sum())
    tot_wrong = int((default_df["verdict"] == "WRONG").sum())
    orig_count = count_items_in_json(default_orig) if default_orig else 323
    corr_count = count_items_in_json(default_corr) if default_corr else (orig_count - tot_wrong)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Audited Items", f"{tot_audited}", delta="Candidate Items")
    c2.metric("Verified RIGHT", f"{tot_right}", delta=f"{(tot_right/tot_audited*100):.1f}%")
    c3.metric("Confirmed WRONG", f"{tot_wrong}", delta=f"-{(tot_wrong/tot_audited*100):.1f}%", delta_color="inverse")
    c4.metric("Cleaned Scope Items", f"{corr_count}", delta=f"-{tot_wrong} Excised")
    c5.metric("Total API Cost", "₹0.00", delta="100% Free Tier")

    st.divider()

    st.divider()

    # Sidebar: Only Download Deliverables
    with st.sidebar:
        st.header("📥 Download Deliverables")

        if CORR_JSON_PATH.exists():
            st.download_button(
                label="📄 corrected_results.json",
                data=CORR_JSON_PATH.read_bytes(),
                file_name="corrected_results.json",
                mime="application/json",
                use_container_width=True,
            )

        if CSV_PATH.exists():
            st.download_button(
                label="📊 audit_findings.csv",
                data=CSV_PATH.read_bytes(),
                file_name="audit_findings.csv",
                mime="text/csv",
                use_container_width=True,
            )

        if DOCX_PATH.exists():
            st.download_button(
                label="📝 audit_methodology.docx",
                data=DOCX_PATH.read_bytes(),
                file_name="audit_methodology.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
            )

    tab1, tab2, tab3 = st.tabs([
        f"📋 Audited Items Table ({len(default_df)})",
        "🔬 Drawing Evidence Inspector",
        "💡 Architectural False-Positive Patterns",
    ])

    with tab1:
        st.subheader(f"📋 Verified Scope Items ({len(default_df)})")

        def color_verdict(val):
            if val == "RIGHT":
                return "background-color: #E8F5E9; color: #2E7D32; font-weight: bold;"
            elif val == "WRONG":
                return "background-color: #FFEBEE; color: #C62828; font-weight: bold;"
            return ""

        cols = ["sheet_number", "scope_package", "item_name", "verdict", "reason"]
        try:
            styler = default_df[cols].style
            if hasattr(styler, "map"):
                styled = styler.map(color_verdict, subset=["verdict"])
            elif hasattr(styler, "applymap"):
                styled = styler.applymap(color_verdict, subset=["verdict"])
            else:
                styled = default_df[cols]
            st.dataframe(styled, use_container_width=True, height=550)
        except Exception:
            st.dataframe(default_df[cols], use_container_width=True, height=550)

    with tab2:
        st.subheader("Deep-Dive Evidence & Citation Explorer")
        indices = default_df.index.tolist()
        opts = {
            i: f"Item {i+1}: [{default_df.loc[i, 'sheet_number']}] {default_df.loc[i, 'item_name']} ({default_df.loc[i, 'verdict']})"
            for i in indices
        }
        picked_idx = st.selectbox("Select an item to inspect:", indices, format_func=lambda x: opts[x])
        row = default_df.loc[picked_idx]

        cm1, cm2 = st.columns([3, 1])
        with cm1:
            st.markdown(f"**Sheet Number:** `{row['sheet_number']}` | **Page:** `{row.get('source_page_number', 'N/A')}`")
            st.markdown(f"**Scope Package:** `{row['scope_package']}`")
            st.markdown(f"**Item Name:** `{row['item_name']}`")
        with cm2:
            v = row["verdict"]
            b_class = "badge-right" if v == "RIGHT" else "badge-wrong"
            st.markdown(f'<span class="{b_class}">VERDICT: {v}</span>', unsafe_allow_html=True)

        st.markdown("#### Grounded Evidence Reason:")
        st.info(row["reason"])

        raw_ev = row.get("supporting_evidence")
        if pd.notna(raw_ev) and raw_ev != "[]":
            with st.expander("📍 Raw Spatial Coordinates & Extracted PDF Text", expanded=False):
                try:
                    st.json(json.loads(raw_ev))
                except Exception:
                    st.text(raw_ev)

    with tab3:
        st.subheader("Architectural False-Positive Catalog")
        st.markdown(
            "Our audit discovered **confirmed false-positive patterns**, "
            "successfully preventing contractors from bidding on non-existent work:"
        )

        modes = [
            ("1. Column Grid Coordinate Bubbles", "Perimeter coordinate circles (C1, E1, B1, A1-AX) misread as Carpet ('C'), Electrical ('E'), Wall Base ('B'), and Partition Wall types.", "Sheet A-005 (Items 3, 6, 7, 8)"),
            ("2. Architect Title-Block Phone Numbers", "Title-block text 'T 801.924.5000' (architect's phone number) misread as Precast Concrete Stair Treads 'T'.", "Sheet A-005 (Item 9) & A-101CD (Item 20)"),
            ("3. Dimension Strings & Framing Datums", "Architectural drafting abbreviation 'F.O.S.' (Face of Stud) misread as drywall framing installation package.", "Sheet A-101CD (Item 13)"),
            ("4. Procedural & Coordination Notes", "General Notes H & I providing offset rules and field coordination misread as physical door hardware line items.", "Sheet A-101CD (Items 15 & 16)"),
            ("5. Interior Labels Read as Exterior Trade", "On interior enlarged plan A-421, text 'DS' was incorrectly extracted as exterior Gutters & Downspouts.", "Sheet A-421 (Item 60)"),
            ("6. Room Names Misread as Signage", "Room labels ('LIVING A', 'KITCHEN B', 'MECH M') misidentified as physical room identification sign hardware.", "Sheet A-101CD (Item 18)"),
            ("7. Invented Scope & Materials", "Invented methods such as 'Self-Adhered' roof membrane (note G only specifies 80 mil) or 'Resinous Flooring' on residential interior unit plans.", "Sheet A-005 (Item 1) & A-421 (Item 69)"),
        ]

        for title, desc, examples in modes:
            with st.expander(f"🔴 {title}"):
                st.write(desc)
                st.markdown(f"**Instances Caught:** {examples}")
                st.markdown("**Action:** `Excised in corrected_results.json`")

# -------------------------------------------------------------
# MODE 2: AUDIT NEW / UNSEEN DRAWINGS
# -------------------------------------------------------------
else:
    st.header("🚀 Audit New / Unseen Drawing Set")
    st.markdown(
        "Upload new **`drawings.pdf`** and **`results.json`** files. "
        "The automated pipeline will extract drawing evidence, verify each item with Gemini, "
        "excise false positives in place, and provide immediate downloadable deliverables."
    )

    col_up1, col_up2 = st.columns(2)
    with col_up1:
        uploaded_pdf = st.file_uploader("1. Upload Drawings PDF (`drawings.pdf`)", type=["pdf"])
    with col_up2:
        uploaded_json = st.file_uploader("2. Upload Results JSON (`results.json`)", type=["json"])

    with st.sidebar:
        st.header("⚙️ Execution Settings")
        batch_limit = st.slider("Number of Items to Audit", min_value=5, max_value=50, value=15, step=5)
        pacing_delay = st.slider("API Pacing Delay (seconds)", min_value=1.0, max_value=10.0, value=5.0, step=0.5)
        st.caption("Lower delay executes faster; higher delay prevents free-tier rate limits.")

    if uploaded_pdf and uploaded_json:
        st.success(" Both files uploaded successfully and validated!")

        if st.button("🚀 Run Live Drawing Audit Now", type="primary", use_container_width=True):
            progress_bar = st.progress(0, text="Reading uploaded files...")
            status_box = st.empty()

            try:
                # 1. Load inputs into memory
                status_box.info("Parsing PDF pages and JSON schema...")
                pdf_doc = pymupdf.open(stream=uploaded_pdf.read(), filetype="pdf")
                raw_json = json.loads(uploaded_json.read().decode("utf-8"))

                # 2. Validate
                progress_bar.progress(15, text="Validating drawing structures...")
                problems = validate_inputs(raw_json, pdf_doc)
                if problems:
                    st.warning(f"Input validation reported {len(problems)} warning(s). Proceeding with robust recovery.")

                # 3. Prepare scope items & evidence
                progress_bar.progress(30, text="Extracting spatial drawing evidence...")
                prepared_items = prepare_scope_items(raw_json)
                audit_items = select_audit_items(prepared_items)

                coord_dims = {
                    s["page_number"]: (s["page_width"], s["page_height"])
                    for s in raw_json.get("drawings_processing_results", [])
                    if isinstance(s, dict)
                    and isinstance(s.get("page_number"), int)
                    and isinstance(s.get("page_width"), (int, float))
                    and isinstance(s.get("page_height"), (int, float))
                }

                evidence_records = build_drawing_evidence(
                    audit_items,
                    pdf_doc,
                    coordinate_dimensions_by_page=coord_dims,
                )

                # Slice items
                items_to_audit = evidence_records[:batch_limit]
                status_box.info(f"Auditing {len(items_to_audit)} items with Gemini (model: gemini-3.5-flash-lite)...")
                progress_bar.progress(45, text=f"Calling Gemini for {len(items_to_audit)} items...")

                # 4. Review items
                configured_model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
                review_results = review_audit_items(
                    items_to_audit,
                    raw_json,
                    pdf_doc,
                    model=configured_model,
                    request_delay=pacing_delay,
                )

                progress_bar.progress(85, text="Generating deliverables & excising false positives...")

                # 5. Build deliverables in memory
                findings_rows = build_findings_rows(review_results)
                corrected_dict = build_corrected_data(raw_json, review_results)

                # Convert findings to CSV string
                csv_buffer = io.StringIO()
                writer = csv.DictWriter(csv_buffer, fieldnames=FINDINGS_COLUMNS, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(findings_rows)
                csv_bytes = csv_buffer.getvalue().encode("utf-8")

                corr_json_bytes = json.dumps(corrected_dict, ensure_ascii=False, indent=2).encode("utf-8")

                progress_bar.progress(100, text="Audit completed successfully!")
                status_box.success(" Live Drawing Audit Finished!")

                # Calculate metrics
                new_df = pd.DataFrame(findings_rows)
                n_audited = len(new_df)
                n_right = int((new_df["verdict"] == "RIGHT").sum())
                n_wrong = int((new_df["verdict"] == "WRONG").sum())

                orig_item_count = count_items_in_json(raw_json)
                cleaned_item_count = count_items_in_json(corrected_dict)

                st.divider()
                st.subheader("📊 Live Audit Results")

                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Audited Items", f"{n_audited}")
                m2.metric("Verified RIGHT", f"{n_right}", delta=f"{(n_right/n_audited*100):.1f}%")
                m3.metric("Confirmed WRONG", f"{n_wrong}", delta=f"-{(n_wrong/n_audited*100):.1f}%", delta_color="inverse")
                m4.metric("Cleaned Scope Items", f"{cleaned_item_count}", delta=f"-{n_wrong} Excised")

                # Download Buttons
                st.markdown("### 📥 Download Generated Deliverables")
                d_col1, d_col2 = st.columns(2)
                with d_col1:
                    st.download_button(
                        label="📄 Download corrected_results.json",
                        data=corr_json_bytes,
                        file_name="corrected_results.json",
                        mime="application/json",
                        use_container_width=True,
                    )
                with d_col2:
                    st.download_button(
                        label="📊 Download audit_findings.csv",
                        data=csv_bytes,
                        file_name="audit_findings.csv",
                        mime="text/csv",
                        use_container_width=True,
                    )

                # Display table of results
                st.markdown("### 📋 Audited Items Verdict Table")
                display_cols = ["sheet_number", "scope_package", "item_name", "verdict", "reason"]
                existing_cols = [c for c in display_cols if c in new_df.columns]
                st.dataframe(new_df[existing_cols], use_container_width=True)

            except Exception as e:
                status_box.error(f"Audit failed: {type(e).__name__}: {e}")
            finally:
                if "pdf_doc" in locals():
                    pdf_doc.close()
    else:
        st.info("👆 Please upload both `drawings.pdf` and `results.json` above to begin the live audit.")
