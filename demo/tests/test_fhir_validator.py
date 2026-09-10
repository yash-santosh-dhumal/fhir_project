"""Unit tests for ABDM FHIR R4 validator."""

import unittest

from demo.fhir_validator import validate_bundle


class TestFhirValidator(unittest.TestCase):

    def test_empty_payload(self):
        result = validate_bundle({})
        self.assertEqual(result["compliance_score"], 0)
        self.assertTrue(any("No FHIR Bundle" in issue["message"] for issue in result["issues"]))

    def test_bundle_not_document_type(self):
        payload = {
            "resourceType": "Bundle",
            "type": "collection",
            "entry": [],
        }
        result = validate_bundle(payload)
        self.assertTrue(any("Bundle.type must be 'document'" in issue["message"] for issue in result["issues"]))

    def test_first_entry_not_composition(self):
        payload = {
            "resourceType": "Bundle",
            "type": "document",
            "entry": [
                {
                    "fullUrl": "urn:uuid:pat-1",
                    "resource": {
                        "resourceType": "Patient",
                        "id": "pat-1",
                    },
                }
            ],
        }
        result = validate_bundle(payload)
        self.assertTrue(any("First entry must be a Composition" in issue["message"] for issue in result["issues"]))

    def test_abdm_compliant_bundle(self):
        payload = {
            "resourceType": "Bundle",
            "id": "bundle-abdm-1",
            "type": "document",
            "meta": {
                "profile": ["https://nrces.in/ndhm/fhir/r4/StructureDefinition/DocumentBundle"]
            },
            "entry": [
                {
                    "fullUrl": "urn:uuid:comp-1",
                    "resource": {
                        "resourceType": "Composition",
                        "id": "comp-1",
                        "status": "final",
                        "meta": {
                            "profile": ["https://nrces.in/ndhm/fhir/r4/StructureDefinition/DiagnosticReportRecord"]
                        },
                        "subject": {"reference": "urn:uuid:pat-1"},
                        "author": [{"reference": "urn:uuid:pract-1"}],
                        "section": [
                            {
                                "entry": [{"reference": "urn:uuid:diag-1"}]
                            }
                        ],
                    },
                },
                {
                    "fullUrl": "urn:uuid:pat-1",
                    "resource": {
                        "resourceType": "Patient",
                        "id": "pat-1",
                        "meta": {
                            "profile": ["https://nrces.in/ndhm/fhir/r4/StructureDefinition/Patient"]
                        },
                        "name": [{"text": "Meera Patel"}],
                        "gender": "female",
                        "birthDate": "1988-03-24",
                    },
                },
                {
                    "fullUrl": "urn:uuid:pract-1",
                    "resource": {
                        "resourceType": "Practitioner",
                        "id": "pract-1",
                        "meta": {
                            "profile": ["https://nrces.in/ndhm/fhir/r4/StructureDefinition/Practitioner"]
                        },
                        "name": [{"text": "Dr. Ramesh Sharma"}],
                    },
                },
                {
                    "fullUrl": "urn:uuid:diag-1",
                    "resource": {
                        "resourceType": "DiagnosticReport",
                        "id": "diag-1",
                        "status": "final",
                        "meta": {
                            "profile": ["https://nrces.in/ndhm/fhir/r4/StructureDefinition/DiagnosticReportLab"]
                        },
                        "subject": {"reference": "urn:uuid:pat-1"},
                        "result": [{"reference": "urn:uuid:obs-1"}],
                    },
                },
                {
                    "fullUrl": "urn:uuid:obs-1",
                    "resource": {
                        "resourceType": "Observation",
                        "id": "obs-1",
                        "status": "final",
                        "meta": {
                            "profile": ["https://nrces.in/ndhm/fhir/r4/StructureDefinition/Observation"]
                        },
                        "subject": {"reference": "urn:uuid:pat-1"},
                        "code": {
                            "coding": [
                                {
                                    "system": "http://loinc.org",
                                    "code": "718-7",
                                    "display": "Hemoglobin",
                                }
                            ],
                            "text": "Hemoglobin",
                        },
                        "valueQuantity": {
                            "value": 13.8,
                            "unit": "g/dL",
                            "system": "http://unitsofmeasure.org",
                        },
                    },
                },
            ],
        }

        result = validate_bundle(payload)
        self.assertGreaterEqual(result["compliance_score"], 80)
        # Check FMM reporting
        fmm = result.get("fmm_report", {})
        self.assertIn("Bundle", fmm)
        self.assertIn("Composition", fmm)
        self.assertIn("Patient", fmm)
        self.assertIn("Observation", fmm)
        self.assertIn("DiagnosticReport", fmm)
        self.assertEqual(fmm["Bundle"]["level"], "N")
        self.assertEqual(fmm["Observation"]["level"], "N")


if __name__ == "__main__":
    unittest.main()
