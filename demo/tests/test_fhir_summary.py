import unittest

from demo.fhir_summary import NOT_AVAILABLE
from demo.fhir_summary import summarize_payload


class FhirSummaryTest(unittest.TestCase):

  def test_empty_bundle(self):
    summary = summarize_payload({"resourceType": "Bundle", "entry": []})

    self.assertTrue(summary["bundle"]["found"])
    self.assertEqual(summary["bundle"]["resource_count"], 0)
    self.assertEqual(summary["patient"]["name"], NOT_AVAILABLE)

  def test_bundle_containing_patient(self):
    summary = summarize_payload({
        "resourceType": "Bundle",
        "entry": [{
            "resource": {
                "resourceType": "Patient",
                "name": [{"text": "Demo Patient"}],
                "gender": "female",
                "birthDate": "1990-01-01",
            }
        }],
    })

    self.assertEqual(summary["patient"]["name"], "Demo Patient")
    self.assertEqual(summary["patient"]["gender"], "female")
    self.assertEqual(summary["patient"]["birthDate"], "1990-01-01")

  def test_bundle_containing_observation(self):
    summary = summarize_payload({
        "resourceType": "Bundle",
        "entry": [{
            "resource": {
                "resourceType": "Observation",
                "code": {"text": "Hemoglobin"},
            }
        }],
    })

    self.assertEqual(summary["observations"][0]["test"], "Hemoglobin")

  def test_observation_with_loinc_code(self):
    summary = summarize_payload({
        "resourceType": "Bundle",
        "entry": [{
            "resource": {
                "resourceType": "Observation",
                "code": {
                    "coding": [{
                        "system": "http://loinc.org",
                        "code": "718-7",
                        "display": "Hemoglobin",
                    }]
                },
            }
        }],
    })

    self.assertEqual(summary["observations"][0]["loinc"], "718-7")

  def test_observation_without_loinc_code(self):
    summary = summarize_payload({
        "resourceType": "Bundle",
        "entry": [{
            "resource": {
                "resourceType": "Observation",
                "code": {"text": "Local Test"},
            }
        }],
    })

    observation = summary["observations"][0]
    self.assertEqual(observation["loinc"], NOT_AVAILABLE)
    self.assertIn("LOINC mapping not available", observation["loincMessage"])

  def test_observation_with_value_quantity(self):
    summary = summarize_payload({
        "resourceType": "Bundle",
        "entry": [{
            "resource": {
                "resourceType": "Observation",
                "code": {"text": "Glucose"},
                "valueQuantity": {"value": 92, "unit": "mg/dL"},
            }
        }],
    })

    observation = summary["observations"][0]
    self.assertEqual(observation["result"], "92")
    self.assertEqual(observation["unit"], "mg/dL")

  def test_missing_optional_fields(self):
    summary = summarize_payload({
        "standardized_medical_documents": [{
            "fhir_bundle": {
                "resourceType": "Bundle",
                "entry": [{"resource": {"resourceType": "Observation"}}],
            }
        }]
    })

    observation = summary["observations"][0]
    self.assertEqual(observation["test"], NOT_AVAILABLE)
    self.assertEqual(observation["result"], NOT_AVAILABLE)
    self.assertEqual(observation["referenceRange"], NOT_AVAILABLE)


  def test_panel_observation_excluded_from_observations_list(self):
    summary = summarize_payload({
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Observation",
                    "code": {"text": "Complete Blood Count"},
                    "hasMember": [{"reference": "urn:uuid:test-1"}],
                }
            },
            {
                "resource": {
                    "resourceType": "Observation",
                    "code": {"text": "Hemoglobin"},
                    "valueQuantity": {"value": 14.5, "unit": "g/dL"},
                }
            },
        ],
    })

    # Only the individual test observation should be listed, not the container panel
    self.assertEqual(len(summary["observations"]), 1)
    self.assertEqual(summary["observations"][0]["test"], "Hemoglobin")
    self.assertEqual(summary["observations"][0]["result"], "14.5")


if __name__ == "__main__":
  unittest.main()
