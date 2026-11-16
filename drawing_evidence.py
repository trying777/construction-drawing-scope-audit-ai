"""Extract nearby drawing evidence for prepared scope items without judging them."""

import math
import re

import pymupdf


DEFAULT_CONTEXT_MARGIN = 48


def _normalize_text(text):
    """Normalize text for conservative substring checks."""
    return re.sub(r"[^a-z0-9]+", "", text.casefold())


def _text_tokens(text):
    """Return lowercase alphanumeric tokens from text."""
    return re.findall(r"[a-z0-9]+", text.casefold())


def _value_as_text(value):
    """Convert reference values to searchable text while preserving the source separately."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(
            f"{_value_as_text(key)} {_value_as_text(child)}" for key, child in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return " ".join(_value_as_text(child) for child in value)
    return str(value)


def _region_rectangle(region, source_width, source_height, page):
    """Scale a displayed-pixel quad into PyMuPDF displayed-page point coordinates."""
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        for value in (source_width, source_height)
    ):
        return None, None
    if source_width <= 0 or source_height <= 0:
        return None, None

    coordinates = region.get("quad_px") if isinstance(region, dict) else None
    if not isinstance(coordinates, list) or len(coordinates) != 8:
        return None, None
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        for value in coordinates
    ):
        return None, None

    display_width = page.rect.width
    display_height = page.rect.height
    scale_x = display_width / source_width
    scale_y = display_height / source_height
    display_coordinates = [
        coordinate * (scale_x if index % 2 == 0 else scale_y)
        for index, coordinate in enumerate(coordinates)
    ]

    x_values = display_coordinates[0::2]
    y_values = display_coordinates[1::2]
    if (
        min(coordinates[0::2]) < 0
        or max(coordinates[0::2]) > source_width
        or min(coordinates[1::2]) < 0
        or max(coordinates[1::2]) > source_height
    ):
        return None, None
    rectangle = pymupdf.Rect(
        min(x_values), min(y_values), max(x_values), max(y_values)
    )
    return rectangle, display_coordinates


def _extract_page_words(page):
    """Read words in raw PDF points and rotate each word box once for display space."""
    rotation_matrix = page.rotation_matrix
    words = []
    for word in page.get_text("words"):
        raw_rectangle = pymupdf.Rect(word[:4])
        displayed_rectangle = raw_rectangle * rotation_matrix
        words.append({"text": word[4], "rectangle": displayed_rectangle})
    return words


def _region_evidence(region, page_words, page, source_width, source_height, context_margin):
    """Collect nearby PDF words and compare them with the source region text."""
    original_text = region.get("text", "") if isinstance(region, dict) else ""
    rectangle, display_coordinates = _region_rectangle(
        region, source_width, source_height, page
    )
    if rectangle is None:
        return {
            "source_region": region,
            "source_quad_px": region.get("quad_px") if isinstance(region, dict) else None,
            "nearby_text": "",
            "matched_pdf_text": "",
            "match_status": "unmatched_invalid_region_or_dimensions",
            "flags": [
                "Region coordinates or source page dimensions are missing, malformed, or invalid."
            ],
        }

    scale_x = page.rect.width / source_width
    scale_y = page.rect.height / source_height
    margin_x = context_margin * scale_x
    margin_y = context_margin * scale_y
    search_rectangle = pymupdf.Rect(
        rectangle.x0 - margin_x,
        rectangle.y0 - margin_y,
        rectangle.x1 + margin_x,
        rectangle.y1 + margin_y,
    )
    nearby_words = [
        word["text"]
        for word in page_words
        if search_rectangle.intersects(word["rectangle"])
    ]
    nearby_text = " ".join(nearby_words)

    source_compact = _normalize_text(original_text)
    nearby_compact = _normalize_text(nearby_text)
    source_tokens = set(_text_tokens(original_text))
    nearby_tokens = set(_text_tokens(nearby_text))

    if source_compact and source_compact in nearby_compact:
        match_status = "matched_text_near_region"
        matched_pdf_text = nearby_text
        flags = []
    elif source_tokens & nearby_tokens:
        match_status = "partial_text_near_region"
        matched_pdf_text = nearby_text
        flags = [
            "Only part of the supplied region text overlaps extracted PDF text; review manually."
        ]
    elif nearby_text:
        match_status = "nearby_text_does_not_match"
        matched_pdf_text = nearby_text
        flags = [
            "The region has nearby PDF text, but it does not match the supplied region text."
        ]
    else:
        match_status = "unmatched_no_nearby_pdf_text"
        matched_pdf_text = ""
        flags = [
            "No extractable PDF text was found within the region context window."
        ]

    return {
        "source_region": region,
        "source_quad_px": list(region.get("quad_px", [])),
        "display_coordinates": display_coordinates,
        "coordinate_space": "PyMuPDF displayed-page points",
        "context_margin_source_pixels": context_margin,
        "nearby_text": nearby_text,
        "matched_pdf_text": matched_pdf_text,
        "match_status": match_status,
        "flags": flags,
    }


def _match_text_to_pages(text, page_texts, normalized_page_texts, item_page_number):
    """Classify a text match on the item's page, another page, or nowhere."""
    searchable_text = _value_as_text(text)
    normalized_text = _normalize_text(searchable_text)
    if not normalized_text:
        return {
            "status": "not_provided",
            "matched_page_numbers": [],
            "matched_text": searchable_text,
        }

    matched_pages = [
        page_number
        for page_number, page_text in enumerate(normalized_page_texts, start=1)
        if normalized_text in page_text
    ]
    if item_page_number in matched_pages:
        status = "found_on_item_sheet"
    elif matched_pages:
        status = "found_elsewhere_only"
    else:
        status = "not_found_in_supplied_pdf"

    return {
        "status": status,
        "matched_page_numbers": matched_pages,
        "matched_text": searchable_text,
    }


def _coordinate_dimensions(item, coordinate_dimensions_by_page):
    """Get the coordinate-canvas dimensions from the item or explicit page map."""
    width = item.get("page_width")
    height = item.get("page_height")
    if width is not None and height is not None:
        return width, height

    page_number = item.get("page_number")
    dimensions = coordinate_dimensions_by_page.get(page_number)
    if isinstance(dimensions, dict):
        return dimensions.get("page_width"), dimensions.get("page_height")
    if isinstance(dimensions, (tuple, list)) and len(dimensions) == 2:
        return dimensions[0], dimensions[1]
    return None, None


def build_drawing_evidence(
    audit_items,
    pdf,
    context_margin=DEFAULT_CONTEXT_MARGIN,
    *,
    coordinate_dimensions_by_page=None,
):
    """Build page-aware evidence records without making verdicts.

    ``coordinate_dimensions_by_page`` maps one-based PDF page numbers to
    ``(page_width, page_height)`` from the source JSON sheet records. Prepared
    items do not currently include those parent-sheet dimensions, so provide the
    map when using ``prepare_scope_items``. Missing dimensions leave region
    evidence unresolved instead of silently assuming a coordinate scale.
    """
    if context_margin < 0:
        raise ValueError("context_margin must be zero or greater")
    if coordinate_dimensions_by_page is None:
        coordinate_dimensions_by_page = {}

    page_cache = {}
    page_texts = []
    for page_index in range(pdf.page_count):
        page = pdf.load_page(page_index)
        page_texts.append(page.get_text())
    normalized_page_texts = [_normalize_text(text) for text in page_texts]

    evidence_records = []
    for item in audit_items:
        flags = []
        page_number = item.get("page_number")
        page = None
        page_words = []
        page_text = ""
        rotation = None
        source_width, source_height = _coordinate_dimensions(
            item, coordinate_dimensions_by_page
        )

        if not isinstance(page_number, int) or isinstance(page_number, bool):
            flags.append("Item has no valid one-based PDF page number.")
        elif page_number < 1 or page_number > pdf.page_count:
            flags.append(f"Item page number {page_number} is outside the supplied PDF.")
        else:
            page = pdf.load_page(page_number - 1)
            page_text = page_texts[page_number - 1]
            rotation = page.rotation
            if page_number not in page_cache:
                page_cache[page_number] = _extract_page_words(page)
            page_words = page_cache[page_number]

            sheet_number = item.get("sheet_number")
            if isinstance(sheet_number, str) and sheet_number not in page_text:
                flags.append(
                    "The item's sheet number was not found in the selected PDF page text."
                )

        if page is not None and (source_width is None or source_height is None):
            flags.append(
                "Source coordinate dimensions are unavailable; region evidence is unresolved."
            )

        region_records = []
        regions = item.get("regions", [])
        if not isinstance(regions, list) or not regions:
            flags.append("Item has no usable drawing regions.")
        else:
            for region in regions:
                if page is None:
                    region_records.append(
                        {
                            "source_region": region,
                            "nearby_text": "",
                            "matched_pdf_text": "",
                            "match_status": "unmatched_invalid_page",
                            "flags": ["No valid PDF page is available for this region."],
                        }
                    )
                else:
                    region_records.append(
                        _region_evidence(
                            region,
                            page_words,
                            page,
                            source_width,
                            source_height,
                            context_margin,
                        )
                    )

            if any(
                record["match_status"] != "matched_text_near_region"
                for record in region_records
            ):
                flags.append("One or more drawing regions need manual evidence review.")

        original_references = item.get("references")
        reference_match = _match_text_to_pages(
            original_references, page_texts, normalized_page_texts, page_number
        )
        if reference_match["status"] in {
            "found_elsewhere_only",
            "not_found_in_supplied_pdf",
        }:
            flags.append("Item references are not verified on the item's sheet.")

        drawing_reference_detail = item.get("drawing_reference_detail")
        drawing_reference_match = _match_text_to_pages(
            drawing_reference_detail, page_texts, normalized_page_texts, page_number
        )
        if drawing_reference_match["status"] in {
            "found_elsewhere_only",
            "not_found_in_supplied_pdf",
        }:
            flags.append("Drawing reference detail is not verified on the item's sheet.")

        specification_records = []
        specifications = item.get("specifications", [])
        if isinstance(specifications, list):
            for specification in specifications:
                if not isinstance(specification, dict):
                    specification_records.append(
                        {
                            "source_specification": specification,
                            "excerpt_records": [],
                            "metadata_matches": {},
                            "status": "unresolved_invalid_specification_record",
                        }
                    )
                    flags.append("A specification reference has an invalid structure.")
                    continue

                metadata_matches = {}
                for field in ("specification_section_code", "specification_reference", "division"):
                    if field in specification:
                        metadata_matches[field] = _match_text_to_pages(
                            specification[field],
                            page_texts,
                            normalized_page_texts,
                            page_number,
                        )

                excerpt_records = []
                spec_regions = specification.get("regions", [])
                if isinstance(spec_regions, list):
                    for excerpt_region in spec_regions:
                        excerpt_text = (
                            excerpt_region.get("text", "")
                            if isinstance(excerpt_region, dict)
                            else ""
                        )
                        match = _match_text_to_pages(
                            excerpt_text,
                            page_texts,
                            normalized_page_texts,
                            page_number,
                        )
                        excerpt_records.append(
                            {
                                "source_region": excerpt_region,
                                "match": match,
                            }
                        )
                        if match["status"] != "found_on_item_sheet":
                            flags.append(
                                "A specification excerpt is not verified on the item's drawing sheet."
                            )

                excerpt_statuses = [
                    record["match"]["status"] for record in excerpt_records
                ]
                metadata_statuses = [
                    match["status"] for match in metadata_matches.values()
                    if match["status"] != "not_provided"
                ]
                statuses = excerpt_statuses or metadata_statuses
                if not statuses:
                    status = "no_specification_text"
                elif all(value == "found_on_item_sheet" for value in statuses):
                    status = "found_on_item_sheet"
                elif any(value in {"found_elsewhere_only", "found_on_item_sheet"} for value in statuses):
                    status = "found_elsewhere_only"
                else:
                    status = "not_found_in_supplied_pdf"

                specification_records.append(
                    {
                        "source_specification": specification,
                        "excerpt_records": excerpt_records,
                        "metadata_matches": metadata_matches,
                        "status": status,
                    }
                )
                if status not in {"found_on_item_sheet", "no_specification_text"}:
                    flags.append(
                        "Specification evidence is unresolved on the item's drawing sheet."
                    )

        evidence_records.append(
            {
                "document_id": item.get("document_id"),
                "page_number": page_number,
                "sheet_number": item.get("sheet_number"),
                "sheet_name": item.get("sheet_name"),
                "package_name": item.get("package_name"),
                "item_name": item.get("item_name"),
                "page_rotation": rotation,
                "coordinate_dimensions_px": (source_width, source_height),
                "page_text": page_text,
                "source_item": item.get("source_item"),
                "region_records": region_records,
                "references": original_references,
                "reference_match": reference_match,
                "reference_status": reference_match["status"],
                "drawing_reference_detail": drawing_reference_detail,
                "drawing_reference_match": drawing_reference_match,
                "drawing_reference_status": drawing_reference_match["status"],
                "specification_records": specification_records,
                "flags": sorted(set(flags)),
                "verdict": None,
            }
        )

    return evidence_records
