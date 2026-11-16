"""Synthetic, offline tests for schema-preserving audit output writing."""

import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path

from input_loader import REFERENCE_SHEETS
from output_writer import (
    FINDINGS_COLUMNS,
    build_corrected_data,
    build_findings_rows,
    write_corrected_json,
    write_findings_csv,
)


class OutputWriterTests(unittest.TestCase):
    def setUp(self):
        self.reference_item = self._item("Legend entry", "Reference notes")
        self.right_item = self._item("Repeated item", "Keep this original")
        self.wrong_item = self._item("Repeated item", "This matching item is WRONG")
        self.human_item = self._item("Needs review", "Keep pending review")
        self.failed_item = self._item("API failed", "Keep after API failure")

        self.input_data = {
            "drawings_processing_results": [
                self._sheet("G-002.3", 1, {"General Requirements": [self.reference_item]}),
                self._sheet(
                    "E-101",
                    2,
                    {
                        "Electrical": [self.right_item, self.wrong_item],
                        "Controls": [self.human_item],
                        "Mechanical": [self.failed_item],
                    },
                ),
            ],
            "specs_processing_results": [],
            "mixed_document_ids": [],
            "custom_root_metadata": {"preserve": ["untouched"]},
        }
        self.original_snapshot = copy.deepcopy(self.input_data)

        self.review_results = [
            self._result(
                sheet_index=1,
                package="Electrical",
                item_index=0,
                item=self.right_item,
                verdict="RIGHT",
                reason="Drawing evidence supports this instance.",
                status="completed",
                human_review=False,
                evidence=[{"evidence_id": "drawing:E-101:2:region:1", "page_number": 2}],
            ),
            self._result(
                sheet_index=1,
                package="Electrical",
                item_index=1,
                item=self.wrong_item,
                verdict="WRONG",
                reason="The cited region does not support this item as written.",
                status="completed",
                human_review=False,
                evidence=[{"evidence_id": "drawing:E-101:2:region:2", "page_number": 2}],
            ),
            self._result(
                sheet_index=1,
                package="Controls",
                item_index=0,
                item=self.human_item,
                verdict=None,
                reason="Evidence is ambiguous.",
                status="human_review",
                human_review=True,
            ),
            self._result(
                sheet_index=1,
                package="Mechanical",
                item_index=0,
                item=self.failed_item,
                verdict=None,
                reason="No model judgment was returned.",
                status="api_or_response_error",
                human_review=True,
                error="ServerError: temporary failure",
            ),
        ]

    @staticmethod
    def _item(name, notes):
        return {
            "scope": "Test package",
            "bid_item": name,
            "subheading": "Synthetic subheading",
            "specifications": [],
            "references": None,
            "drawing_reference_detail": "",
            "notes": notes,
            "regions": [
                {
                    "quad_px": [1, 2, 3, 2, 3, 4, 1, 4],
                    "text": "Synthetic source region",
                }
            ],
            "user_metadata": {"keep": True},
        }

    @staticmethod
    def _sheet(sheet_number, page_number, packages):
        return {
            "document_id": "synthetic-doc",
            "page_number": page_number,
            "sheet_name": f"Sheet {sheet_number}",
            "sheet_number": sheet_number,
            "discipline": "Test",
            "issue_chart": "",
            "page_width": 3024.0,
            "page_height": 2160.0,
            "scopes": [],
            "schedules": [],
            "bid_items": packages,
            "callouts": [],
            "custom_sheet_metadata": "preserve",
        }

    @staticmethod
    def _source_path(sheet_index, package, item_index):
        package_json = json.dumps(package, ensure_ascii=True)
        return (
            f"drawings_processing_results[{sheet_index}].bid_items"
            f"[{package_json}][{item_index}]"
        )

    def _result(
        self,
        *,
        sheet_index,
        package,
        item_index,
        item,
        verdict,
        reason,
        status,
        human_review,
        evidence=None,
        error=None,
    ):
        sheet = self.input_data["drawings_processing_results"][sheet_index]
        return {
            "item_identifier": f"synthetic-{sheet_index}-{package}-{item_index}",
            "source_document_id": sheet["document_id"],
            "source_sheet_number": sheet["sheet_number"],
            "source_page_number": sheet["page_number"],
            "source_json_path": self._source_path(sheet_index, package, item_index),
            "source_item": item,
            "package": package,
            "original_item_name": item["bid_item"],
            "proposed_verdict": verdict,
            "reason": reason,
            "supporting_evidence": evidence or [],
            "confidence": "high" if verdict in {"RIGHT", "WRONG"} else "none",
            "uncertainty": "" if verdict in {"RIGHT", "WRONG"} else reason,
            "human_review": human_review,
            "status": status,
            "error": error,
        }

    def test_corrected_copy_removes_only_confirmed_wrong_exact_item(self):
        corrected = build_corrected_data(self.input_data, self.review_results)
        audit_sheet = corrected["drawings_processing_results"][1]

        self.assertEqual(
            [item["notes"] for item in audit_sheet["bid_items"]["Electrical"]],
            ["Keep this original"],
        )
        self.assertEqual(audit_sheet["bid_items"]["Controls"], [self.human_item])
        self.assertEqual(audit_sheet["bid_items"]["Mechanical"], [self.failed_item])
        self.assertEqual(
            corrected["drawings_processing_results"][0]["bid_items"]["General Requirements"],
            [self.reference_item],
        )

    def test_original_input_remains_unchanged(self):
        build_corrected_data(self.input_data, self.review_results)
        self.assertEqual(self.input_data, self.original_snapshot)

    def test_corrected_copy_preserves_root_and_nested_schema(self):
        corrected = build_corrected_data(self.input_data, self.review_results)
        self.assertEqual(set(corrected), set(self.original_snapshot))
        self.assertEqual(
            set(corrected["drawings_processing_results"][0]),
            set(self.original_snapshot["drawings_processing_results"][0]),
        )
        self.assertEqual(
            set(corrected["drawings_processing_results"][1]["bid_items"]["Electrical"][0]),
            set(self.right_item),
        )
        self.assertEqual(corrected["custom_root_metadata"], {"preserve": ["untouched"]})
        self.assertNotIn("verdict", corrected["drawings_processing_results"][1]["bid_items"]["Electrical"][0])

    def test_reference_item_is_never_removed(self):
        reference_result = self._result(
            sheet_index=0,
            package="General Requirements",
            item_index=0,
            item=self.reference_item,
            verdict="WRONG",
            reason="Synthetic reference judgment must be ignored.",
            status="completed",
            human_review=False,
        )
        corrected = build_corrected_data(self.input_data, [reference_result])
        self.assertEqual(
            corrected["drawings_processing_results"][0]["bid_items"]["General Requirements"],
            [self.reference_item],
        )

    def test_ambiguous_duplicate_review_paths_do_not_remove_item(self):
        wrong_result = self.review_results[1]
        duplicate_conflict = dict(wrong_result, proposed_verdict="RIGHT")
        corrected = build_corrected_data(
            self.input_data, [wrong_result, duplicate_conflict]
        )
        electrical = corrected["drawings_processing_results"][1]["bid_items"]["Electrical"]
        self.assertEqual(len(electrical), 2)

    def test_writer_refuses_to_overwrite_results_json(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "results.json"
            with self.assertRaises(ValueError):
                write_corrected_json(self.input_data, self.review_results, output_path)
            with self.assertRaises(ValueError):
                write_findings_csv(self.review_results, output_path)
            self.assertFalse(output_path.exists())

    def test_csv_has_required_columns_and_one_row_per_review_record(self):
        rows = build_findings_rows(self.review_results)
        self.assertEqual(len(rows), len(self.review_results))
        self.assertTrue(
            {"sheet_number", "scope_package", "item_name", "verdict", "reason"}
            .issubset(FINDINGS_COLUMNS)
        )
        self.assertTrue(
            {"source_sheet_number", "package", "original_item_name", "verdict", "reason"}
            .issubset(FINDINGS_COLUMNS)
        )
        self.assertEqual(rows[0]["verdict"], "RIGHT")
        self.assertEqual(rows[0]["review_classification"], "judged")
        self.assertEqual(rows[1]["verdict"], "WRONG")
        self.assertEqual(rows[1]["review_classification"], "judged")
        # An item with no proposed verdict due to ambiguity or failure has empty verdict and human_review True
        self.assertEqual(rows[2]["verdict"], "")
        self.assertEqual(rows[2]["review_classification"], "not_judged")
        self.assertTrue(rows[2]["human_review"])
        self.assertEqual(rows[3]["verdict"], "")
        self.assertEqual(rows[3]["review_classification"], "not_judged")
        self.assertEqual(rows[3]["status"], "api_or_response_error")
        self.assertTrue(rows[3]["human_review"])

        # An item judged WRONG with human_review retains binary WRONG verdict
        flagged_wrong_result = dict(
            self.review_results[1],
            proposed_verdict="WRONG",
            status="human_review",
            human_review=True,
        )
        flagged_row = build_findings_rows([flagged_wrong_result])[0]
        self.assertEqual(flagged_row["verdict"], "WRONG")
        self.assertEqual(flagged_row["review_classification"], "human_review")
        self.assertTrue(flagged_row["human_review"])

    def test_csv_writer_round_trips_expected_rows_and_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "findings.csv"
            returned_path = write_findings_csv(self.review_results, output_path)
            self.assertEqual(returned_path, output_path.resolve())
            with output_path.open(encoding="utf-8", newline="") as csv_file:
                reader = csv.DictReader(csv_file)
                rows = list(reader)
            self.assertEqual(reader.fieldnames, FINDINGS_COLUMNS)
            self.assertEqual(len(rows), 4)
            self.assertEqual(rows[1]["original_item_name"], "Repeated item")
            self.assertEqual(rows[1]["source_json_path"], self.review_results[1]["source_json_path"])
            self.assertEqual(rows[3]["verdict"], "")
            self.assertEqual(rows[3]["error"], "ServerError: temporary failure")

    def test_missing_optional_result_fields_are_safe_in_csv(self):
        sparse_result = {
            "source_sheet_number": "A-999",
            "package": "Unclassified",
            "original_item_name": "Item with partial review record",
            "reason": "Requires follow-up.",
            "status": "api_error",
            "proposed_verdict": None,
        }
        rows = build_findings_rows([sparse_result])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_sheet_number"], "A-999")
        self.assertEqual(rows[0]["verdict"], "")
        self.assertEqual(rows[0]["supporting_evidence"], "[]")
        self.assertEqual(rows[0]["confidence"], "")
        self.assertEqual(rows[0]["review_classification"], "not_judged")

    def test_json_writer_writes_copy_and_leaves_original_object_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            original_path = Path(directory) / "input" / "results.json"
            original_path.parent.mkdir()
            original_path.write_text(
                json.dumps(self.input_data, ensure_ascii=False), encoding="utf-8"
            )
            output_path = Path(directory) / "corrected_results.json"
            written_path = write_corrected_json(
                self.input_data,
                self.review_results,
                output_path,
                original_input_path=original_path,
            )
            self.assertEqual(written_path, output_path.resolve())
            self.assertEqual(self.input_data, self.original_snapshot)
            self.assertEqual(
                json.loads(original_path.read_text(encoding="utf-8")),
                self.original_snapshot,
            )
            corrected_from_disk = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(set(corrected_from_disk), set(self.original_snapshot))
            self.assertEqual(
                len(corrected_from_disk["drawings_processing_results"][1]["bid_items"]["Electrical"]),
                1,
            )


if __name__ == "__main__":
    unittest.main()
