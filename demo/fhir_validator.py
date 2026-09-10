"""ABDM FHIR R4 structural validation for Document Bundles.

Validates against the NRCeS Implementation Guide requirements:
- Bundle structure (type, first entry is Composition)
- Required ABDM profile meta tags
- Internal reference resolution
- Terminology bindings (SNOMED CT, LOINC)
- FHIR Maturity Model (FMM) level reporting
"""

from __future__ import annotations

from typing import Any


# ABDM required profiles per resource type
ABDM_PROFILES = {
    "Bundle": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/DocumentBundle",
    "Composition": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/DiagnosticReportRecord",
    "Patient": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Patient",
    "Practitioner": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Practitioner",
    "Organization": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Organization",
    "Encounter": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Encounter",
    "Observation": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Observation",
    "DiagnosticReport": "https://nrces.in/ndhm/fhir/r4/StructureDefinition/DiagnosticReportLab",
}

# FMM levels for resource types used in this pipeline (FHIR R4)
FMM_LEVELS = {
    "Bundle": {"level": "N", "label": "Normative"},
    "Patient": {"level": "N", "label": "Normative"},
    "Observation": {"level": "N", "label": "Normative"},
    "Organization": {"level": "N", "label": "Normative"},
    "Practitioner": {"level": 3, "label": "Trial Use"},
    "DiagnosticReport": {"level": 3, "label": "Trial Use"},
    "Composition": {"level": 2, "label": "Trial Use"},
    "Encounter": {"level": 2, "label": "Trial Use"},
}

# Valid terminology system URIs
VALID_SYSTEMS = {
    "http://snomed.info/sct",
    "http://loinc.org",
    "http://unitsofmeasure.org",
    "http://terminology.hl7.org/CodeSystem/v2-0203",
    "http://terminology.hl7.org/CodeSystem/v3-ActCode",
    "http://terminology.hl7.org/CodeSystem/v3-ParticipationType",
    "http://terminology.hl7.org/CodeSystem/observation-category",
    "http://hospital.smarthealthit.org",
    "https://healthid.ndhm.gov.in",
    "https://doctor.ndhm.gov.in",
    "https://facility.ndhm.gov.in",
    "http://hip.in",
}


class ValidationIssue:
    """A single validation finding."""

    def __init__(self, severity: str, message: str, path: str = ""):
        self.severity = severity  # error, warning, info
        self.message = message
        self.path = path

    def to_dict(self) -> dict[str, str]:
        return {"severity": self.severity, "message": self.message, "path": self.path}


def validate_bundle(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a FHIR Bundle against ABDM R4 requirements.

    Returns a dict with:
      - issues: list of ValidationIssue dicts
      - compliance_score: 0-100 percentage
      - fmm_report: FMM levels for each resource type used
      - summary: human readable summary string
    """
    issues: list[ValidationIssue] = []

    # Find the bundle
    bundle = None
    if payload.get("resourceType") == "Bundle":
        bundle = payload
    else:
        docs = payload.get("standardized_medical_documents", []) or []
        if docs and isinstance(docs[0], dict):
            bundle = docs[0].get("fhir_bundle")

    if not bundle:
        issues.append(ValidationIssue("error", "No FHIR Bundle found in payload"))
        res = _build_result(issues, {})
        res["compliance_score"] = 0
        return res

    entries = bundle.get("entry", []) or []
    resources = [e.get("resource", {}) for e in entries if e.get("resource")]
    full_urls = {e.get("fullUrl") for e in entries if e.get("fullUrl")}

    # 1. Bundle structure
    if bundle.get("type") != "document":
        issues.append(ValidationIssue("error", f"Bundle.type must be 'document', got '{bundle.get('type')}'", "Bundle.type"))

    if not bundle.get("id"):
        issues.append(ValidationIssue("warning", "Bundle.id is missing", "Bundle.id"))

    if not bundle.get("timestamp"):
        issues.append(ValidationIssue("warning", "Bundle.timestamp is missing", "Bundle.timestamp"))

    # 2. First entry must be Composition
    if resources:
        if resources[0].get("resourceType") != "Composition":
            issues.append(ValidationIssue("error", "First entry must be a Composition resource", "Bundle.entry[0]"))
    else:
        issues.append(ValidationIssue("error", "Bundle has no entries", "Bundle.entry"))

    # 3. Profile checks
    _check_bundle_profile(bundle, issues)
    resource_types_used = set()
    for i, resource in enumerate(resources):
        rt = resource.get("resourceType", "Unknown")
        resource_types_used.add(rt)
        _check_resource_profile(resource, rt, i, issues)

    # 4. Required resource types for a lab report bundle
    required_types = {"Composition", "Patient", "DiagnosticReport", "Observation"}
    missing_types = required_types - resource_types_used
    for mt in missing_types:
        issues.append(ValidationIssue("error", f"Required resource type '{mt}' is missing from the bundle"))

    # 5. Internal reference resolution
    _check_references(resources, full_urls, issues)

    # 6. Terminology checks
    _check_terminology(resources, issues)

    # 7. Patient checks
    for r in resources:
        if r.get("resourceType") == "Patient":
            _check_patient(r, issues)

    # 8. Observation checks
    for i, r in enumerate(resources):
        if r.get("resourceType") == "Observation":
            _check_observation(r, i, issues)

    # Build FMM report
    fmm_report = {"Bundle": FMM_LEVELS.get("Bundle", {"level": "N", "label": "Normative"})}
    for rt in sorted(resource_types_used):
        fmm = FMM_LEVELS.get(rt, {"level": "?", "label": "Unknown"})
        fmm_report[rt] = fmm

    return _build_result(issues, fmm_report)


def _check_bundle_profile(bundle: dict, issues: list[ValidationIssue]) -> None:
    profiles = bundle.get("meta", {}).get("profile", []) or []
    expected = ABDM_PROFILES.get("Bundle", "")
    if expected and expected not in profiles:
        issues.append(ValidationIssue("warning", f"Bundle missing ABDM profile: {expected}", "Bundle.meta.profile"))


def _check_resource_profile(resource: dict, rt: str, index: int, issues: list[ValidationIssue]) -> None:
    profiles = resource.get("meta", {}).get("profile", []) or []
    expected = ABDM_PROFILES.get(rt)
    if expected:
        has_abdm = any(expected in p for p in profiles)
        if not has_abdm:
            issues.append(ValidationIssue(
                "warning",
                f"{rt} resource missing ABDM profile: {expected}",
                f"Bundle.entry[{index}].resource.meta.profile",
            ))


def _check_references(resources: list[dict], full_urls: set[str], issues: list[ValidationIssue]) -> None:
    """Check that all internal urn:uuid: references resolve."""
    for i, resource in enumerate(resources):
        refs = _collect_references(resource)
        for ref_path, ref_value in refs:
            if ref_value.startswith("urn:uuid:") and ref_value not in full_urls:
                issues.append(ValidationIssue(
                    "error",
                    f"Unresolved reference: {ref_value}",
                    f"Bundle.entry[{i}].resource.{ref_path}",
                ))


def _collect_references(obj: Any, path: str = "") -> list[tuple[str, str]]:
    """Recursively collect all {reference: ...} values."""
    refs = []
    if isinstance(obj, dict):
        if "reference" in obj and isinstance(obj["reference"], str):
            refs.append((path + ".reference" if path else "reference", obj["reference"]))
        for key, value in obj.items():
            child_path = f"{path}.{key}" if path else key
            refs.extend(_collect_references(value, child_path))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            refs.extend(_collect_references(item, f"{path}[{idx}]"))
    return refs


def _check_terminology(resources: list[dict], issues: list[ValidationIssue]) -> None:
    """Check that coding systems use valid URIs."""
    for i, resource in enumerate(resources):
        codings = _collect_codings(resource)
        for coding_path, coding in codings:
            system = coding.get("system", "")
            if system and system not in VALID_SYSTEMS:
                issues.append(ValidationIssue(
                    "info",
                    f"Non-standard terminology system: {system}",
                    f"Bundle.entry[{i}].resource.{coding_path}",
                ))


def _collect_codings(obj: Any, path: str = "") -> list[tuple[str, dict]]:
    """Recursively collect all coding objects."""
    codings = []
    if isinstance(obj, dict):
        if "system" in obj and "code" in obj:
            codings.append((path, obj))
        for key, value in obj.items():
            child_path = f"{path}.{key}" if path else key
            codings.extend(_collect_codings(value, child_path))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            codings.extend(_collect_codings(item, f"{path}[{idx}]"))
    return codings


def _check_patient(patient: dict, issues: list[ValidationIssue]) -> None:
    if not patient.get("name"):
        issues.append(ValidationIssue("warning", "Patient.name is missing", "Patient.name"))
    if not patient.get("gender"):
        issues.append(ValidationIssue("warning", "Patient.gender is missing", "Patient.gender"))
    # Check for ABHA identifier
    identifiers = patient.get("identifier", []) or []
    has_abha = any(
        i.get("system") == "https://healthid.ndhm.gov.in" for i in identifiers
    )
    if not has_abha:
        issues.append(ValidationIssue("info", "Patient has no ABHA ID identifier (required for ABDM production)", "Patient.identifier"))


def _check_observation(obs: dict, index: int, issues: list[ValidationIssue]) -> None:
    if not obs.get("status"):
        issues.append(ValidationIssue("error", "Observation.status is required", f"Observation[{index}].status"))
    code = obs.get("code", {})
    if not code:
        issues.append(ValidationIssue("error", "Observation.code is required", f"Observation[{index}].code"))
    else:
        codings = code.get("coding", []) or []
        has_loinc = any(c.get("system") == "http://loinc.org" for c in codings)
        if not has_loinc and not code.get("text"):
            issues.append(ValidationIssue("warning", "Observation has neither LOINC coding nor text", f"Observation[{index}].code"))


def _build_result(issues: list[ValidationIssue], fmm_report: dict) -> dict[str, Any]:
    error_count = sum(1 for i in issues if i.severity == "error")
    warning_count = sum(1 for i in issues if i.severity == "warning")
    info_count = sum(1 for i in issues if i.severity == "info")
    total_checks = max(len(issues), 1)
    # Score: start at 100, subtract 10 per error, 3 per warning
    score = max(0, 100 - (error_count * 10) - (warning_count * 3))

    if error_count == 0 and warning_count == 0:
        summary = "FHIR Bundle is fully ABDM-compliant."
    elif error_count == 0:
        summary = f"FHIR Bundle is structurally valid with {warning_count} warning(s)."
    else:
        summary = f"FHIR Bundle has {error_count} error(s) and {warning_count} warning(s)."

    return {
        "valid": error_count == 0,
        "compliance_score": score,
        "summary": summary,
        "error_count": error_count,
        "warning_count": warning_count,
        "info_count": info_count,
        "issues": [i.to_dict() for i in issues],
        "fmm_report": fmm_report,
    }
