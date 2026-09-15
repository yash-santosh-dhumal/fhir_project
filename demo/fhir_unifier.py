"""Unifies multiple FHIR Bundles and Gemini-extracted data into a single FHIR Document Bundle.

When a ZIP archive containing multiple documents/images of the same patient is uploaded:
- Integrates high-level OCR and structured patient demographics from ALL documents
- Merges patient demographics into a single canonical Patient resource (Aadhaar, address, DOB)
- Consolidates all Observation resources across all documents/folders
- Updates subject references to point to the canonical Patient
- Generates a consolidated ABDM-compliant Composition and DiagnosticReport
- Returns a normative FHIR R4 Document Bundle
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path
import re
from typing import Any
import uuid

log = logging.getLogger(__name__)

ABDM_COMPOSITION_PROFILE = (
    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/DiagnosticReportRecord"
)
ABDM_DIAGNOSTIC_REPORT_PROFILE = (
    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/DiagnosticReportLab"
)
ABDM_PATIENT_PROFILE = "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Patient"
ABDM_PRACTITIONER_PROFILE = (
    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Practitioner"
)
ABDM_ORGANIZATION_PROFILE = (
    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Organization"
)
ABDM_OBSERVATION_PROFILE = (
    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Observation"
)
ABDM_DOCUMENT_BUNDLE_PROFILE = (
    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/DocumentBundle"
)

_LOINC_CACHE: dict[str, tuple[str, str]] | None = None


def _get_loinc_mapping() -> dict[str, tuple[str, str]]:
    """Loads LOINC number and display name mapping from local knowledge base."""
    global _LOINC_CACHE
    if _LOINC_CACHE is not None:
        return _LOINC_CACHE

    mapping: dict[str, tuple[str, str]] = {
        "blood group": ("883-9", "ABO and Rh group [Type] in Blood"),
        "rh factor": ("10331-7", "Rh [Type] in Blood"),
        "abo group": ("883-9", "ABO group [Type] in Blood"),
    }

    csv_path = Path(__file__).resolve().parent.parent / "data" / "analyte_records_top_2000.csv"
    if csv_path.exists():
        try:
            import ast
            import csv
            with open(csv_path, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    code = (row.get("LOINC_NUM") or "").strip()
                    core = (row.get("core_analyte") or "").strip()
                    long_name = (row.get("LONG_COMMON_NAME") or "").strip()
                    if code and core:
                        mapping[core.lower()] = (code, long_name)
                    syns_str = row.get("synonyms")
                    if syns_str:
                        try:
                            syns = ast.literal_eval(syns_str)
                            for s in syns:
                                mapping[str(s).strip().lower()] = (code, long_name)
                        except Exception:
                            pass
        except Exception as exc:
            log.warning("Could not load LOINC KB: %s", exc)

    _LOINC_CACHE = mapping
    return mapping


def _lookup_loinc(test_name: str) -> tuple[str, str] | None:
    """Finds best matching LOINC code and display name for a test analyte."""
    if not test_name:
        return None
    mapping = _get_loinc_mapping()
    t_lower = test_name.strip().lower()
    if t_lower in mapping:
        return mapping[t_lower]
    for k, v in mapping.items():
        if len(k) >= 4 and (k == t_lower or k in t_lower or t_lower in k):
            return v
    return None


def _get_resources(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    resources: list[dict[str, Any]] = []
    for entry in bundle.get("entry", []) or []:
        res = entry.get("resource") if isinstance(entry, dict) else None
        if isinstance(res, dict):
            resources.append(res)
    return resources


def _choose_best_patient(patients: list[dict[str, Any]]) -> dict[str, Any]:
    """Selects the most complete Patient resource from the list."""
    if not patients:
        return {
            "resourceType": "Patient",
            "id": str(uuid.uuid4()),
            "name": [{"text": "Patient"}],
            "gender": "unknown",
            "meta": {"profile": [ABDM_PATIENT_PROFILE]},
        }

    def score_patient(p: dict[str, Any]) -> int:
        score = 0
        names = p.get("name", []) or []
        if names and (names[0].get("text") or names[0].get("family") or names[0].get("given")):
            score += 10
        if p.get("birthDate"):
            score += 5
        if p.get("gender") and p.get("gender") != "unknown":
            score += 3
        if p.get("identifier"):
            score += 4
        return score

    best = max(patients, key=score_patient)
    res = dict(best)
    if "meta" not in res:
        res["meta"] = {}
    profiles = res["meta"].get("profile", [])
    if ABDM_PATIENT_PROFILE not in profiles:
        profiles.append(ABDM_PATIENT_PROFILE)
    res["meta"]["profile"] = profiles
    return res


def _apply_gemini_demographics(patient: dict[str, Any], demographics: Any) -> None:
    """Overlays high-confidence demographics from Gemini onto the Patient resource."""
    if not demographics:
        return
    d = demographics.to_dict() if hasattr(demographics, "to_dict") else (demographics or {})
    if not isinstance(d, dict):
        return

    # 1. Full legal name
    pname = d.get("name")
    if pname and str(pname).strip() and str(pname).strip().lower() not in ("patient", "unknown", "na", "null"):
        clean_name = str(pname).strip()
        parts = clean_name.split()
        family = parts[-1] if len(parts) > 1 else ""
        given = parts[:-1] if len(parts) > 1 else [parts[0]]
        patient["name"] = [{
            "text": clean_name,
            "family": family,
            "given": given,
        }]

    # 2. Birth Date
    pdob = d.get("birth_date")
    if pdob and str(pdob).strip() and str(pdob).strip().lower() not in ("unknown", "na", "null"):
        patient["birthDate"] = str(pdob).strip()

    # 3. Gender
    pgender = d.get("gender")
    if pgender and str(pgender).lower() in ("male", "female", "other"):
        patient["gender"] = str(pgender).lower()

    # 4. Identifiers (Aadhaar, ABHA, Hospital MRN/IP)
    identifiers = patient.get("identifier", []) or []

    aadhaar = d.get("aadhaar_number")
    if aadhaar and str(aadhaar).strip() and str(aadhaar).strip().lower() not in ("unknown", "na", "null"):
        clean_aadhaar = str(aadhaar).strip()
        if not any(i.get("system") == "https://uidai.gov.in/aadhaar" for i in identifiers):
            identifiers.append({
                "system": "https://uidai.gov.in/aadhaar",
                "value": clean_aadhaar,
                "type": {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/v2-0203",
                            "code": "MR",
                            "display": "Aadhaar Card / UIDAI",
                        }
                    ]
                },
            })

    health_card = d.get("health_card_number")
    if health_card and str(health_card).strip() and str(health_card).strip().lower() not in ("unknown", "na", "null"):
        if not any(i.get("system") == "https://abdm.gov.in/abha" for i in identifiers):
            identifiers.append({
                "system": "https://abdm.gov.in/abha",
                "value": str(health_card).strip(),
                "type": {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/v2-0203",
                            "code": "NH",
                            "display": "ABHA / Health ID",
                        }
                    ]
                },
            })

    patient_id = d.get("patient_id")
    if patient_id and str(patient_id).strip() and str(patient_id).strip().lower() not in ("unknown", "na", "null"):
        if not any(i.get("system") == "https://hospital.org/patient-id" for i in identifiers):
            identifiers.append({
                "system": "https://hospital.org/patient-id",
                "value": str(patient_id).strip(),
                "type": {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/v2-0203",
                            "code": "PI",
                            "display": "Patient Internal Identifier",
                        }
                    ]
                },
            })

    if identifiers:
        patient["identifier"] = identifiers

    # 5. Full Address
    address_str = d.get("address")
    if address_str and str(address_str).strip() and str(address_str).strip().lower() not in ("unknown", "na", "null"):
        city = d.get("city") or ""
        district = d.get("district") or ""
        state = d.get("state") or ""
        pincode = d.get("pincode") or ""
        patient["address"] = [{
            "text": str(address_str).strip(),
            "line": [city or str(address_str).strip()],
            "city": city,
            "district": district,
            "state": state,
            "postalCode": pincode,
        }]

    # 6. Telecom
    phone = d.get("phone")
    if phone and str(phone).strip() and str(phone).strip().lower() not in ("unknown", "na", "null"):
        patient["telecom"] = [{
            "system": "phone",
            "value": str(phone).strip(),
            "use": "mobile",
        }]

    # 7. Guardian / Emergency Contact
    guardian = d.get("guardian_name")
    if guardian and str(guardian).strip() and str(guardian).strip().lower() not in ("unknown", "na", "null"):
        patient["contact"] = [{
            "relationship": [
                {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/v2-0131",
                            "code": "C",
                            "display": "Emergency Contact / Guardian",
                        }
                    ]
                }
            ],
            "name": {"text": str(guardian).strip()},
        }]


def _build_observation_resource(
    obs: Any,
    patient_ref: str,
    organization_ref: str,
) -> dict[str, Any]:
    """Constructs an ABDM-compliant FHIR Observation resource from extracted observation data."""
    o = obs.to_dict() if hasattr(obs, "to_dict") else (obs or {})
    tname = str(o.get("test_name") or "Diagnostic Test").strip()
    val_str = str(o.get("value") or "").strip()
    unit = str(o.get("unit") or "").strip()
    ref_range = str(o.get("reference_range") or "").strip()
    src_doc = str(o.get("source_document") or "").strip()

    obs_id = str(uuid.uuid4())
    loinc_match = _lookup_loinc(tname)
    codings = []
    if loinc_match:
        codings.append({
            "system": "http://loinc.org",
            "code": loinc_match[0],
            "display": loinc_match[1],
        })
    else:
        codings.append({
            "system": "http://local-clinic.org/tests",
            "code": re.sub(r"[^a-zA-Z0-9]+", "_", tname).strip("_") or "test",
            "display": tname,
        })

    resource: dict[str, Any] = {
        "resourceType": "Observation",
        "id": obs_id,
        "meta": {"profile": [ABDM_OBSERVATION_PROFILE]},
        "status": "final",
        "category": [
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                        "code": "laboratory",
                        "display": "Laboratory",
                    }
                ]
            }
        ],
        "code": {
            "coding": codings,
            "text": tname,
        },
        "subject": {"reference": patient_ref},
        "performer": [{"reference": organization_ref}],
    }

    # Value: numeric vs string
    try:
        clean_num = re.sub(r"[^\d.-]", "", val_str)
        if clean_num and clean_num not in (".", "-", "--"):
            num_val = float(clean_num)
            resource["valueQuantity"] = {
                "value": num_val,
                "unit": unit or "1",
                "system": "http://unitsofmeasure.org" if unit else None,
                "code": unit or "1",
            }
        else:
            resource["valueString"] = val_str
    except Exception:
        resource["valueString"] = val_str

    if ref_range:
        resource["referenceRange"] = [{"text": ref_range}]
    if src_doc:
        resource["note"] = [{"text": f"Source document: {src_doc}"}]

    return resource


def unify_patient_bundles(
    bundles: list[dict[str, Any]],
    archive_filename: str = "patient_archive.zip",
    source_filenames: list[str] | None = None,
    gemini_extraction: Any = None,
) -> dict[str, Any]:
    """Combines multiple FHIR Bundles and Gemini extraction into one unified Document Bundle.

    Args:
        bundles: List of FHIR Bundle dictionaries extracted from the patient documents.
        archive_filename: Name of the ZIP archive.
        source_filenames: List of relative paths for each source document.
        gemini_extraction: Optional ArchiveExtractionResult from high-level Gemini OCR.

    Returns:
        A consolidated FHIR R4 Document Bundle dictionary.
    """
    if not bundles and not gemini_extraction:
        raise ValueError("No FHIR bundles or extracted data provided for unification.")

    all_patients: list[dict[str, Any]] = []
    all_practitioners: dict[str, dict[str, Any]] = {}
    all_organizations: dict[str, dict[str, Any]] = {}
    all_observations: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []

    doc_sources = source_filenames or [f"Document {i+1}" for i in range(len(bundles))]

    # 1. Gather resources from existing bundles
    for idx, bundle in enumerate(bundles):
        src_name = doc_sources[idx] if idx < len(doc_sources) else f"Document {idx+1}"
        resources = _get_resources(bundle)
        doc_obs_refs: list[dict[str, str]] = []

        for r in resources:
            rtype = r.get("resourceType")
            if rtype == "Patient":
                all_patients.append(r)
            elif rtype == "Practitioner":
                pid = r.get("id") or str(uuid.uuid4())
                r["id"] = pid
                if "meta" not in r:
                    r["meta"] = {}
                p_prof = r["meta"].get("profile", []) or []
                if ABDM_PRACTITIONER_PROFILE not in p_prof:
                    p_prof.append(ABDM_PRACTITIONER_PROFILE)
                r["meta"]["profile"] = p_prof
                all_practitioners[pid] = r
            elif rtype == "Organization":
                oid = r.get("id") or str(uuid.uuid4())
                r["id"] = oid
                if "meta" not in r:
                    r["meta"] = {}
                o_prof = r["meta"].get("profile", []) or []
                if ABDM_ORGANIZATION_PROFILE not in o_prof:
                    o_prof.append(ABDM_ORGANIZATION_PROFILE)
                r["meta"]["profile"] = o_prof
                all_organizations[oid] = r
            elif rtype == "Observation":
                obs_id = r.get("id") or str(uuid.uuid4())
                r["id"] = obs_id
                notes = r.get("note", []) or []
                tag_text = f"Source document: {src_name}"
                if not any(n.get("text") == tag_text for n in notes if isinstance(n, dict)):
                    notes.append({"text": tag_text})
                r["note"] = notes
                all_observations.append(r)
                doc_obs_refs.append({"reference": f"urn:uuid:{obs_id}"})

        if doc_obs_refs:
            sections.append({
                "title": f"Laboratory Results — {src_name}",
                "code": {
                    "coding": [
                        {
                            "system": "http://loinc.org",
                            "code": "26436-6",
                            "display": "Laboratory studies",
                        }
                    ]
                },
                "entry": doc_obs_refs,
            })

    # 2. Select / Create Canonical Patient
    canonical_patient = _choose_best_patient(all_patients)

    # 3. Apply Gemini-extracted demographics
    if gemini_extraction:
        demo = getattr(gemini_extraction, "demographics", None)
        _apply_gemini_demographics(canonical_patient, demo)

    patient_id = canonical_patient.get("id") or str(uuid.uuid4())
    canonical_patient["id"] = patient_id
    patient_ref = f"urn:uuid:{patient_id}"

    # 4. Fallback or update Practitioner & Organization
    hosp_name = (
        getattr(gemini_extraction, "hospital_name", None)
        if gemini_extraction
        else None
    ) or "Clinical Healthcare Facility"

    doc_name = (
        getattr(gemini_extraction, "treating_physician", None)
        if gemini_extraction
        else None
    ) or "Attending Physician / Medical Officer"

    if not all_practitioners:
        fallback_pid = str(uuid.uuid4())
        all_practitioners[fallback_pid] = {
            "resourceType": "Practitioner",
            "id": fallback_pid,
            "meta": {"profile": [ABDM_PRACTITIONER_PROFILE]},
            "name": [{"text": doc_name}],
        }
    elif doc_name and doc_name != "Attending Physician / Medical Officer":
        first_pid = list(all_practitioners.keys())[0]
        all_practitioners[first_pid]["name"] = [{"text": doc_name}]

    if not all_organizations:
        fallback_oid = str(uuid.uuid4())
        all_organizations[fallback_oid] = {
            "resourceType": "Organization",
            "id": fallback_oid,
            "meta": {"profile": [ABDM_ORGANIZATION_PROFILE]},
            "name": hosp_name,
        }
    elif hosp_name and hosp_name != "Clinical Healthcare Facility":
        first_oid = list(all_organizations.keys())[0]
        all_organizations[first_oid]["name"] = hosp_name

    primary_practitioner = list(all_practitioners.values())[0]
    practitioner_ref = f"urn:uuid:{primary_practitioner['id']}"

    primary_organization = list(all_organizations.values())[0]
    organization_ref = f"urn:uuid:{primary_organization['id']}"

    # 5. Add extra observations from Gemini extraction if not already present
    if gemini_extraction and getattr(gemini_extraction, "observations", None):
        existing_test_names = {
            (obs.get("code", {}).get("text") or "").strip().lower()
            for obs in all_observations
        }
        gemini_obs_refs: list[dict[str, str]] = []
        for g_obs in gemini_extraction.observations:
            tname = getattr(g_obs, "test_name", "") or ""
            if tname.strip().lower() not in existing_test_names:
                obs_res = _build_observation_resource(g_obs, patient_ref, organization_ref)
                all_observations.append(obs_res)
                gemini_obs_refs.append({"reference": f"urn:uuid:{obs_res['id']}"})
                existing_test_names.add(tname.strip().lower())

        if gemini_obs_refs and not bundles:
            sections.append({
                "title": "Clinical & Diagnostic Findings",
                "code": {
                    "coding": [
                        {
                            "system": "http://loinc.org",
                            "code": "26436-6",
                            "display": "Laboratory studies",
                        }
                    ]
                },
                "entry": gemini_obs_refs,
            })

    # 6. Add Clinical Diagnoses Section if available
    if gemini_extraction and getattr(gemini_extraction, "clinical_notes", None):
        notes_list = gemini_extraction.clinical_notes
        if notes_list:
            sections.append({
                "title": "Clinical Diagnoses & Treatment Plan",
                "code": {
                    "coding": [
                        {
                            "system": "http://snomed.info/sct",
                            "code": "424525001",
                            "display": "Antenatal care and summary notes",
                        }
                    ]
                },
                "text": {
                    "status": "additional",
                    "div": f"<div xmlns='http://www.w3.org/1999/xhtml'><ul>{''.join(f'<li>{n}</li>' for n in notes_list)}</ul></div>",
                },
            })

    # 7. Add Archive Document Manifest Section if available
    if gemini_extraction and getattr(gemini_extraction, "documents", None):
        manifest_items = []
        for d in gemini_extraction.documents:
            d_fname = getattr(d, "filename", "")
            d_type = getattr(d, "document_type", "Medical Document")
            d_sum = getattr(d, "summary", "")
            manifest_items.append(f"<li><strong>{d_fname}</strong> ({d_type}): {d_sum}</li>")

        if manifest_items:
            sections.append({
                "title": "Archive Document Inventory & Manifest",
                "code": {
                    "coding": [
                        {
                            "system": "http://loinc.org",
                            "code": "11503-0",
                            "display": "Medical records",
                        }
                    ]
                },
                "text": {
                    "status": "additional",
                    "div": f"<div xmlns='http://www.w3.org/1999/xhtml'><ul>{''.join(manifest_items)}</ul></div>",
                },
            })

    all_obs_ids = {obs["id"] for obs in all_observations} | {f"urn:uuid:{obs['id']}" for obs in all_observations}
    valid_performer_ids = set(all_practitioners.keys()) | set(all_organizations.keys())

    obs_references: list[dict[str, str]] = []
    for obs in all_observations:
        obs["subject"] = {"reference": patient_ref}
        if not obs.get("status"):
            obs["status"] = "final"
        if "meta" not in obs:
            obs["meta"] = {}
        if ABDM_OBSERVATION_PROFILE not in obs["meta"].get("profile", []):
            obs["meta"]["profile"] = [ABDM_OBSERVATION_PROFILE]
        obs_id = obs["id"]
        obs_references.append({"reference": f"urn:uuid:{obs_id}"})

        performers = obs.get("performer", []) or []
        cleaned_performers = []
        for perf in performers:
            if isinstance(perf, dict) and "reference" in perf:
                ref_str = str(perf["reference"])
                raw_id = ref_str.replace("urn:uuid:", "").strip()
                if raw_id in valid_performer_ids:
                    cleaned_performers.append({"reference": f"urn:uuid:{raw_id}"})
                else:
                    cleaned_performers.append({"reference": organization_ref})
            elif isinstance(perf, dict):
                cleaned_performers.append({"reference": organization_ref})

        obs["performer"] = cleaned_performers or [{"reference": organization_ref}]

        for member_field in ("hasMember", "derivedFrom"):
            if member_field in obs and isinstance(obs[member_field], list):
                valid_members = []
                for item in obs[member_field]:
                    if isinstance(item, dict) and "reference" in item:
                        m_ref = str(item["reference"])
                        m_id = m_ref.replace("urn:uuid:", "").strip()
                        if m_id in all_obs_ids or m_ref in all_obs_ids:
                            valid_members.append({"reference": f"urn:uuid:{m_id}"})
                if valid_members:
                    obs[member_field] = valid_members
                else:
                    obs.pop(member_field, None)

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    report_id = str(uuid.uuid4())
    doc_count_str = f"{len(bundles)} lab report(s)" if bundles else f"{len(all_observations)} observation(s)"

    diagnostic_report = {
        "resourceType": "DiagnosticReport",
        "id": report_id,
        "meta": {
            "profile": [ABDM_DIAGNOSTIC_REPORT_PROFILE],
        },
        "status": "final",
        "category": [
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/v2-074",
                        "code": "LAB",
                        "display": "Laboratory",
                    }
                ]
            }
        ],
        "code": {
            "coding": [
                {
                    "system": "http://loinc.org",
                    "code": "26436-6",
                    "display": "Laboratory studies",
                }
            ],
            "text": f"Consolidated Diagnostic Report ({doc_count_str})",
        },
        "subject": {"reference": patient_ref},
        "issued": now_iso,
        "performer": [{"reference": practitioner_ref}],
        "result": obs_references,
        "conclusion": f"Consolidated clinical records extracted across {archive_filename}.",
    }

    composition_id = str(uuid.uuid4())
    composition = {
        "resourceType": "Composition",
        "id": composition_id,
        "meta": {
            "profile": [ABDM_COMPOSITION_PROFILE],
        },
        "status": "final",
        "type": {
            "coding": [
                {
                    "system": "http://snomed.info/sct",
                    "code": "4241000179101",
                    "display": "Laboratory report",
                }
            ],
            "text": "Laboratory report",
        },
        "subject": {"reference": patient_ref},
        "date": now_iso,
        "author": [{"reference": practitioner_ref}],
        "title": f"Consolidated Diagnostic Report Record ({archive_filename})",
        "custodian": {"reference": organization_ref},
        "section": sections or [
            {
                "title": "Consolidated Laboratory Results",
                "entry": obs_references,
            }
        ],
    }

    bundle_id = str(uuid.uuid4())
    bundle_entries: list[dict[str, Any]] = [
        {"fullUrl": f"urn:uuid:{composition_id}", "resource": composition},
        {"fullUrl": patient_ref, "resource": canonical_patient},
    ]

    for practitioner in all_practitioners.values():
        bundle_entries.append({
            "fullUrl": f"urn:uuid:{practitioner['id']}",
            "resource": practitioner,
        })

    for organization in all_organizations.values():
        bundle_entries.append({
            "fullUrl": f"urn:uuid:{organization['id']}",
            "resource": organization,
        })

    bundle_entries.append({
        "fullUrl": f"urn:uuid:{report_id}",
        "resource": diagnostic_report,
    })

    for obs in all_observations:
        bundle_entries.append({
            "fullUrl": f"urn:uuid:{obs['id']}",
            "resource": obs,
        })

    master_bundle = {
        "resourceType": "Bundle",
        "id": bundle_id,
        "meta": {
            "versionId": "1",
            "lastUpdated": now_iso,
            "profile": [ABDM_DOCUMENT_BUNDLE_PROFILE],
        },
        "identifier": {
            "system": "https://abdm.gov.in/bundle",
            "value": bundle_id,
        },
        "type": "document",
        "timestamp": now_iso,
        "entry": bundle_entries,
    }

    return master_bundle
