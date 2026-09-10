"""Unit tests for FhirStore SQLite storage layer."""

import os
import tempfile
import unittest

from demo.fhir_store import FhirStore


class TestFhirStore(unittest.TestCase):

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.store = FhirStore(db_path=self.temp_db.name)

    def tearDown(self):
        if os.path.exists(self.temp_db.name):
            try:
                os.remove(self.temp_db.name)
            except OSError:
                pass

    def test_upsert_patient_and_deduplication(self):
        patient_1 = {
            "resourceType": "Patient",
            "id": "pat-1",
            "name": [{"text": "Alice Jones"}],
            "gender": "female",
            "birthDate": "1985-05-12",
        }
        pid1 = self.store.upsert_patient(patient_1)
        self.assertEqual(pid1, "pat-1")

        # Duplicate patient with same name, gender, birth date
        patient_2 = {
            "resourceType": "Patient",
            "id": "pat-2",
            "name": [{"text": "Alice Jones"}],
            "gender": "female",
            "birthDate": "1985-05-12",
        }
        pid2 = self.store.upsert_patient(patient_2)
        # Must return the existing ID
        self.assertEqual(pid2, pid1)

        all_patients = self.store.get_all_patients()
        self.assertEqual(len(all_patients), 1)
        self.assertEqual(all_patients[0]["name"], "Alice Jones")

    def test_store_bundle_and_query(self):
        bundle = {
            "resourceType": "Bundle",
            "id": "bundle-xyz",
            "type": "document",
            "entry": [
                {
                    "resource": {
                        "resourceType": "Patient",
                        "id": "pat-bob",
                        "name": [{"text": "Bob Smith"}],
                        "gender": "male",
                        "birthDate": "1972-11-20",
                    }
                },
                {
                    "resource": {
                        "resourceType": "Observation",
                        "id": "obs-1",
                        "status": "final",
                        "code": {
                            "text": "Hemoglobin",
                            "coding": [{
                                "system": "http://loinc.org",
                                "code": "718-7",
                                "display": "Hemoglobin [Mass/volume] in Blood",
                            }],
                        },
                        "valueQuantity": {
                            "value": 14.5,
                            "unit": "g/dL",
                        },
                        "referenceRange": [{"text": "13.0 - 17.0 g/dL"}],
                    }
                },
            ],
        }

        bundle_id = self.store.store_bundle(
            bundle,
            source_filename="lab_report_bob.pdf",
            document_type="LABORATORY_REPORT",
            page_count=1,
        )
        self.assertEqual(bundle_id, "bundle-xyz")

        # Verify bundle retrieval
        stored_b = self.store.get_bundle("bundle-xyz")
        self.assertIsNotNone(stored_b)
        self.assertEqual(stored_b["source_filename"], "lab_report_bob.pdf")
        self.assertEqual(stored_b["patient_id"], "pat-bob")

        # Verify patient query
        patient = self.store.get_patient("pat-bob")
        self.assertIsNotNone(patient)
        self.assertEqual(patient["name"], "Bob Smith")

        # Verify bundles for patient
        p_bundles = self.store.get_bundles_for_patient("pat-bob")
        self.assertEqual(len(p_bundles), 1)
        self.assertEqual(p_bundles[0]["id"], "bundle-xyz")

        # Verify observations for patient
        p_obs = self.store.get_observations_for_patient("pat-bob")
        self.assertEqual(len(p_obs), 1)
        self.assertEqual(p_obs[0]["code_text"], "Hemoglobin")
        self.assertEqual(p_obs[0]["loinc_code"], "718-7")
        self.assertEqual(p_obs[0]["value"], 14.5)
        self.assertEqual(p_obs[0]["unit"], "g/dL")

    def test_processing_log_lifecycle(self):
        batch_id = "batch-101"
        log_id = self.store.log_processing(batch_id, "test_doc.pdf", status="processing")
        self.assertTrue(bool(log_id))

        status_list = self.store.get_batch_status(batch_id)
        self.assertEqual(len(status_list), 1)
        self.assertEqual(status_list[0]["status"], "processing")

        # First store a bundle so foreign key is satisfied
        bundle = {"resourceType": "Bundle", "id": "bundle-123", "entry": []}
        self.store.store_bundle(bundle, source_filename="test_doc.pdf")

        self.store.update_processing_log(
            log_id,
            status="success",
            duration_ms=450.2,
            bundle_id="bundle-123",
        )

        updated_status = self.store.get_batch_status(batch_id)
        self.assertEqual(len(updated_status), 1)
        self.assertEqual(updated_status[0]["status"], "success")
        self.assertEqual(updated_status[0]["duration_ms"], 450.2)
        self.assertEqual(updated_status[0]["bundle_id"], "bundle-123")

    def test_delete_bundle(self):
        bundle = {
            "resourceType": "Bundle",
            "id": "bundle-del-1",
            "entry": [
                {
                    "resource": {
                        "resourceType": "Patient",
                        "id": "pat-del-1",
                        "name": [{"text": "Charlie Brown"}],
                        "gender": "male",
                        "birthDate": "1990-01-01",
                    }
                },
                {
                    "resource": {
                        "resourceType": "Observation",
                        "id": "obs-del-1",
                        "status": "final",
                        "code": {"text": "Glucose"},
                        "valueQuantity": {"value": 99, "unit": "mg/dL"},
                    }
                },
            ],
        }
        self.store.store_bundle(bundle, source_filename="del_test.pdf")
        self.assertIsNotNone(self.store.get_bundle("bundle-del-1"))
        self.assertEqual(len(self.store.get_observations_for_patient("pat-del-1")), 1)

        # Delete the bundle
        res = self.store.delete_bundle("bundle-del-1")
        self.assertTrue(res)
        self.assertIsNone(self.store.get_bundle("bundle-del-1"))
        self.assertEqual(len(self.store.get_observations_for_patient("pat-del-1")), 0)
        # Since Charlie has no other bundles, patient is cleaned up
        self.assertIsNone(self.store.get_patient("pat-del-1"))

    def test_delete_patient(self):
        bundle = {
            "resourceType": "Bundle",
            "id": "bundle-del-2",
            "entry": [
                {
                    "resource": {
                        "resourceType": "Patient",
                        "id": "pat-del-2",
                        "name": [{"text": "Diana Prince"}],
                        "gender": "female",
                        "birthDate": "1985-05-05",
                    }
                },
                {
                    "resource": {
                        "resourceType": "Observation",
                        "id": "obs-del-2",
                        "status": "final",
                        "code": {"text": "Sodium"},
                        "valueQuantity": {"value": 140, "unit": "mmol/L"},
                    }
                },
            ],
        }
        self.store.store_bundle(bundle, source_filename="del_test2.pdf")
        self.assertIsNotNone(self.store.get_patient("pat-del-2"))

        # Delete the patient
        res = self.store.delete_patient("pat-del-2")
        self.assertTrue(res)
        self.assertIsNone(self.store.get_patient("pat-del-2"))
        self.assertIsNone(self.store.get_bundle("bundle-del-2"))
        self.assertEqual(len(self.store.get_observations_for_patient("pat-del-2")), 0)

    def test_stats(self):
        stats_initial = self.store.get_stats()
        self.assertEqual(stats_initial["patients"], 0)
        self.assertEqual(stats_initial["bundles"], 0)
        self.assertEqual(stats_initial["observations"], 0)


if __name__ == "__main__":
    unittest.main()
