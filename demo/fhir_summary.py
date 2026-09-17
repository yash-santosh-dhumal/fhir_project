"""Utilities for summarizing FHIR JSON returned by Medical Data Toolkit."""

from __future__ import annotations

from collections import Counter
from typing import Any


NOT_AVAILABLE = "Not available"
LOINC_SYSTEM = "http://loinc.org"


def _display(value: Any) -> str:
  if value is None or value == "":
    return NOT_AVAILABLE
  return str(value)


def find_bundles(payload: dict[str, Any]) -> list[dict[str, Any]]:
  """Finds FHIR Bundle dictionaries in a direct or toolkit-wrapped response."""
  bundles: list[dict[str, Any]] = []
  if payload.get("resourceType") == "Bundle":
    bundles.append(payload)

  for doc in payload.get("standardized_medical_documents", []) or []:
    bundle = doc.get("fhir_bundle") if isinstance(doc, dict) else None
    if isinstance(bundle, dict) and bundle.get("resourceType") == "Bundle":
      bundles.append(bundle)
  return bundles


def get_resources(bundle: dict[str, Any]) -> list[dict[str, Any]]:
  resources: list[dict[str, Any]] = []
  for entry in bundle.get("entry", []) or []:
    resource = entry.get("resource") if isinstance(entry, dict) else None
    if isinstance(resource, dict):
      resources.append(resource)
  return resources


def summarize_payload(payload: dict[str, Any]) -> dict[str, Any]:
  """Builds a demo-friendly clinical and resource summary from FHIR JSON."""
  bundles = find_bundles(payload)
  bundle = bundles[0] if bundles else {}
  resources = get_resources(bundle) if bundle else []
  resource_counts = Counter(
      resource.get("resourceType", "Unknown") for resource in resources
  )

  patient = next(
      (r for r in resources if r.get("resourceType") == "Patient"), {}
  )
  observations = [
      r
      for r in resources
      if r.get("resourceType") == "Observation"
      and not (
          r.get("hasMember")
          and not r.get("valueQuantity")
          and not r.get("valueString")
          and not r.get("valueCodeableConcept")
      )
  ]

  profiles = []
  for resource in resources:
    for profile in resource.get("meta", {}).get("profile", []) or []:
      if profile not in profiles:
        profiles.append(profile)
  for profile in bundle.get("meta", {}).get("profile", []) or []:
    if profile not in profiles:
      profiles.append(profile)

  return {
      "bundle": {
          "found": bool(bundle),
          "type": _display(bundle.get("type")),
          "resource_count": len(resources),
          "resource_counts": dict(sorted(resource_counts.items())),
          "profiles": profiles,
      },
      "patient": _summarize_patient(patient),
      "observations": [_summarize_observation(obs) for obs in observations],
  }


def _summarize_patient(patient: dict[str, Any]) -> dict[str, str]:
  names = patient.get("name", []) or []
  first_name = names[0] if names else {}
  text_name = first_name.get("text")
  if not text_name:
    given = " ".join(first_name.get("given", []) or [])
    family = first_name.get("family", "")
    text_name = " ".join(part for part in [given, family] if part)

  addresses = patient.get("address", []) or []
  addr_text = addresses[0].get("text") if addresses else ""

  contacts = patient.get("contact", []) or []
  guardian_val = contacts[0].get("name", {}).get("text", "") if contacts else ""

  telecoms = patient.get("telecom", []) or []
  phone_val = next((t.get("value") for t in telecoms if t.get("system") == "phone"), "")

  identifiers = patient.get("identifier", []) or []
  aadhaar_val = next((i.get("value") for i in identifiers if "aadhaar" in str(i.get("system", "")).lower()), "")
  abha_val = next((i.get("value") for i in identifiers if "abha" in str(i.get("system", "")).lower()), "")
  hosp_id = next((i.get("value") for i in identifiers if "hospital.org/patient-id" in str(i.get("system", "")).lower() or (i.get("type", {}).get("coding", [{}])[0].get("code") == "PI")), "")
  mrn_val = next((i.get("value") for i in identifiers if i.get("type", {}).get("coding", [{}])[0].get("code") == "MR"), "")

  return {
      "name": _display(text_name),
      "gender": _display(patient.get("gender")),
      "birthDate": _display(patient.get("birthDate")),
      "address": _display(addr_text),
      "aadhaar": _display(aadhaar_val),
      "abha": _display(abha_val),
      "phone": _display(phone_val),
      "guardian": _display(guardian_val),
      "hospital_id": _display(hosp_id),
      "mrn": _display(mrn_val),
  }


def _summarize_observation(observation: dict[str, Any]) -> dict[str, str]:
  code = observation.get("code", {}) or {}
  codings = code.get("coding", []) or []
  loinc = next(
      (coding for coding in codings if coding.get("system") == LOINC_SYSTEM),
      None,
  )
  display_coding = loinc or (codings[0] if codings else {})
  value_quantity = observation.get("valueQuantity")

  if isinstance(value_quantity, dict):
    result = _display(value_quantity.get("value"))
    unit = _display(value_quantity.get("unit") or value_quantity.get("code"))
  else:
    result = _display(
        observation.get("valueString")
        or observation.get("valueCodeableConcept", {}).get("text")
    )
    unit = NOT_AVAILABLE

  return {
      "test": _display(
          code.get("text")
          or display_coding.get("display")
          or display_coding.get("code")
      ),
      "loinc": _display(loinc.get("code") if loinc else None),
      "loincMessage": ""
      if loinc
      else "LOINC mapping not available for this observation.",
      "result": result,
      "unit": unit,
      "referenceRange": _format_reference_ranges(
          observation.get("referenceRange", []) or []
      ),
  }


def _format_reference_ranges(ranges: list[dict[str, Any]]) -> str:
  if not ranges:
    return NOT_AVAILABLE
  values = []
  for ref_range in ranges:
    text = ref_range.get("text")
    low = ref_range.get("low", {}).get("value")
    high = ref_range.get("high", {}).get("value")
    unit = (
        ref_range.get("low", {}).get("unit")
        or ref_range.get("high", {}).get("unit")
        or ""
    )
    if text:
      values.append(str(text))
    elif low is not None or high is not None:
      values.append(f"{_display(low)} - {_display(high)} {unit}".strip())
  return "; ".join(values) if values else NOT_AVAILABLE
