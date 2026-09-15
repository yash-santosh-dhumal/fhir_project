"""Integration tests for demo Flask app endpoints."""

import io
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from demo.app import app, fhir_store
from demo.fhir_store import FhirStore


class TestAppEndpoints(unittest.TestCase):

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        # Override app's fhir_store with isolated store
        app.config["TESTING"] = True
        self.client = app.test_client()

    def tearDown(self):
        if os.path.exists(self.temp_db.name):
            try:
                os.remove(self.temp_db.name)
            except OSError:
                pass

    def test_index_page(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"FHIR Dashboard", resp.data)

    def test_health_endpoint(self):
        with patch("requests.get") as mock_get:
            mock_get.return_value.ok = True
            resp = self.client.get("/api/health")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertEqual(data["demo"], "ok")
            self.assertEqual(data["toolkit"], "ok")

    def test_stats_endpoint(self):
        resp = self.client.get("/api/stats")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("patients", data)
        self.assertIn("bundles", data)
        self.assertIn("observations", data)

    def test_patients_and_bundles_empty(self):
        resp = self.client.get("/api/patients")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.get_json(), list)

        resp = self.client.get("/api/bundles")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.get_json(), list)

    def test_validate_endpoint(self):
        bundle = {
            "resourceType": "Bundle",
            "type": "document",
            "entry": [
                {
                    "resource": {
                        "resourceType": "Composition",
                        "id": "comp-1",
                    }
                }
            ],
        }
        resp = self.client.post("/api/validate", json=bundle)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("compliance_score", data)
        self.assertIn("fmm_report", data)

    def test_convert_no_file(self):
        resp = self.client.post("/api/convert")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.get_json())

    def test_delete_endpoints(self):
        bundle = {
            "resourceType": "Bundle",
            "id": "bundle-test-del",
            "entry": [
                {
                    "resource": {
                        "resourceType": "Patient",
                        "id": "pat-del-test",
                        "name": [{"text": "Test Deletion"}],
                        "gender": "other",
                        "birthDate": "2000-01-01",
                    }
                }
            ],
        }
        bid = fhir_store.store_bundle(bundle, source_filename="del.pdf")
        self.assertEqual(bid, "bundle-test-del")

        # Test DELETE /api/bundles/<bundle_id>
        resp = self.client.delete(f"/api/bundles/{bid}")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["success"])

        # Test DELETE 404 for non-existent bundle
        resp_404 = self.client.delete("/api/bundles/non-existent-bundle")
        self.assertEqual(resp_404.status_code, 404)

        # Store another patient and test DELETE /api/patients/<id>
        bundle2 = {
            "resourceType": "Bundle",
            "id": "bundle-test-del-2",
            "entry": [
                {
                    "resource": {
                        "resourceType": "Patient",
                        "id": "pat-del-test-2",
                        "name": [{"text": "Test Deletion 2"}],
                        "gender": "male",
                        "birthDate": "1995-01-01",
                    }
                }
            ],
        }
        fhir_store.store_bundle(bundle2, source_filename="del2.pdf")
        resp_p = self.client.delete("/api/patients/pat-del-test-2")
        self.assertEqual(resp_p.status_code, 200)
        self.assertTrue(resp_p.get_json()["success"])

        # Test DELETE 404 for non-existent patient
        resp_p_404 = self.client.delete("/api/patients/non-existent-patient")
        self.assertEqual(resp_p_404.status_code, 404)

    def _create_test_zip(self, file_map: dict[str, bytes]) -> io.BytesIO:
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path, data in file_map.items():
                zf.writestr(path, data)
        buf.seek(0)
        return buf

    @patch("requests.post")
    def test_convert_multi_document_zip_success(self, mock_post):
        mock_bundle_1 = {
            "resourceType": "Bundle",
            "id": "bundle-part-1",
            "type": "document",
            "entry": [
                {
                    "resource": {
                        "resourceType": "Patient",
                        "id": "pat-1",
                        "name": [{"text": "Alice Doe"}],
                        "gender": "female",
                        "birthDate": "1988-04-12",
                    }
                },
                {
                    "resource": {
                        "resourceType": "Observation",
                        "id": "obs-hgb",
                        "status": "final",
                        "code": {"coding": [{"system": "http://loinc.org", "code": "718-7", "display": "Hemoglobin"}]},
                        "valueQuantity": {"value": 13.5, "unit": "g/dL"},
                    }
                },
            ],
        }
        mock_bundle_2 = {
            "resourceType": "Bundle",
            "id": "bundle-part-2",
            "type": "document",
            "entry": [
                {
                    "resource": {
                        "resourceType": "Patient",
                        "id": "pat-1",
                        "name": [{"text": "Alice Doe"}],
                        "gender": "female",
                        "birthDate": "1988-04-12",
                    }
                },
                {
                    "resource": {
                        "resourceType": "Observation",
                        "id": "obs-glu",
                        "status": "final",
                        "code": {"coding": [{"system": "http://loinc.org", "code": "2345-7", "display": "Glucose"}]},
                        "valueQuantity": {"value": 95, "unit": "mg/dL"},
                    }
                },
            ],
        }

        mock_resp_1 = MagicMock()
        mock_resp_1.ok = True
        mock_resp_1.status_code = 200
        mock_resp_1.json.return_value = {
            "standardized_medical_documents": [
                {"document_type": "LABORATORY_REPORT", "fhir_bundle": mock_bundle_1}
            ]
        }

        mock_resp_2 = MagicMock()
        mock_resp_2.ok = True
        mock_resp_2.status_code = 200
        mock_resp_2.json.return_value = {
            "standardized_medical_documents": [
                {"document_type": "LABORATORY_REPORT", "fhir_bundle": mock_bundle_2}
            ]
        }

        mock_post.side_effect = [mock_resp_1, mock_resp_2]

        zip_buf = self._create_test_zip({
            "Reports/CBC/cbc.pdf": b"%PDF-1.4 test cbc",
            "Reports/Biochemistry/glucose.jpg": b"\xff\xd8\xff test jpg",
            "Reports/notes.txt": b"doctor notes - ignored file",
        })

        resp = self.client.post(
            "/api/convert",
            data={"file": (zip_buf, "alice_records.zip")},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("is_archive"))
        archive_info = data.get("archive_info", {})
        self.assertEqual(archive_info.get("documents_unified"), 2)
        self.assertEqual(archive_info.get("skipped_count"), 1)
        self.assertIn("Reports/CBC/cbc.pdf", archive_info.get("source_files", []))
        self.assertIn("Reports/Biochemistry/glucose.jpg", archive_info.get("source_files", []))

        # Check that observations from both documents are in the unified bundle
        raw_bundle = data.get("raw", {})
        self.assertEqual(raw_bundle.get("resourceType"), "Bundle")
        resources = [e.get("resource", {}).get("resourceType") for e in raw_bundle.get("entry", [])]
        self.assertIn("Composition", resources)
        self.assertIn("Patient", resources)
        self.assertIn("Observation", resources)

        # Unified summary
        obs_summary = data.get("summary", {}).get("observations", [])
        self.assertEqual(len(obs_summary), 2)
        obs_names = [o.get("test") for o in obs_summary]
        self.assertIn("Hemoglobin", obs_names)
        self.assertIn("Glucose", obs_names)

    def test_convert_zip_no_valid_docs(self):
        zip_buf = self._create_test_zip({
            "notes.txt": b"not a supported medical document format",
            "readme.md": b"# Readme",
        })
        resp = self.client.post(
            "/api/convert",
            data={"file": (zip_buf, "invalid.zip")},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertIn("No valid medical documents", data.get("error", ""))


if __name__ == "__main__":
    unittest.main()

