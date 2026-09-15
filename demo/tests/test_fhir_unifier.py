"""Unit tests for demo/fhir_unifier.py."""

import unittest

from demo.fhir_unifier import unify_patient_bundles
from demo.fhir_validator import validate_bundle


class TestFhirUnifier(unittest.TestCase):

    def setUp(self):
        # Sample bundle 1 (CBC report for John Doe)
        self.bundle1 = {
            "resourceType": "Bundle",
            "type": "document",
            "entry": [
                {
                    "resource": {
                        "resourceType": "Patient",
                        "id": "pt-1",
                        "name": [{"text": "John Doe"}],
                        "gender": "male",
                        "birthDate": "1985-06-15",
                    }
                },
                {
                    "resource": {
                        "resourceType": "Observation",
                        "id": "obs-hemoglobin",
                        "code": {
                            "coding": [{"system": "http://loinc.org", "code": "718-7", "display": "Hemoglobin"}],
                            "text": "Hemoglobin",
                        },
                        "valueQuantity": {"value": 14.5, "unit": "g/dL"},
                    }
                },
            ],
        }

        # Sample bundle 2 (Lipid Panel for John Doe)
        self.bundle2 = {
            "resourceType": "Bundle",
            "type": "document",
            "entry": [
                {
                    "resource": {
                        "resourceType": "Patient",
                        "id": "pt-2",
                        "name": [{"text": "John Doe"}],
                        "gender": "male",
                        "birthDate": "1985-06-15",
                    }
                },
                {
                    "resource": {
                        "resourceType": "Observation",
                        "id": "obs-cholesterol",
                        "code": {
                            "coding": [{"system": "http://loinc.org", "code": "2093-3", "display": "Cholesterol"}],
                            "text": "Cholesterol",
                        },
                        "valueQuantity": {"value": 195, "unit": "mg/dL"},
                    }
                },
            ],
        }

    def test_unify_bundles_combines_observations(self):
        unified = unify_patient_bundles(
            [self.bundle1, self.bundle2],
            archive_filename="patient_john_doe.zip",
            source_filenames=["CBC/cbc.pdf", "Lipid/lipid.png"],
        )

        self.assertEqual(unified["resourceType"], "Bundle")
        self.assertEqual(unified["type"], "document")

        # Check entries
        entries = unified["entry"]
        resources = [e["resource"] for e in entries]

        # First entry must be Composition
        self.assertEqual(resources[0]["resourceType"], "Composition")

        # Must have exactly 1 Patient resource
        patients = [r for r in resources if r["resourceType"] == "Patient"]
        self.assertEqual(len(patients), 1)
        self.assertEqual(patients[0]["name"][0]["text"], "John Doe")

        # Must have both Observations
        observations = [r for r in resources if r["resourceType"] == "Observation"]
        self.assertEqual(len(observations), 2)
        obs_codes = [o["code"]["coding"][0]["code"] for o in observations]
        self.assertIn("718-7", obs_codes)
        self.assertIn("2093-3", obs_codes)

        # Subject of all observations must reference the canonical patient
        patient_ref = f"urn:uuid:{patients[0]['id']}"
        for obs in observations:
            self.assertEqual(obs["subject"]["reference"], patient_ref)

        # Validate with ABDM validator
        val_result = validate_bundle(unified)
        self.assertGreaterEqual(val_result["compliance_score"], 80)

    def test_unify_multi_practitioners_and_resolve_performer_references(self):
        """Verify that multiple practitioners and organizations across documents are included
        in the bundle entries and all observation performers resolve without errors."""
        bundle_a = {
            "resourceType": "Bundle",
            "type": "document",
            "entry": [
                {
                    "resource": {
                        "resourceType": "Patient",
                        "id": "pt-alice",
                        "name": [{"text": "Alice Smith"}],
                        "gender": "female",
                        "birthDate": "1990-01-01",
                    }
                },
                {
                    "resource": {
                        "resourceType": "Practitioner",
                        "id": "doc-dr-patel",
                        "name": [{"text": "Dr. Patel"}],
                    }
                },
                {
                    "resource": {
                        "resourceType": "Organization",
                        "id": "org-city-lab",
                        "name": "City Laboratory",
                    }
                },
                {
                    "resource": {
                        "resourceType": "Observation",
                        "id": "obs-glucose",
                        "status": "final",
                        "code": {
                            "coding": [{"system": "http://loinc.org", "code": "1558-6", "display": "Fasting Glucose"}],
                            "text": "Fasting Glucose",
                        },
                        "valueQuantity": {"value": 90, "unit": "mg/dL"},
                        "performer": [{"reference": "urn:uuid:doc-dr-patel"}],
                    }
                },
            ],
        }

        bundle_b = {
            "resourceType": "Bundle",
            "type": "document",
            "entry": [
                {
                    "resource": {
                        "resourceType": "Patient",
                        "id": "pt-alice",
                        "name": [{"text": "Alice Smith"}],
                        "gender": "female",
                        "birthDate": "1990-01-01",
                    }
                },
                {
                    "resource": {
                        "resourceType": "Practitioner",
                        "id": "doc-dr-sharma",
                        "name": [{"text": "Dr. Sharma"}],
                    }
                },
                {
                    "resource": {
                        "resourceType": "Organization",
                        "id": "org-metro-hospital",
                        "name": "Metro Hospital",
                    }
                },
                {
                    "resource": {
                        "resourceType": "Observation",
                        "id": "obs-hba1c",
                        "status": "final",
                        "code": {
                            "coding": [{"system": "http://loinc.org", "code": "4548-4", "display": "HbA1c"}],
                            "text": "HbA1c",
                        },
                        "valueQuantity": {"value": 5.4, "unit": "%"},
                        "performer": [{"reference": "urn:uuid:org-metro-hospital"}],
                    },
                },
                {
                    "resource": {
                        "resourceType": "Observation",
                        "id": "obs-dangling-test",
                        "status": "final",
                        "code": {
                            "coding": [{"system": "http://loinc.org", "code": "2160-0", "display": "Creatinine"}],
                            "text": "Creatinine",
                        },
                        "valueQuantity": {"value": 0.9, "unit": "mg/dL"},
                        # Dangling reference that wasn't included in bundle
                        "performer": [{"reference": "urn:uuid:non-existent-performer-uuid"}],
                    },
                },
            ],
        }

        unified = unify_patient_bundles(
            [bundle_a, bundle_b],
            archive_filename="alice_health_records.zip",
            source_filenames=["Tests/glucose.pdf", "Tests/hba1c.jpg"],
        )

        entries = unified["entry"]
        resources = [e["resource"] for e in entries]
        full_urls = {e["fullUrl"] for e in entries}

        practitioners = [r for r in resources if r["resourceType"] == "Practitioner"]
        organizations = [r for r in resources if r["resourceType"] == "Organization"]
        observations = [r for r in resources if r["resourceType"] == "Observation"]

        # Both practitioners and both organizations must be present in bundle entries
        self.assertEqual(len(practitioners), 2)
        self.assertEqual(len(organizations), 2)
        self.assertEqual(len(observations), 3)

        # Every practitioner and organization must have a fullUrl
        for p in practitioners:
            self.assertIn(f"urn:uuid:{p['id']}", full_urls)
        for o in organizations:
            self.assertIn(f"urn:uuid:{o['id']}", full_urls)

        # Observation 1 performer resolves to Dr. Patel
        self.assertEqual(observations[0]["performer"][0]["reference"], "urn:uuid:doc-dr-patel")
        # Observation 2 performer resolves to Metro Hospital
        self.assertEqual(observations[1]["performer"][0]["reference"], "urn:uuid:org-metro-hospital")
        # Observation 3 with dangling performer was re-pointed to a valid organization
        dangling_perf_ref = observations[2]["performer"][0]["reference"]
        self.assertIn(dangling_perf_ref, full_urls)

        # Validate with ABDM validator - must have 0 errors and 100% compliance
        val_result = validate_bundle(unified)
        self.assertEqual(val_result["error_count"], 0)
        self.assertEqual(val_result["compliance_score"], 100)


if __name__ == "__main__":
    unittest.main()
