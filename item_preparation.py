"""Prepare existing bid items as simple records for later evidence review."""

from input_loader import REFERENCE_SHEETS


def prepare_scope_items(data):
    """Flatten sheet/package/item nesting without editing or judging the items."""
    prepared_items = []
    sheets = data.get("drawings_processing_results", [])
    if not isinstance(sheets, list):
        return prepared_items

    for sheet in sheets:
        if not isinstance(sheet, dict):
            continue

        bid_items = sheet.get("bid_items", {})
        if not isinstance(bid_items, dict):
            continue

        for package_name, items in bid_items.items():
            if not isinstance(items, list):
                continue

            for item in items:
                if not isinstance(item, dict):
                    continue

                prepared_items.append(
                    {
                        "document_id": sheet.get("document_id"),
                        "page_number": sheet.get("page_number"),
                        "sheet_number": sheet.get("sheet_number"),
                        "sheet_name": sheet.get("sheet_name"),
                        "discipline": sheet.get("discipline"),
                        "package_name": package_name,
                        "item_name": item.get("bid_item"),
                        "scope": item.get("scope"),
                        "subheading": item.get("subheading"),
                        "regions": item.get("regions", []),
                        "specifications": item.get("specifications", []),
                        "references": item.get("references"),
                        "drawing_reference_detail": item.get(
                            "drawing_reference_detail"
                        ),
                        "notes": item.get("notes"),
                        "is_reference_sheet": sheet.get("sheet_number")
                        in REFERENCE_SHEETS,
                        "source_item": item,
                    }
                )

    return prepared_items


def select_audit_items(prepared_items):
    """Return only non-reference items for an audit or findings workflow."""
    return [
        item
        for item in prepared_items
        if not item.get("is_reference_sheet", False)
    ]