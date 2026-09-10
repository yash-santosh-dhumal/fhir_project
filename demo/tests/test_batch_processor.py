"""Unit tests for BatchProcessor."""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from demo.batch_processor import BatchProcessor, infer_mime_type
from demo.fhir_store import FhirStore


class TestBatchProcessor(unittest.TestCase):

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.store = FhirStore(db_path=self.temp_db.name)
        self.processor = BatchProcessor(
            toolkit_url="http://mock-toolkit:8080",
            fhir_store=self.store,
            timeout_seconds=5.0,
            max_workers=2,
        )

    def tearDown(self):
        if os.path.exists(self.temp_db.name):
            try:
                os.remove(self.temp_db.name)
            except OSError:
                pass

    def test_infer_mime_type(self):
        self.assertEqual(infer_mime_type("report.pdf"), "application/pdf")
        self.assertEqual(infer_mime_type("scan.jpg"), "image/jpeg")
        self.assertEqual(infer_mime_type("scan.jpeg"), "image/jpeg")
        self.assertEqual(infer_mime_type("test.PNG"), "image/png")
        self.assertEqual(infer_mime_type("file.txt"), "application/octet-stream")

    def test_process_empty_file(self):
        result = self.processor.process_document("empty.pdf", b"")
        self.assertEqual(result.status, "error")
        self.assertIn("Empty file", result.error_message)

    @patch("requests.post")
    def test_process_document_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "standardized_medical_documents": [
                {
                    "document_type": "LABORATORY_REPORT",
                    "fhir_bundle": {
                        "resourceType": "Bundle",
                        "id": "mock-bundle-1",
                        "type": "document",
                        "meta": {
                            "profile": ["https://nrces.in/ndhm/fhir/r4/StructureDefinition/DocumentBundle"]
                        },
                        "entry": [
                            {
                                "resource": {
                                    "resourceType": "Composition",
                                    "id": "comp-1",
                                    "meta": {
                                        "profile": ["https://nrces.in/ndhm/fhir/r4/StructureDefinition/DiagnosticReportRecord"]
                                    },
                                }
                            },
                            {
                                "resource": {
                                    "resourceType": "Patient",
                                    "id": "pat-1",
                                    "name": [{"text": "Suresh Gupta"}],
                                    "gender": "male",
                                    "birthDate": "1980-01-15",
                                    "meta": {
                                        "profile": ["https://nrces.in/ndhm/fhir/r4/StructureDefinition/Patient"]
                                    },
                                }
                            },
                            {
                                "resource": {
                                    "resourceType": "DiagnosticReport",
                                    "id": "diag-1",
                                    "meta": {
                                        "profile": ["https://nrces.in/ndhm/fhir/r4/StructureDefinition/DiagnosticReportLab"]
                                    },
                                }
                            },
                            {
                                "resource": {
                                    "resourceType": "Observation",
                                    "id": "obs-1",
                                    "status": "final",
                                    "code": {
                                        "coding": [{"system": "http://loinc.org", "code": "2345-7"}],
                                        "text": "Glucose",
                                    },
                                    "valueQuantity": {"value": 110, "unit": "mg/dL"},
                                    "meta": {
                                        "profile": ["https://nrces.in/ndhm/fhir/r4/StructureDefinition/Observation"]
                                    },
                                }
                            },
                        ],
                    },
                }
            ]
        }
        mock_post.return_value = mock_response

        result = self.processor.process_document("lab1.pdf", b"%PDF-1.4 dummy", "application/pdf")
        self.assertEqual(result.status, "success")
        self.assertEqual(result.bundle_id, "mock-bundle-1")
        self.assertEqual(result.observation_count, 1)
        self.assertIsNotNone(result.compliance_score)

        # Verify stored in SQLite
        bundle = self.store.get_bundle("mock-bundle-1")
        self.assertIsNotNone(bundle)
        self.assertEqual(bundle["source_filename"], "lab1.pdf")

    @patch("requests.post")
    def test_process_document_http_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.status_code = 502
        mock_response.json.return_value = {"error": "Upstream service crashed"}
        mock_post.return_value = mock_response

        result = self.processor.process_document("bad.pdf", b"%PDF-1.4 dummy", "application/pdf")
        self.assertEqual(result.status, "error")
        self.assertIn("502", result.error_message)

    @patch("requests.post")
    def test_process_files_batch(self, mock_post):
        # First file succeeds, second file fails
        succ_resp = MagicMock()
        succ_resp.ok = True
        succ_resp.status_code = 200
        succ_resp.json.return_value = {
            "standardized_medical_documents": [
                {
                    "fhir_bundle": {
                        "resourceType": "Bundle",
                        "id": "b-ok",
                        "type": "document",
                        "entry": [
                            {"resource": {"resourceType": "Composition", "id": "c1"}},
                            {"resource": {"resourceType": "Patient", "id": "p1", "name": [{"text": "P1"}]}},
                            {"resource": {"resourceType": "DiagnosticReport", "id": "d1"}},
                            {"resource": {"resourceType": "Observation", "id": "o1", "status": "final", "code": {"text": "T1"}}},
                        ],
                    }
                }
            ]
        }
        fail_resp = MagicMock()
        fail_resp.ok = False
        fail_resp.status_code = 422
        fail_resp.json.return_value = {"error": "Unprocessable report"}

        mock_post.side_effect = [succ_resp, fail_resp]

        progress_calls = []

        def on_prog(done, total, item):
            progress_calls.append((done, total, item.status))

        files = [
            ("doc1.pdf", b"pdf1", "application/pdf"),
            ("doc2.pdf", b"pdf2", "application/pdf"),
        ]

        summary = self.processor.process_files(
            files,
            batch_id="b-123",
            progress_callback=on_prog,
            concurrent_execution=False,
        )

        self.assertEqual(summary.total, 2)
        self.assertEqual(summary.successful, 1)
        self.assertEqual(summary.failed, 1)
        self.assertEqual(len(progress_calls), 2)


if __name__ == "__main__":
    unittest.main()
