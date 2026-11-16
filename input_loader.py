"""Load drawing audit inputs and check their basic structure."""

import json
import math
from pathlib import Path

import pymupdf


REFERENCE_SHEETS = {"G-002.3", "G-501", "G-511"}

EXPECTED_ROOT_FIELDS = {
    "drawings_processing_results": list,
    "specs_processing_results": list,
    "mixed_document_ids": list,
}

EXPECTED_SHEET_FIELDS = {
    "document_id": str,
    "page_number": int,
    "sheet_name": str,
    "sheet_number": str,
    "discipline": str,
    "page_width": (int, float),
    "page_height": (int, float),
    "scopes": list,
    "schedules": list,
    "bid_items": dict,
    "callouts": list,
}


class InputLoadError(Exception):
    """An input file is missing, unreadable, or has an invalid format."""


def load_inputs(input_folder=None, json_path=None, pdf_path=None):
    
    import os

    script_folder = Path(__file__).resolve().parent
    data_folder = script_folder / "Data"

    # 1. Resolve results JSON path
    if json_path is not None:
        json_file = Path(json_path)
    elif os.environ.get("INPUT_JSON_PATH"):
        json_file = Path(os.environ["INPUT_JSON_PATH"])
    elif input_folder is not None:
        json_file = Path(input_folder) / "results.json"
    else:
        if (data_folder / "results.json").exists():
            json_file = data_folder / "results.json"
        elif (script_folder / "results.json").exists():
            json_file = script_folder / "results.json"
        elif (script_folder / "practice_test_data" / "practice_results.json").exists():
            json_file = script_folder / "practice_test_data" / "practice_results.json"
        else:
            json_file = data_folder / "results.json"

    # 2. Resolve drawings PDF path
    if pdf_path is not None:
        pdf_file = Path(pdf_path)
    elif os.environ.get("INPUT_PDF_PATH"):
        pdf_file = Path(os.environ["INPUT_PDF_PATH"])
    elif input_folder is not None:
        pdf_file = Path(input_folder) / "drawings.pdf"
    else:
        if (data_folder / "drawings.pdf").exists():
            pdf_file = data_folder / "drawings.pdf"
        elif (script_folder / "drawings.pdf").exists():
            pdf_file = script_folder / "drawings.pdf"
        elif (script_folder / "practice_test_data" / "practice_drawings.pdf").exists():
            pdf_file = script_folder / "practice_test_data" / "practice_drawings.pdf"
        else:
            pdf_file = data_folder / "drawings.pdf"

    try:
        data = json.loads(json_file.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise InputLoadError(f"could not find results.json at '{json_file}'") from error
    except OSError as error:
        raise InputLoadError(f"could not read results.json at '{json_file}': {error}") from error
    except json.JSONDecodeError as error:
        raise InputLoadError(
            f"results.json at '{json_file}' is invalid JSON at line {error.lineno}, "
            f"column {error.colno}: {error.msg}"
        ) from error

    if not isinstance(data, dict):
        raise InputLoadError("the JSON root value should be an object")

    try:
        pdf = pymupdf.open(pdf_file)
    except FileNotFoundError as error:
        raise InputLoadError(f"could not find drawings.pdf at '{pdf_file}'") from error
    except Exception as error:
        raise InputLoadError(f"could not open drawings.pdf at '{pdf_file}': {error}") from error

    return data, pdf


def _check_expected_fields(record, expected_fields, location, problems):
    """Add a message for each absent field or field with an unexpected type."""
    for field, expected_type in expected_fields.items():
        if field not in record:
            problems.append(f"{location}: missing field '{field}'")
        elif not isinstance(record[field], expected_type):
            expected_name = getattr(expected_type, "__name__", str(expected_type))
            problems.append(
                f"{location}: '{field}' should be {expected_name}, "
                f"not {type(record[field]).__name__}"
            )


def _check_region(region, location, page_width, page_height, problems, check_bounds):
    """Check the region text and coordinate shape, optionally checking page bounds."""
    if not isinstance(region, dict):
        problems.append(f"{location}: region should be an object")
        return

    if not isinstance(region.get("text"), str) or not region["text"].strip():
        problems.append(f"{location}: region has missing or empty text")

    coordinates = region.get("quad_px")
    if not isinstance(coordinates, list) or len(coordinates) != 8:
        problems.append(f"{location}: 'quad_px' should contain eight numbers")
        return

    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        for value in coordinates
    ):
        problems.append(f"{location}: 'quad_px' contains a non-numeric value")
        return

    if check_bounds:
        x_values = coordinates[0::2]
        y_values = coordinates[1::2]
        if (
            min(x_values) < 0
            or max(x_values) > page_width
            or min(y_values) < 0
            or max(y_values) > page_height
        ):
            problems.append(f"{location}: 'quad_px' extends outside the sheet bounds")


def validate_inputs(data, pdf):
    """Return structural issues found in the JSON/PDF pairing."""
    problems = []
    _check_expected_fields(data, EXPECTED_ROOT_FIELDS, "JSON root", problems)
    sheets = data.get("drawings_processing_results", [])
    if not isinstance(sheets, list):
        return problems

    seen_sheet_numbers = set()
    for sheet_index, sheet in enumerate(sheets):
        location = f"sheet record {sheet_index + 1}"
        if not isinstance(sheet, dict):
            problems.append(f"{location}: record should be an object")
            continue

        _check_expected_fields(sheet, EXPECTED_SHEET_FIELDS, location, problems)
        sheet_number = sheet.get("sheet_number")
        page_number = sheet.get("page_number")
        page_width = sheet.get("page_width", 0)
        page_height = sheet.get("page_height", 0)

        if sheet_number in seen_sheet_numbers:
            problems.append(f"{location}: duplicate sheet number '{sheet_number}'")
        seen_sheet_numbers.add(sheet_number)

        if not isinstance(page_number, int) or isinstance(page_number, bool):
            problems.append(f"{location}: 'page_number' should be an integer")
            pdf_page = None
        elif page_number < 1 or page_number > pdf.page_count:
            problems.append(
                f"{location}: page_number {page_number} is outside the PDF page range"
            )
            pdf_page = None
        else:
            pdf_page = pdf.load_page(page_number - 1)
            if isinstance(sheet_number, str) and sheet_number not in pdf_page.get_text():
                problems.append(
                    f"{location}: sheet number '{sheet_number}' was not found "
                    f"in PDF page {page_number} text"
                )

        if not isinstance(page_width, (int, float)) or not isinstance(
            page_height, (int, float)
        ):
            page_width = page_height = 0

        bid_items = sheet.get("bid_items", {})
        if isinstance(bid_items, dict):
            for package_name, items in bid_items.items():
                package_location = f"{location}, package {package_name!r}"
                if not isinstance(package_name, str) or not package_name.strip():
                    problems.append(f"{location}: package name is empty or not text")
                if not isinstance(items, list):
                    problems.append(f"{package_location}: items should be a list")
                    continue

                for item_index, item in enumerate(items):
                    item_location = f"{package_location}, item {item_index + 1}"
                    if not isinstance(item, dict):
                        problems.append(f"{item_location}: item should be an object")
                        continue

                    _check_expected_fields(
                        item,
                        {"scope": str, "bid_item": str, "regions": list, "specifications": list},
                        item_location,
                        problems,
                    )
                    if item.get("scope") != package_name:
                        problems.append(
                            f"{item_location}: item scope does not match its package key"
                        )
                    if not isinstance(item.get("bid_item"), str) or not item["bid_item"].strip():
                        problems.append(f"{item_location}: item name is missing or empty")

                    regions = item.get("regions", [])
                    if isinstance(regions, list):
                        for region_index, region in enumerate(regions):
                            _check_region(
                                region,
                                f"{item_location}, region {region_index + 1}",
                                page_width,
                                page_height,
                                problems,
                                check_bounds=True,
                            )

                    specifications = item.get("specifications", [])
                    if isinstance(specifications, list):
                        for specification_index, specification in enumerate(specifications):
                            spec_location = (
                                f"{item_location}, specification {specification_index + 1}"
                            )
                            if not isinstance(specification, dict):
                                problems.append(f"{spec_location}: should be an object")
                                continue
                            spec_regions = specification.get("regions", [])
                            if not isinstance(spec_regions, list):
                                problems.append(f"{spec_location}: 'regions' should be a list")
                                continue
                            for region_index, region in enumerate(spec_regions):
                                _check_region(
                                    region,
                                    f"{spec_location}, region {region_index + 1}",
                                    page_width,
                                    page_height,
                                    problems,
                                    check_bounds=False,
                                )

        scopes = sheet.get("scopes", [])
        if isinstance(scopes, list):
            for scope_index, scope in enumerate(scopes):
                scope_location = f"{location}, scope record {scope_index + 1}"
                if not isinstance(scope, dict):
                    problems.append(f"{scope_location}: record should be an object")
                    continue
                _check_expected_fields(
                    scope,
                    {
                        "scope": str,
                        "matched_keywords": list,
                        "identification_basis": str,
                        "regions": list,
                        "found_at_level": (str, list),
                    },
                    scope_location,
                    problems,
                )
                scope_regions = scope.get("regions", [])
                if isinstance(scope_regions, list):
                    for region_index, region in enumerate(scope_regions):
                        _check_region(
                            region,
                            f"{scope_location}, region {region_index + 1}",
                            page_width,
                            page_height,
                            problems,
                            check_bounds=True,
                        )

    for reference_sheet in sorted(REFERENCE_SHEETS - seen_sheet_numbers):
        problems.append(f"expected reference sheet '{reference_sheet}' is missing from the JSON")

    return problems