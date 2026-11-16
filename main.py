"""Coordinate read-only loading, preparation, and reporting of drawing audit inputs."""

import json
import os
from collections import Counter
from pathlib import Path


def _load_env_file():
    """Load GEMINI configuration variables from project .env if present without overwriting."""
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.is_file():
        return
    try:
        content = env_path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'\"")
            if key in {"GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_REQUEST_DELAY", "BATCH_LIMIT"}:
                os.environ.setdefault(key, value)


_load_env_file()

from drawing_evidence import build_drawing_evidence
from input_loader import REFERENCE_SHEETS, InputLoadError, load_inputs, validate_inputs
from item_preparation import prepare_scope_items, select_audit_items
from decision_review import review_audit_items
from output_writer import write_corrected_json, write_findings_csv

def print_report(data, pdf, prepared_items, audit_items, evidence_records, problems):
    """Show input summaries and validation messages without changing the inputs."""
    sheets = data.get("drawings_processing_results", [])
    package_counts = Counter(item["package_name"] for item in prepared_items)

    print("Drawing audit input inspection (read-only)")
    print(f"PDF page count: {pdf.page_count}")
    print(f"Top-level JSON keys: {', '.join(data.keys()) or '(none)'}")
    print(f"Sheet records: {len(sheets)}")

    print("\nSheets and prepared bid items")
    for sheet in sheets:
        if not isinstance(sheet, dict):
            continue

        page_number = sheet.get("page_number")
        sheet_number = sheet.get("sheet_number", "(missing sheet number)")
        if isinstance(page_number, int) and 1 <= page_number <= pdf.page_count:
            rotation = pdf.load_page(page_number - 1).rotation
        else:
            rotation = "unknown"

        sheet_items = [
            item for item in prepared_items
            if item["page_number"] == page_number
        ]
        item_region_count = sum(len(item["regions"]) for item in sheet_items)
        scopes = sheet.get("scopes", [])
        if not isinstance(scopes, list):
            scopes = []
        scope_region_count = sum(
            len(scope.get("regions", []))
            for scope in scopes
            if isinstance(scope, dict) and isinstance(scope.get("regions", []), list)
        )
        specification_region_count = sum(
            len(region_list)
            for item in sheet_items
            for specification in item["specifications"]
            if isinstance(specification, dict)
            for region_list in [specification.get("regions", [])]
            if isinstance(region_list, list)
        )
        sheet_type = "reference" if sheet_number in REFERENCE_SHEETS else "audit"
        packages = sorted({item["package_name"] for item in sheet_items})

        print(
            f"  PDF page {page_number}: {sheet_number} | "
            f"{sheet.get('sheet_name', '(missing name)')} | {sheet_type} | "
            f"rotation {rotation} | {len(sheet_items)} items across "
            f"{len(packages)} packages | item regions {item_region_count} | "
            f"scope regions {scope_region_count} | "
            f"specification regions {specification_region_count}"
        )
        print(f"    Packages: {', '.join(packages) or '(none)'}")

    reference_count = sum(
        1 for sheet in sheets
        if isinstance(sheet, dict) and sheet.get("sheet_number") in REFERENCE_SHEETS
    )
    print("\nSummary")
    print(f"  Prepared bid items: {len(prepared_items)}")
    print(f"  Audit items: {len(audit_items)}")
    print(f"  Drawing evidence records: {len(evidence_records)}")
    print(f"  Reference sheets present: {reference_count}")
    print("  Bid items by package:")
    for package_name in sorted(package_counts):
        print(f"    {package_name}: {package_counts[package_name]}")

    print(f"\nStructure checks: {len(problems)} issue(s) reported")
    if not problems:
        print("  No missing or inconsistent values were found by these checks.")
    else:
        for problem in problems:
            print(f"  - {problem}")

    unresolved_records = [record for record in evidence_records if record["flags"]]
    unresolved_flag_counts = Counter(
        flag for record in evidence_records for flag in record["flags"]
    )
    print(
        f"\nEvidence review flags: {len(unresolved_records)} records "
        f"with {sum(unresolved_flag_counts.values())} total flag(s)"
    )
    for flag, count in unresolved_flag_counts.most_common():
        print(f"  {count}x {flag}")


def main():
    """Run the approved input-loading and item-preparation stages."""
    try:
        data, pdf = load_inputs()
    except InputLoadError as error:
        print(f"Error: {error}")
        return 1

    try:
        problems = validate_inputs(data, pdf)
        prepared_items = prepare_scope_items(data)
        audit_items = select_audit_items(prepared_items)
        coordinate_dimensions_by_page = {
            sheet["page_number"]: (sheet["page_width"], sheet["page_height"])
            for sheet in data.get("drawings_processing_results", [])
            if isinstance(sheet, dict)
            and isinstance(sheet.get("page_number"), int)
            and not isinstance(sheet.get("page_number"), bool)
            and isinstance(sheet.get("page_width"), (int, float))
            and isinstance(sheet.get("page_height"), (int, float))
        }
        evidence_records = build_drawing_evidence(
            audit_items,
            pdf,
            coordinate_dimensions_by_page=coordinate_dimensions_by_page,
        )
        # Review all auditable drawing evidence records (all 237 candidates)
        test_records = evidence_records

        configured_model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
        configured_delay = float(os.environ.get("GEMINI_REQUEST_DELAY", "6.0"))
        configured_batch_limit = int(os.environ.get("BATCH_LIMIT", "0"))

        project_dir = Path(__file__).resolve().parent
        review_results_path = project_dir / "audit_review_results.json"
        findings_csv_path = project_dir / "audit_findings.csv"
        corrected_json_path = project_dir / "corrected_results.json"
        original_json_path = (
            project_dir / "Data" / "results.json"
            if (project_dir / "Data" / "results.json").exists()
            else project_dir / "results.json"
        )

        review_results = review_audit_items(
            test_records,
            data,
            pdf,
            model=configured_model,
            request_delay=configured_delay,
            checkpoint_path=review_results_path,
            batch_limit=configured_batch_limit,
        )

        with review_results_path.open("w", encoding="utf-8") as output_file:
            json.dump(review_results, output_file, ensure_ascii=False, indent=2)

        write_findings_csv(
            review_results,
            findings_csv_path,
            original_input_path=original_json_path,
        )
        write_corrected_json(
            data,
            review_results,
            corrected_json_path,
            original_input_path=original_json_path,
        )

        for result in review_results:
            print("\nGemini test result")
            print("Item:", result.get("original_item_name"))
            print("Verdict:", result.get("proposed_verdict"))
            print("Reason:", result.get("reason"))
            print("Status:", result.get("status"))
            print("Human review:", result.get("human_review"))
            if result.get("error"):
                print("Error:", result.get("error"))

        print_report(
            data,
            pdf,
            prepared_items,
            audit_items,
            evidence_records,
            problems,
        )
    finally:
        pdf.close()

    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())