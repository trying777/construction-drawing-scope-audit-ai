"""Write schema-preserving audit outputs without making audit decisions."""

import csv
import copy
import json
from pathlib import Path
from typing import Any

from input_loader import REFERENCE_SHEETS


FINDINGS_COLUMNS = [
    "sheet_number",
    "scope_package",
    "item_name",
    "verdict",
    "reason",
    "human_review",
    "review_classification",
    "confidence",
    "uncertainty",
    "supporting_evidence",
    "source_sheet_number",
    "package",
    "original_item_name",
    "item_identifier",
    "source_document_id",
    "source_page_number",
    "status",
    "error",
    "source_json_path",
]

SOURCE_PATH_FIELD = "source_json_path"


def _iter_source_items(data: dict[str, Any]):
    """Yield each original item with its canonical JSON path and source context."""
    sheets = data.get("drawings_processing_results", [])
    if not isinstance(sheets, list):
        return

    for sheet_index, sheet in enumerate(sheets):
        if not isinstance(sheet, dict):
            continue
        package_map = sheet.get("bid_items", {})
        if not isinstance(package_map, dict):
            continue

        for package_name, items in package_map.items():
            if not isinstance(items, list):
                continue
            package_key = json.dumps(package_name, ensure_ascii=True)
            for item_index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                source_path = (
                    f"drawings_processing_results[{sheet_index}].bid_items"
                    f"[{package_key}][{item_index}]"
                )
                yield source_path, sheet_index, sheet, package_name, item_index, item


def _result_matches_source(
    result: dict[str, Any],
    sheet: dict[str, Any],
    package_name: Any,
    item: dict[str, Any],
) -> bool:
    """Check available identifying metadata so stale paths cannot hit a wrong item."""
    expected_values = {
        "source_document_id": sheet.get("document_id"),
        "source_sheet_number": sheet.get("sheet_number"),
        "source_page_number": sheet.get("page_number"),
        "package": package_name,
        "original_item_name": item.get("bid_item"),
    }
    return all(
        result[field] == expected
        for field, expected in expected_values.items()
        if field in result
    )


def _is_confirmed_verdict(result: dict[str, Any], verdict: str) -> bool:
    """Only recognize reviewed RIGHT/WRONG decisions without errors."""
    return (
        result.get("proposed_verdict") == verdict
        and result.get("status") in {"completed", "human_review"}
        and not result.get("error")
    )


def build_corrected_data(
    input_data: dict[str, Any], review_results: list[dict[str, Any]]
) -> dict[str, Any]:
    """Return a deep copy with only uniquely matched, confirmed WRONG items removed.

    The original input is never changed. This module does not add verdict fields
    or any other fields to the source JSON schema. Uncertain, failed, duplicate,
    or unmatched review records leave their source items unchanged.
    """
    if not isinstance(input_data, dict):
        raise TypeError("input_data must be a JSON object represented by a dict")
    if not isinstance(review_results, list):
        raise TypeError("review_results must be a list")

    corrected_data = copy.deepcopy(input_data)
    source_items = {
        source_path: (sheet_index, sheet, package_name, item_index, item)
        for source_path, sheet_index, sheet, package_name, item_index, item
        in _iter_source_items(input_data)
    }

    results_by_path: dict[str, list[dict[str, Any]]] = {}
    for result in review_results:
        if not isinstance(result, dict):
            continue
        source_path = result.get(SOURCE_PATH_FIELD)
        if isinstance(source_path, str):
            results_by_path.setdefault(source_path, []).append(result)

    removals: list[tuple[int, str, int]] = []
    for source_path, matching_results in results_by_path.items():
        source = source_items.get(source_path)
        if source is None or len(matching_results) != 1:
            continue

        sheet_index, sheet, package_name, item_index, item = source
        result = matching_results[0]
        if sheet.get("sheet_number") in REFERENCE_SHEETS:
            continue
        if not _result_matches_source(result, sheet, package_name, item):
            continue
        if _is_confirmed_verdict(result, "WRONG"):
            removals.append((sheet_index, package_name, item_index))

    corrected_sheets = corrected_data.get("drawings_processing_results", [])
    for sheet_index, package_name, item_index in sorted(
        removals,
        key=lambda removal: (removal[0], str(removal[1]), removal[2]),
        reverse=True,
    ):
        sheet = corrected_sheets[sheet_index]
        del sheet["bid_items"][package_name][item_index]

    return corrected_data


def _csv_review_fields(result: dict[str, Any]) -> tuple[str, str, bool]:
    """Separate binary RIGHT/WRONG verdicts from review classification."""
    verdict = result.get("proposed_verdict")
    human_review = result.get("human_review") is True
    status = result.get("status")

    if verdict in {"RIGHT", "WRONG"}:
        classification = "human_review" if human_review else "judged"
        return verdict, classification, human_review
    return "", "not_judged", human_review


def build_findings_rows(review_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert each supplied review record into a safe, spreadsheet-ready row."""
    if not isinstance(review_results, list):
        raise TypeError("review_results must be a list")

    rows = []
    for result in review_results:
        if not isinstance(result, dict):
            continue
        verdict, classification, human_review = _csv_review_fields(result)
        evidence = result.get("supporting_evidence", [])
        if not isinstance(evidence, list):
            evidence = []

        sheet_number = result.get("source_sheet_number", "") or ""
        package = result.get("package", "") or ""
        item_name = result.get("original_item_name", "") or ""

        rows.append(
            {
                "sheet_number": sheet_number,
                "scope_package": package,
                "item_name": item_name,
                "verdict": verdict,
                "reason": result.get("reason", "") or "",
                "human_review": human_review,
                "review_classification": classification,
                "confidence": result.get("confidence", "") or "",
                "uncertainty": result.get("uncertainty", "") or "",
                "supporting_evidence": json.dumps(
                    evidence,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                ),
                "source_sheet_number": sheet_number,
                "package": package,
                "original_item_name": item_name,
                "item_identifier": result.get("item_identifier", "") or "",
                "source_document_id": result.get("source_document_id", "") or "",
                "source_page_number": result.get("source_page_number", "") or "",
                "status": result.get("status", "") or "",
                "error": result.get("error", "") or "",
                "source_json_path": result.get(SOURCE_PATH_FIELD, "") or "",
            }
        )

    return rows


def _ensure_not_original_input(
    output_path: str | Path, original_input_path: str | Path | None
) -> Path:
    """Reject output paths that would overwrite the original results.json."""
    destination = Path(output_path).expanduser().resolve()
    if destination.name.casefold() == "results.json":
        raise ValueError("Output path must not overwrite the original results.json")

    if original_input_path is not None:
        original = Path(original_input_path).expanduser().resolve()
        if destination == original:
            raise ValueError("Output path must not overwrite the original input file")
    return destination


def write_corrected_json(
    input_data: dict[str, Any],
    review_results: list[dict[str, Any]],
    output_path: str | Path,
    *,
    original_input_path: str | Path | None = None,
) -> Path:
    """Write a corrected schema-preserving copy, never the source input file."""
    destination = _ensure_not_original_input(output_path, original_input_path)
    corrected_data = build_corrected_data(input_data, review_results)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(corrected_data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


def write_findings_csv(
    review_results: list[dict[str, Any]],
    output_path: str | Path,
    *,
    original_input_path: str | Path | None = None,
) -> Path:
    """Write one CSV row per supplied review record using UTF-8 encoding."""
    destination = _ensure_not_original_input(output_path, original_input_path)
    rows = build_findings_rows(review_results)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=FINDINGS_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return destination