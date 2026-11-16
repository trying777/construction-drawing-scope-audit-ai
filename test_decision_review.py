"""Offline unit tests for Gemini-assisted decision review safeguards."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import decision_review


class MockAPIError(Exception):
    """Small stand-in for an SDK API exception."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class FakeResponse:
    """Response returned by the fake Gemini client."""

    def __init__(self, text):
        self.text = text


class FakeModels:
    """Fake models endpoint that counts requests and returns or raises in order."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.request_count = 0
        self.requested_models = []

    def generate_content(self, **kwargs):
        self.request_count += 1
        self.requested_models.append(kwargs.get("model"))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeClient:
    """Fake client with the same models.generate_content access pattern."""

    def __init__(self, responses):
        self.models = FakeModels(responses)


class DecisionReviewTests(unittest.TestCase):
    def setUp(self):
        source_item = {
            "scope": "Roofing",
            "bid_item": "Install roof membrane",
            "subheading": "Roofing membrane",
            "specifications": [],
        }
        self.input_data = {
            "drawings_processing_results": [
                {
                    "document_id": "doc-test",
                    "sheet_number": "A-005",
                    "page_number": 1,
                    "bid_items": {"Roofing": [source_item]},
                }
            ]
        }
        self.record = {
            "document_id": "doc-test",
            "sheet_number": "A-005",
            "sheet_name": "Roof Plan",
            "page_number": 1,
            "package_name": "Roofing",
            "item_name": "Install roof membrane",
            "source_item": source_item,
            "region_records": [
                {
                    "source_region": {
                        "text": "PROVIDE ROOF MEMBRANE",
                        "quad_px": [10, 10, 20, 10, 20, 20, 10, 20],
                    },
                    "source_quad_px": [10, 10, 20, 10, 20, 20, 10, 20],
                    "nearby_text": "PROVIDE ROOF MEMBRANE",
                    "matched_pdf_text": "PROVIDE ROOF MEMBRANE",
                    "match_status": "matched_text_near_region",
                    "flags": [],
                }
            ],
            "specification_records": [],
            "references": None,
            "reference_match": {"status": "not_provided", "matched_page_numbers": []},
            "drawing_reference_detail": None,
            "drawing_reference_match": {
                "status": "not_provided",
                "matched_page_numbers": [],
            },
            "flags": [],
            "page_text": "PROVIDE ROOF MEMBRANE",
        }
        self.evidence_sources = decision_review._evidence_sources(self.record, [])
        self.region_evidence_id = next(
            source["evidence_id"]
            for source in self.evidence_sources
            if source["kind"] == "drawing_region"
        )

    def make_model_response(self, **overrides):
        response = {
            "proposed_verdict": "RIGHT",
            "reason": "A drawing note supports the item as written.",
            "supporting_evidence_ids": [self.region_evidence_id],
            "confidence": "high",
            "uncertainty": "",
            "human_review": False,
        }
        response.update(overrides)
        return json.dumps(response)

    def test_valid_review_response_with_valid_evidence_citation(self):
        result = decision_review._validate_response(
            self.make_model_response(),
            self.record,
            "item-test",
            "drawings_processing_results[0]",
            self.evidence_sources,
        )

        self.assertEqual(result["proposed_verdict"], "RIGHT")
        self.assertFalse(result["human_review"])
        self.assertEqual(result["supporting_evidence"][0]["evidence_id"], self.region_evidence_id)
        self.assertEqual(result["source_sheet_number"], "A-005")
        self.assertEqual(result["original_item_name"], "Install roof membrane")

    def test_malformed_and_invalid_model_responses_are_rejected(self):
        with self.assertRaises(decision_review.ReviewResponseError):
            decision_review._validate_response(
                "not JSON",
                self.record,
                "item-test",
                "source-path",
                self.evidence_sources,
            )

        with self.assertRaises(decision_review.ReviewResponseError):
            decision_review._validate_response(
                self.make_model_response(proposed_verdict="MAYBE"),
                self.record,
                "item-test",
                "source-path",
                self.evidence_sources,
            )

    def test_missing_or_unresolved_evidence_requires_human_review(self):
        no_citations = self.make_model_response(supporting_evidence_ids=[])
        missing_result = decision_review._validate_response(
            no_citations,
            self.record,
            "item-test",
            "source-path",
            self.evidence_sources,
        )
        self.assertEqual(missing_result["proposed_verdict"], "RIGHT")
        self.assertTrue(missing_result["human_review"])
        self.assertEqual(missing_result["status"], "human_review")
        self.assertIn("No verified supporting evidence", missing_result["uncertainty"])

        unresolved_specification = {
            "evidence_id": "specification:A-005:1:1:1",
            "kind": "specification_excerpt",
            "verified": False,
            "source_region": {"text": "Not in supplied PDF"},
        }
        sources = self.evidence_sources + [unresolved_specification]
        cited_unresolved_spec = self.make_model_response(
            supporting_evidence_ids=[unresolved_specification["evidence_id"]]
        )
        unresolved_result = decision_review._validate_response(
            cited_unresolved_spec,
            self.record,
            "item-test",
            "source-path",
            sources,
        )
        self.assertEqual(unresolved_result["proposed_verdict"], "RIGHT")
        self.assertTrue(unresolved_result["human_review"])
        self.assertEqual(unresolved_result["supporting_evidence"], [])
        self.assertEqual(len(unresolved_result["unverified_cited_evidence"]), 1)
        self.assertIn("A cited reference was not verified", unresolved_result["uncertainty"])

        partial_record = dict(self.record)
        partial_record["region_records"] = [
            dict(self.record["region_records"][0], match_status="partial_text_near_region")
        ]
        partial_source = decision_review._evidence_sources(partial_record, [])[0]
        fake_client = FakeClient(
            [FakeResponse(self.make_model_response(
                supporting_evidence_ids=[partial_source["evidence_id"]]
            ))]
        )
        with patch.object(
            decision_review,
            "_make_client_and_config",
            return_value=(fake_client, object()),
        ):
            partial_result = decision_review.review_audit_items(
                [partial_record],
                {"drawings_processing_results": []},
                pdf=object(),
            )[0]
        self.assertEqual(partial_result["proposed_verdict"], "RIGHT")
        self.assertTrue(partial_result["human_review"])
        self.assertEqual(partial_result["status"], "human_review")

    def test_temporary_rate_limit_is_not_daily_quota_exhaustion(self):
        error = MockAPIError("Rate limit exceeded; retry shortly.", status_code=429)
        self.assertFalse(decision_review._is_daily_quota_error(error))
        self.assertTrue(decision_review._is_retryable(error))

    def test_daily_quota_message_variants_are_detected(self):
        messages = (
            "You exceeded your current quota",
            "Quota exceeded for metric: generate_content_free_tier_requests",
            "The daily quota has been reached",
        )
        for message in messages:
            with self.subTest(message=message):
                self.assertTrue(decision_review._is_daily_quota_error(MockAPIError(message, 429)))

    def test_daily_quota_stops_requests_and_marks_remaining_items_unreviewed(self):
        records = []
        for item_number in range(3):
            source_item = {"bid_item": f"Item {item_number + 1}", "scope": "Roofing"}
            record = dict(self.record)
            record["item_name"] = source_item["bid_item"]
            record["source_item"] = source_item
            records.append(record)

        fake_client = FakeClient(
            [MockAPIError("You exceeded your current quota", status_code=429)]
        )
        with (
            patch.object(decision_review, "_make_client_and_config", return_value=(fake_client, object())),
            patch.object(decision_review.time, "sleep") as sleep_mock,
        ):
            results = decision_review.review_audit_items(
                records,
                {"drawings_processing_results": []},
                pdf=object(),
            )

        self.assertEqual(fake_client.models.request_count, 1)
        sleep_mock.assert_not_called()
        self.assertEqual(len(results), len(records))
        self.assertEqual(results[0]["status"], "daily_quota_exhausted")
        self.assertIsNone(results[0]["proposed_verdict"])
        self.assertTrue(results[0]["human_review"])
        for result in results[1:]:
            self.assertEqual(result["status"], "not_attempted_quota_exhausted")
            self.assertIsNone(result["proposed_verdict"])
            self.assertTrue(result["human_review"])

    def test_model_selection_default(self):
        fake_client = FakeClient([FakeResponse(self.make_model_response())])
        with (
            patch.object(decision_review, "_make_client_and_config", return_value=(fake_client, object())),
            patch.dict(decision_review.os.environ, {}, clear=True),
        ):
            decision_review.review_audit_items([self.record], {"drawings_processing_results": []}, pdf=object())
        self.assertEqual(fake_client.models.requested_models, ["gemini-3.8-flash"])

    def test_model_selection_env_override(self):
        fake_client = FakeClient([FakeResponse(self.make_model_response())])
        with (
            patch.object(decision_review, "_make_client_and_config", return_value=(fake_client, object())),
            patch.dict(decision_review.os.environ, {"GEMINI_MODEL": "custom-flash-model"}),
        ):
            decision_review.review_audit_items([self.record], {"drawings_processing_results": []}, pdf=object())
        self.assertEqual(fake_client.models.requested_models, ["custom-flash-model"])

    def test_model_selection_explicit_parameter(self):
        fake_client = FakeClient([FakeResponse(self.make_model_response())])
        with patch.object(decision_review, "_make_client_and_config", return_value=(fake_client, object())):
            decision_review.review_audit_items(
                [self.record],
                {"drawings_processing_results": []},
                pdf=object(),
                model="explicit-model-override",
            )
        self.assertEqual(fake_client.models.requested_models, ["explicit-model-override"])

    def test_transient_503_retry_and_recovery(self):
        fake_client = FakeClient([
            MockAPIError("503 Server Unavailable", status_code=503),
            FakeResponse(self.make_model_response()),
        ])
        with (
            patch.object(decision_review, "_make_client_and_config", return_value=(fake_client, object())),
            patch.object(decision_review.time, "sleep") as sleep_mock,
        ):
            results = decision_review.review_audit_items([self.record], {"drawings_processing_results": []}, pdf=object())
        self.assertEqual(fake_client.models.request_count, 2)
        self.assertEqual(sleep_mock.call_count, 1)
        self.assertEqual(results[0]["proposed_verdict"], "RIGHT")
        self.assertEqual(results[0]["status"], "completed")
        self.assertFalse(results[0]["human_review"])

    def test_retry_limit_exhaustion_preserves_unreviewed(self):
        fake_client = FakeClient([
            MockAPIError("503 Server Unavailable", status_code=503),
            MockAPIError("503 Server Unavailable", status_code=503),
            MockAPIError("503 Server Unavailable", status_code=503),
        ])
        with (
            patch.object(decision_review, "_make_client_and_config", return_value=(fake_client, object())),
            patch.object(decision_review.time, "sleep") as sleep_mock,
        ):
            results = decision_review.review_audit_items([self.record], {"drawings_processing_results": []}, pdf=object())
        self.assertEqual(fake_client.models.request_count, 3)
        self.assertEqual(sleep_mock.call_count, 2)
        self.assertIsNone(results[0]["proposed_verdict"])
        self.assertTrue(results[0]["human_review"])
        self.assertEqual(results[0]["status"], "api_or_response_error")
        self.assertIn("503 Server Unavailable", results[0]["error"])

    def test_request_pacing_delay_between_records(self):
        records = []
        for i in range(3):
            r = dict(self.record)
            r["item_name"] = f"Item {i + 1}"
            records.append(r)
        fake_client = FakeClient([
            FakeResponse(self.make_model_response()),
            FakeResponse(self.make_model_response()),
            FakeResponse(self.make_model_response()),
        ])
        with (
            patch.object(decision_review, "_make_client_and_config", return_value=(fake_client, object())),
            patch.object(decision_review.time, "sleep") as sleep_mock,
        ):
            results = decision_review.review_audit_items(
                records,
                {"drawings_processing_results": []},
                pdf=object(),
                request_delay=12.0,
            )
        self.assertEqual(fake_client.models.request_count, 3)
        self.assertEqual(sleep_mock.call_count, 2)
        sleep_mock.assert_called_with(12.0)

    def test_retry_after_seconds_parsing(self):
        err = MockAPIError("Quota exceeded. Please retry in 43.895s.", status_code=429)
        self.assertAlmostEqual(decision_review._retry_after_seconds(err), 43.895)
        err_no_delay = MockAPIError("Some other rate limit error", status_code=429)
        self.assertIsNone(decision_review._retry_after_seconds(err_no_delay))

    def test_checkpoint_skips_already_completed_items(self):
        with tempfile.TemporaryDirectory() as directory:
            cp_path = Path(directory) / "checkpoint.json"
            completed_result = {
                "item_identifier": "item-cached",
                "source_json_path": "drawings_processing_results[0].bid_items[\"Roofing\"][0]",
                "source_sheet_number": "A-005",
                "package": "Roofing",
                "original_item_name": "Install roof membrane",
                "proposed_verdict": "RIGHT",
                "reason": "Cached completed verdict.",
                "status": "completed",
                "human_review": False,
                "error": None,
            }
            cp_path.write_text(json.dumps([completed_result]), encoding="utf-8")

            fake_client = FakeClient([FakeResponse(self.make_model_response())])
            with patch.object(decision_review, "_make_client_and_config", return_value=(fake_client, object())):
                results = decision_review.review_audit_items(
                    [self.record],
                    self.input_data,
                    pdf=object(),
                    checkpoint_path=cp_path,
                )
            self.assertEqual(fake_client.models.request_count, 0)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["proposed_verdict"], "RIGHT")
            self.assertEqual(results[0]["reason"], "Cached completed verdict.")

    def test_checkpoint_retries_errored_or_incomplete_items(self):
        with tempfile.TemporaryDirectory() as directory:
            cp_path = Path(directory) / "checkpoint.json"
            errored_result = {
                "item_identifier": "item-cached",
                "source_json_path": "drawings_processing_results[0].bid_items[\"Roofing\"][0]",
                "source_sheet_number": "A-005",
                "package": "Roofing",
                "original_item_name": "Install roof membrane",
                "proposed_verdict": None,
                "reason": "Temporary error",
                "status": "api_error",
                "human_review": True,
                "error": "ServerError: 503",
            }
            cp_path.write_text(json.dumps([errored_result]), encoding="utf-8")

            fake_client = FakeClient([FakeResponse(self.make_model_response())])
            with patch.object(decision_review, "_make_client_and_config", return_value=(fake_client, object())):
                results = decision_review.review_audit_items(
                    [self.record],
                    self.input_data,
                    pdf=object(),
                    checkpoint_path=cp_path,
                )
            self.assertEqual(fake_client.models.request_count, 1)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["proposed_verdict"], "RIGHT")
            self.assertEqual(results[0]["status"], "completed")

    def test_batch_limit_restricts_number_of_processed_items(self):
        records = []
        for i in range(3):
            r = dict(self.record)
            r["item_name"] = f"Batch Item {i+1}"
            records.append(r)

        fake_client = FakeClient([
            FakeResponse(self.make_model_response()),
        ])
        with patch.object(decision_review, "_make_client_and_config", return_value=(fake_client, object())):
            results = decision_review.review_audit_items(
                records,
                self.input_data,
                pdf=object(),
                batch_limit=1,
            )
        self.assertEqual(fake_client.models.request_count, 1)
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["status"], "completed")
        self.assertEqual(results[1]["status"], "not_attempted_batch_limit")
        self.assertEqual(results[2]["status"], "not_attempted_batch_limit")
        self.assertIsNone(results[1]["proposed_verdict"])
        self.assertTrue(results[1]["human_review"])

    def test_incremental_checkpoint_saves_to_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            cp_path = Path(directory) / "checkpoint.json"
            fake_client = FakeClient([FakeResponse(self.make_model_response())])
            with patch.object(decision_review, "_make_client_and_config", return_value=(fake_client, object())):
                results = decision_review.review_audit_items(
                    [self.record],
                    self.input_data,
                    pdf=object(),
                    checkpoint_path=cp_path,
                )
            self.assertTrue(cp_path.is_file())
            saved_data = json.loads(cp_path.read_text(encoding="utf-8"))
            self.assertEqual(len(saved_data), 1)
            self.assertEqual(saved_data[0]["proposed_verdict"], "RIGHT")


if __name__ == "__main__":
    unittest.main()
