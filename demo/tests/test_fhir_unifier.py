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

    def test_adjudication_demo_bill_cashless_approval(self):
        from demo.gemini_ocr import (
            ArchiveExtractionResult,
            ExtractedBillingData,
            ExtractedBillingItem,
            ExtractedInsurancePolicy,
        )
        from demo.fhir_unifier import _adjudicate_bill_against_policy

        policy = ExtractedInsurancePolicy(
            policy_number="WAP138200500121/04",
            scheme_or_insurer="Dr. YSR Aarogyasri / AB-PMJAY",
            claim_or_preauth_number="APTRUST/KNL/2025/1/13510681/07",
            annual_sum_insured=2500000.0,
            pre_auth_approved_amount=42962.0,
            patient_out_of_pocket=0.0,
            coverage_type="Cashless Government Health Assurance Floater",
        )
        bill = ExtractedBillingData(
            bill_number="BILL-2025-AP-01",
            total_amount=42962.0,
            source_document="AP12363098/demo bill/Hospital_Bill_AP12363098.pdf",
            items=[ExtractedBillingItem(description="Surgical Procedure", amount=42962.0)],
        )

        adj = _adjudicate_bill_against_policy([bill], policy)
        self.assertEqual(adj["total_billed"], 42962.0)
        self.assertEqual(adj["insured_amount"], 42962.0)
        self.assertEqual(adj["patient_payable"], 0.0)
        self.assertEqual(adj["coverage_percentage"], 100.0)
        self.assertEqual(adj["status"], "100% Cashless Approved")

    def test_adjudication_80_20_copay_policy(self):
        from demo.gemini_ocr import ExtractedBillingData, ExtractedBillingItem, ExtractedInsurancePolicy
        from demo.fhir_unifier import _adjudicate_bill_against_policy

        policy = ExtractedInsurancePolicy(
            policy_number="WAP138200500121/04",
            scheme_or_insurer="Dr. YSR Aarogyasri / AB-PMJAY",
            claim_or_preauth_number="APTRUST/KNL/2025/1/13510681/07",
            annual_sum_insured=2500000.0,
            pre_auth_approved_amount=34370.0,
            patient_out_of_pocket=8592.0,
            copayment_percentage=20.0,
            copayment_liability=8592.0,
            coverage_type="Co-Pay Health Assurance Scheme (80:20 Risk-Sharing)",
            terms_and_rules="Adjudicated under 80:20 risk-sharing clause: 80% sanctioned by Trust, 20% co-payment out-of-pocket.",
        )
        bill = ExtractedBillingData(
            bill_number="BILL/ONC/129368/2026",
            total_amount=42962.0,
            source_document="AP12363098/demo bill/Hospital_Bill_AP12363098.pdf",
            items=[ExtractedBillingItem(description="Targeted Chemotherapy & Bed Charges", amount=42962.0)],
        )

        adj = _adjudicate_bill_against_policy([bill], policy)
        self.assertEqual(adj["total_billed"], 42962.0)
        self.assertEqual(adj["insured_amount"], 34370.0)
        self.assertEqual(adj["patient_payable"], 8592.0)
        self.assertEqual(adj["coverage_percentage"], 80.0)
        self.assertEqual(adj["status"], "Partially Covered (80.0%)")

    def test_unify_patient_bundles_with_coverage_and_claim_response(self):
        from demo.gemini_ocr import (
            ArchiveExtractionResult,
            ExtractedBillingData,
            ExtractedBillingItem,
            ExtractedInsurancePolicy,
        )

        extraction = ArchiveExtractionResult(
            billing_data=[
                ExtractedBillingData(
                    bill_number="BILL-42962",
                    total_amount=42962.0,
                    source_document="demo bill/Hospital_Bill_AP12363098.pdf",
                    items=[ExtractedBillingItem(description="In-Patient Charges", amount=42962.0)],
                )
            ],
            insurance_policy=ExtractedInsurancePolicy(
                policy_number="WAP138200500121/04",
                scheme_or_insurer="Dr. YSR Aarogyasri / AB-PMJAY",
                claim_or_preauth_number="APTRUST/KNL/2025/1/13510681/07",
                annual_sum_insured=2500000.0,
                pre_auth_approved_amount=42962.0,
                patient_out_of_pocket=0.0,
                coverage_type="Cashless Government Health Assurance Floater",
            ),
        )

        unified = unify_patient_bundles(
            [self.bundle1],
            archive_filename="AP12363098.zip",
            gemini_extraction=extraction,
        )

        entries = unified.get("entry", [])
        resources = [e["resource"] for e in entries]

        # Verify Coverage and ClaimResponse resources exist
        coverages = [r for r in resources if r["resourceType"] == "Coverage"]
        claims = [r for r in resources if r["resourceType"] == "Claim"]
        claim_responses = [r for r in resources if r["resourceType"] == "ClaimResponse"]

        self.assertEqual(len(coverages), 1)
        self.assertEqual(len(claims), 1)
        self.assertEqual(len(claim_responses), 1)

        cr = claim_responses[0]
        self.assertEqual(cr["outcome"], "complete")
        self.assertEqual(cr["preAuthRef"], "APTRUST/KNL/2025/1/13510681/07")
        totals = {t["category"]["coding"][0]["code"]: t["amount"]["value"] for t in cr["total"]}
        self.assertEqual(totals.get("submitted"), 42962.0)
        self.assertEqual(totals.get("benefit"), 42962.0)
        self.assertEqual(totals.get("patientoutoppocket"), 0.0)

        # Ensure composition has the Insurance section
        composition = [r for r in resources if r["resourceType"] == "Composition"][0]
        sec_titles = [s.get("title") for s in composition.get("section", [])]
        self.assertIn("Insurance Coverage & Claim Adjudication", sec_titles)


if __name__ == "__main__":
    unittest.main()
