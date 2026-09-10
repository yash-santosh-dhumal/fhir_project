"""Integration tests for demo Flask app endpoints."""

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


if __name__ == "__main__":
    unittest.main()
