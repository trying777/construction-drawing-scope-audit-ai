# 📐 Construction Drawing Scope Verification Pipeline

An automated AI-powered verification pipeline that audits vision-language scope extractions against architectural drawings, identifies hallucinations and drafting artifacts, and excises false positives while strictly preserving JSON schema integrity.

---

## 🌟 Key Capabilities

1. **Multimodal Grounded Verification:** Cross-references extracted construction bid items against underlying PDF vector text and spatial bounding boxes (`quad_px`) using Google Gemini with structured output validation.
2. **Schema-Preserving Excision Engine:** Surgically excises confirmed false positives in-place from nested drawing trees with zero schema mutations, synthetic keys, or data corruption.
3. **Architectural Artifact Detection:** Catches recurring false-positive patterns produced by vision-language models:
   - **Column Grid Bubbles:** Perimeter coordinate circles (`C1`, `E1`, `B1`, `A1–AX`) misread as trade scope (Carpet, Electrical, Wall Base).
   - **Title-Block Metadata:** Architect phone numbers and contact details (e.g. `T 801.924.5000`) misread as stair treads (`T`).
   - **Drafting Abbreviations:** Structural notations like `F.O.S.` (*Face of Stud*) misread as drywall framing.
   - **Coordination Notes:** General notes specifying clearances misread as physical door hardware.
   - **Room Names & Signage:** Room tags (`LIVING A`, `KITCHEN B`) misclassified as physical signage hardware.
   - **Invented Scope & Materials:** Unsubstantiated installation methods (e.g. self-adhered membranes or resinous flooring).
4. **Interactive Streamlit Web Dashboard (`app.py`):**
   - **Audit Explorer:** Filterable KPI metrics, status cards, and verdict breakdowns.
   - **Deep-Dive Spatial Inspector:** View exact quad pixel coordinates and extracted contextual drawing text.
   - **Live Drawing Set Audit:** Upload any unseen `drawings.pdf` and `results.json` to run real-time verification and download cleaned deliverables directly in-browser.
5. **Traceable Audit Outputs:** Generates detailed 16-column audit findings CSV and executive methodology reports.

---

## 🚀 Quick Setup Guide

### Step 1: Clone the Repository

```bash
git clone https://github.com/trying777/construction-drawing-scope-audit-ai.git
cd construction-drawing-scope-audit-ai
```

---

### Step 2: Create & Activate Virtual Environment

```powershell
# Windows PowerShell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
```

---

### Step 3: Install Dependencies

```bash
pip install -r requirements.txt
```

---

### Step 4: Configure Environment Variables

Copy `.env.example` to create your local `.env`:

```powershell
# Windows PowerShell
Copy-Item .env.example .env
```

```bash
# macOS / Linux
cp .env.example .env
```

Add your Google Gemini API key to `.env`:
```ini
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-3.5-flash-lite
GEMINI_REQUEST_DELAY=6.0
```

> **Note:** The API key is only needed when executing live audits on new drawing sets. Pre-generated audit deliverables can be inspected without an API key.

---

### Step 5: Run the Verification Suite & Unit Tests

Run the unit test suite:
```bash
python -m unittest test_decision_review.py test_output_writer.py
```

Run the 5-point deliverable verification check:
```bash
python verify_corrected_json.py
```

---

### Step 6: Launch the Streamlit Web Dashboard

```bash
streamlit run app.py
```

Open [http://localhost:8501](http://localhost:8501) in your browser.

---

## 📂 Repository File Structure

```text
├── app.py                      # Interactive Streamlit dashboard & live audit runner
├── decision_review.py          # Gemini-powered scope verification & heuristic reasoning
├── drawing_evidence.py         # PyMuPDF spatial coordinate extraction & text matching
├── item_preparation.py         # Scope item extraction, normalization & reference filtering
├── output_writer.py            # Schema-preserving JSON cleaner & CSV findings generator
├── input_loader.py             # Read-only input loader with coordinate & schema validation
├── verify_corrected_json.py    # Standalone 5-point mathematical verification script
├── create_methodology_doc.py   # Automated generator for the methodology report
├── test_decision_review.py     # Unit tests for review logic & edge cases
├── test_output_writer.py       # Unit tests for JSON excision & CSV schema
├── practice_test_data/         # Clean synthetic test fixtures (drawings & extraction JSON)
├── UnseenTest/                 # Synthetic unseen evaluation fixtures
├── audit_findings.csv          # Audit findings table with grounded drawing citations
├── corrected_results.json      # Cleaned scope JSON with false positives excised
├── audit_methodology.docx      # Comprehensive methodology report
├── requirements.txt            # Python dependencies
├── .env.example                # Environment variables template
└── README.md                   # Documentation & setup guide
```

---

## 🛡️ License & Disclaimers

This project is released under the MIT License. Architectural drawings and proprietary project materials are excluded from this repository.
