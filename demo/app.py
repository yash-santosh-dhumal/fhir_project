"""Small Flask demo for Google Health Medical Data Toolkit."""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import sys
import threading
import time
from typing import Any
import uuid as uuid_mod

from flask import Flask, jsonify, render_template, request
import requests

_DEMO_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _DEMO_DIR.parent
for _p in (str(_DEMO_DIR), str(_REPO_ROOT)):
  if _p not in sys.path:
    sys.path.insert(0, _p)

try:
  from archive_analyzer import (
      ArchiveAnalysisResult,
      ArchiveDocument,
      ArchiveFormatError,
      ArchiveSecurityError,
      extract_and_analyze_archive,
  )
  from claim_summarizer import summarize_claim
  from insurance_plan_extractor import extract_insurance_plan
  from fhir_store import FhirStore
  from fhir_summary import summarize_payload
  from fhir_unifier import unify_patient_bundles
  from fhir_validator import validate_bundle
  from gemini_ocr import process_archive_documents_with_gemini
except ImportError:
  from demo.archive_analyzer import (
      ArchiveAnalysisResult,
      ArchiveDocument,
      ArchiveFormatError,
      ArchiveSecurityError,
      extract_and_analyze_archive,
  )
  from demo.claim_summarizer import summarize_claim
  from demo.insurance_plan_extractor import extract_insurance_plan
  from demo.fhir_store import FhirStore
  from demo.fhir_summary import summarize_payload
  from demo.fhir_unifier import unify_patient_bundles
  from demo.fhir_validator import validate_bundle
  from demo.gemini_ocr import process_archive_documents_with_gemini

try:
  from dotenv import load_dotenv

  load_dotenv(_REPO_ROOT / ".env", override=True)
except ImportError:
  pass

TOOLKIT_URL = os.environ.get("TOOLKIT_URL", "http://localhost:8088").rstrip("/")
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("TOOLKIT_TIMEOUT_SECONDS", "180"))
TOOLKIT_CONCURRENT_WORKERS = int(os.environ.get("TOOLKIT_CONCURRENT_WORKERS", "4"))
SUPPORTED_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/bmp",
    "image/tiff",
    "application/zip",
    "application/x-zip-compressed",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

app = Flask(__name__)
fhir_store = FhirStore()
insurance_store = fhir_store
log = logging.getLogger(__name__)


@app.get("/")
def index():
  return render_template("index.html")


@app.get("/api/health")
def health():
  toolkit_status = "unavailable"
  toolkit_message = ""
  try:
    response = requests.get(
        f"{TOOLKIT_URL}/", timeout=min(5, REQUEST_TIMEOUT_SECONDS)
    )
    if response.ok:
      toolkit_status = "ok"
    else:
      toolkit_message = f"Toolkit health check returned HTTP {response.status_code}."
  except requests.RequestException as exc:
    toolkit_message = str(exc)

  return jsonify({
      "demo": "ok",
      "toolkit": toolkit_status,
      "toolkit_url": TOOLKIT_URL,
      "message": toolkit_message,
  })


def _optimize_image_if_needed(file_bytes: bytes, mime_type: str) -> tuple[bytes, str]:
  """Optimizes image resolution and compression for faster network upload and model processing."""
  if mime_type not in ("image/jpeg", "image/png"):
    return file_bytes, mime_type
  try:
    import io
    import PIL.Image

    with PIL.Image.open(io.BytesIO(file_bytes)) as img:
      w, h = img.size
      max_dim = max(w, h)
      if max_dim > 1600 or len(file_bytes) > 300 * 1024:
        if max_dim > 1600:
          scale = 1600.0 / max_dim
          new_size = (int(w * scale), int(h * scale))
          img = img.resize(new_size, PIL.Image.Resampling.LANCZOS)
        if img.mode != "RGB":
          img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        opt_bytes = buf.getvalue()
        if len(opt_bytes) < len(file_bytes):
          return opt_bytes, "image/jpeg"
  except Exception:
    pass
  return file_bytes, mime_type


def _do_convert(store: FhirStore, skip_insurance: bool = True):
  """Shared conversion logic used by both hospital and insurance portals.

  Returns:
      Tuple of (response_data_dict, http_status_code, gemini_extraction_or_None).
      The caller is responsible for calling jsonify on the dict.
  """
  uploaded = request.files.get("file")
  if uploaded is None or uploaded.filename == "":
    return _error_dict("No file was uploaded."), 400, None

  mime_type = uploaded.mimetype or _infer_mime_type(uploaded.filename)
  if mime_type not in SUPPORTED_MIME_TYPES:
    return _error_dict("Supported formats are PDF, JPEG, PNG, WebP, BMP, TIFF, and ZIP archives."), 400, None

  file_bytes = uploaded.read()
  if not file_bytes:
    return _error_dict("The uploaded file is empty."), 400, None

  # Check if uploaded file is a ZIP archive containing multiple documents for the same patient
  if mime_type in ("application/zip", "application/x-zip-compressed") or (uploaded.filename and uploaded.filename.lower().endswith(".zip")):
    try:
      analysis = extract_and_analyze_archive(file_bytes, archive_name=uploaded.filename or "patient_archive.zip")
    except (ArchiveSecurityError, ArchiveFormatError) as exc:
      return _error_dict(f"Archive error: {exc}"), 400, None
    except Exception as exc:
      return _error_dict(f"Failed to process archive: {exc}"), 500, None

    if analysis.document_count == 0:
      return _error_dict("No valid medical documents (PDF or images) found in the ZIP archive."), 400, None

    start = time.perf_counter()

    # 1. Identify candidate lab reports using precise clinical vocabulary while excluding administrative forms
    non_lab_folders = (
        "consent", "counseling", "dischargesummary", "bills", "bill",
        "case sheet", "jeevandaan", "preauthorisation", "preauth",
        "satisfactory", "transportation", "dtrs", "tumor board",
        "treatment plan", "operation", "icu", "ward",
    )
    lab_keywords = (
        "cbc", "cbp", "blood", "pathology", "biochemistry", "serum",
        "lipid", "lft", "rft", "kft", "bone marrow", "bone    marrow",
        "biopsy", "hpe", "colonoscopy", "culture", "urine", "stool",
        "hematology", "haematology", "lab_report", "lab report",
    )
    candidate_lab_docs = []
    for doc in analysis.documents:
      if not getattr(doc, "is_clinical_text_document", True):
        continue
      fl = doc.folder_path.lower()
      fn = doc.filename.lower()
      if any(f in fl for f in non_lab_folders):
        continue
      if any(k in fn for k in lab_keywords) or "investigation" in fl:
        candidate_lab_docs.append(doc)

    # Prioritize primary quantitative laboratory reports (CBC, CBP, Serum, etc.) and limit to top 2 for toolkit API
    def _lab_priority_score(d: Any) -> int:
      fn = d.filename.lower()
      if any(k in fn for k in ("cbp", "cbc", "complete blood")): return 0
      if any(k in fn for k in ("serum", "biochemistry", "lft", "rft", "kft")): return 1
      if any(k in fn for k in ("bone marrow", "bone    marrow", "biopsy", "hpe")): return 2
      if any(k in fn for k in ("blood group", "blood")): return 3
      return 4

    candidate_lab_docs.sort(key=_lab_priority_score)
    candidate_lab_docs = candidate_lab_docs[:2]

    def _call_toolkit_api(doc_bytes: bytes, doc_mime: str) -> dict:
      resp = requests.post(
          f"{TOOLKIT_URL}/document_to_fhir",
          data=doc_bytes,
          headers={"Content-Type": doc_mime},
          timeout=REQUEST_TIMEOUT_SECONDS,
      )
      if not resp.ok:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
      return resp.json()

    def _run_toolkit_for_docs(docs: list[Any]) -> tuple[list[dict[str, Any]], list[str]]:
      bundles_out: list[dict[str, Any]] = []
      names_out: list[str] = []
      if not docs:
        return bundles_out, names_out

      max_workers = min(TOOLKIT_CONCURRENT_WORKERS, len(docs))
      log.info("Checking %d candidate lab reports with toolkit API in parallel (%d workers)...",
               len(docs), max_workers)

      def _process_candidate(idx_and_doc: tuple[int, Any]) -> tuple[int, str, list[dict[str, Any]]]:
        idx, doc = idx_and_doc
        doc_bundles: list[dict[str, Any]] = []
        try:
          doc_bytes, doc_mime = _optimize_image_if_needed(doc.file_bytes, doc.mime_type)
          pl = _call_toolkit_api(doc_bytes, doc_mime)
          for sdoc in (pl.get("standardized_medical_documents") or []):
            if isinstance(sdoc, dict):
              fb = sdoc.get("fhir_bundle")
              if isinstance(fb, dict) and fb.get("resourceType") == "Bundle":
                doc_bundles.append(fb)
        except Exception as exc:
          log.warning("Toolkit API skipped for candidate '%s': %s", doc.relative_path, exc)
        return idx, doc.relative_path, doc_bundles

      candidate_results: list[tuple[str, list[dict[str, Any]]]] = [("", [])] * len(docs)

      with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(_process_candidate, (i, d)): i
            for i, d in enumerate(docs)
        }
        for future in as_completed(future_map):
          res_idx, rel_path, bundles = future.result()
          candidate_results[res_idx] = (rel_path, bundles)

      # Deterministically preserve candidate document ordering
      for rel_path, bundles in candidate_results:
        for fb in bundles:
          bundles_out.append(fb)
          names_out.append(rel_path)

      return bundles_out, names_out

    # 2. Advanced Concurrent Pipeline: Overlap Gemini OCR and Toolkit API simultaneously
    log.info("Launching concurrent processing: Gemini OCR (%d docs) and Toolkit API (%d candidate lab docs)...",
             analysis.document_count, len(candidate_lab_docs))

    with ThreadPoolExecutor(max_workers=2) as pipeline_executor:
      gemini_future = pipeline_executor.submit(
          process_archive_documents_with_gemini,
          analysis.documents,
          10,
          skip_insurance,
      )
      toolkit_future = pipeline_executor.submit(_run_toolkit_for_docs, candidate_lab_docs)

      gemini_extraction = gemini_future.result()
      extracted_bundles, source_names = toolkit_future.result()

    # Safety check: if Gemini OCR identified an actual laboratory test document that wasn't in candidate_lab_docs,
    # process only those specific lab test documents with toolkit API.
    already_processed_paths = {d.relative_path for d in candidate_lab_docs}
    additional_candidates = []
    specific_lab_types = ("laboratory", "pathology", "biochemistry", "complete blood", "cbc", "cbp", "blood test", "bone marrow", "biopsy")
    for doc in analysis.documents:
      if doc.relative_path not in already_processed_paths and gemini_extraction.documents:
        for edoc in gemini_extraction.documents:
          if (edoc.filename == doc.filename or edoc.relative_path == doc.relative_path):
            dt_lower = edoc.document_type.lower()
            if any(k in dt_lower for k in specific_lab_types) and not any(f in dt_lower for f in ("discharge", "sheet", "summary", "consent")):
              additional_candidates.append(doc)
              break

    if additional_candidates:
      log.info("Processing %d additional lab document(s) discovered by Gemini OCR...", len(additional_candidates))
      extra_bundles, extra_names = _run_toolkit_for_docs(additional_candidates)
      extracted_bundles.extend(extra_bundles)
      source_names.extend(extra_names)

    # 3. Unify all bundles and Gemini-extracted demographics, billing, and observations
    master_bundle = unify_patient_bundles(
        extracted_bundles,
        archive_filename=uploaded.filename or "patient_archive.zip",
        source_filenames=source_names,
        gemini_extraction=gemini_extraction,
    )

    duration_ms = round((time.perf_counter() - start) * 1000, 2)

    # Store master bundle in SQLite under the unified patient
    bid = store.store_bundle(
        master_bundle,
        source_filename=f"{uploaded.filename} ({analysis.document_count} documents unified)",
        document_type="CLINICAL_RECORD",
    )

    # Find patient_id for stored bundle
    patient_res = next(
        (e.get("resource", {}) for e in master_bundle.get("entry", []) if e.get("resource", {}).get("resourceType") == "Patient"),
        None,
    )
    patient_id = store.upsert_patient(patient_res) if patient_res else None

    # Summarize and validate unified bundle
    unified_payload = {"standardized_medical_documents": [{"fhir_bundle": master_bundle, "document_type": "CLINICAL_RECORD"}]}
    summary = summarize_payload(unified_payload)
    validation = validate_bundle(master_bundle)

    return {
        "duration_ms": duration_ms,
        "summary": summary,
        "raw": master_bundle,
        "validation": validation,
        "stored_bundle_ids": [bid],
        "patient_id": patient_id,
        "is_archive": True,
        "archive_info": {
            "archive_name": uploaded.filename,
            "total_files_in_zip": analysis.total_entries_scanned,
            "documents_found": analysis.document_count,
            "documents_processed": analysis.document_count,
            "documents_unified": len(extracted_bundles) if extracted_bundles else analysis.document_count,
            "patient_name": gemini_extraction.demographics.name or "Patient",
            "birth_date": gemini_extraction.demographics.birth_date or "N/A",
            "gender": gemini_extraction.demographics.gender or "unknown",
            "aadhaar_number": gemini_extraction.demographics.aadhaar_number or "N/A",
            "address": gemini_extraction.demographics.address or "N/A",
            "observations_count": len(summary.get("observations", [])),
            "classified_documents": [d.to_dict() for d in gemini_extraction.documents],
            "source_files": source_names if extracted_bundles else [d.relative_path for d in analysis.documents],
            "skipped_count": analysis.skipped_count,
            "insurance_policy": gemini_extraction.insurance_policy.to_dict() if gemini_extraction.insurance_policy else None,
            "insurance_adjudication": summary.get("insurance_adjudication"),
        },
    }, 200, gemini_extraction

  file_bytes, mime_type = _optimize_image_if_needed(file_bytes, mime_type)

  start = time.perf_counter()
  try:
    toolkit_response = requests.post(
        f"{TOOLKIT_URL}/document_to_fhir",
        data=file_bytes,
        headers={"Content-Type": mime_type},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
  except requests.ConnectionError:
    return _error_dict(
        f"Medical Data Toolkit is not reachable at {TOOLKIT_URL}. Ensure the backend service is running on port 8088.",
    ), 503, None
  except requests.Timeout:
    return _error_dict("Medical Data Toolkit timed out while processing the document."), 504, None
  except requests.RequestException as exc:
    return _error_dict(f"Toolkit request failed: {exc}"), 502, None

  duration_ms = round((time.perf_counter() - start) * 1000, 2)
  safe_error = _safe_toolkit_error(toolkit_response)

  # Parse toolkit response first so 'summary' is available for fallback check
  payload = {}
  standardized_docs = []
  summary = {}
  if toolkit_response.ok:
    try:
      payload = toolkit_response.json()
      standardized_docs = payload.get("standardized_medical_documents", []) or []
      summary = summarize_payload(payload)
    except Exception:
      pass

  # If toolkit cannot process this document (e.g. Aadhaar, Discharge Summary, Non-Lab),
  # fall back to Gemini high-level OCR to extract patient demographics and generate FHIR
  if not toolkit_response.ok or not summary.get("bundle", {}).get("found"):
    log.info("Toolkit could not convert '%s' (status=%s); executing Gemini high-level OCR fallback...",
             uploaded.filename, toolkit_response.status_code)
    try:
      single_doc = ArchiveDocument(
          filename=uploaded.filename or "document",
          relative_path=uploaded.filename or "document",
          folder_path="",
          file_bytes=file_bytes,
          mime_type=mime_type,
          size_bytes=len(file_bytes),
      )
      gemini_extraction = process_archive_documents_with_gemini([single_doc], 5, skip_insurance)
      master_bundle = unify_patient_bundles(
          [],
          archive_filename=uploaded.filename or "document",
          source_filenames=[uploaded.filename or "document"],
          gemini_extraction=gemini_extraction,
      )
      bid = store.store_bundle(
          master_bundle,
          source_filename=uploaded.filename or "document",
          document_type="CLINICAL_RECORD",
      )
      unified_payload = {"standardized_medical_documents": [{"fhir_bundle": master_bundle, "document_type": "CLINICAL_RECORD"}]}
      summary = summarize_payload(unified_payload)
      validation = validate_bundle(master_bundle)
      return {
          "duration_ms": duration_ms,
          "summary": summary,
          "raw": master_bundle,
          "validation": validation,
          "stored_bundle_ids": [bid],
      }, 200, gemini_extraction
    except Exception as exc:
      log.error("Gemini fallback failed for '%s': %s", uploaded.filename, exc)
      if not toolkit_response.ok:
        return _error_dict(
            f"Document conversion failed: {safe_error}",
            toolkit_response.status_code,
            duration_ms,
        ), 422, None
      return _error_dict(
          "The document could not be converted to a FHIR bundle.",
          toolkit_response.status_code,
          duration_ms,
      ), 422, None

  # Auto-store bundle in FHIR store
  bundles_from_payload = []
  for doc in standardized_docs:
    fb = doc.get("fhir_bundle") if isinstance(doc, dict) else None
    if isinstance(fb, dict) and fb.get("resourceType") == "Bundle":
      bundles_from_payload.append(fb)

  stored_bundle_ids = []
  for fb in bundles_from_payload:
    src_name = uploaded.filename if uploaded else ""
    doc_type = standardized_docs[0].get("document_type", "") if standardized_docs else ""
    bid = store.store_bundle(fb, source_filename=src_name, document_type=doc_type)
    stored_bundle_ids.append(bid)

  # Run validation
  validation = validate_bundle(payload)

  return {
      "duration_ms": duration_ms,
      "summary": summary,
      "raw": payload,
      "validation": validation,
      "stored_bundle_ids": stored_bundle_ids,
  }, 200, None


@app.post("/api/convert")
def convert():
  data, status, _extraction = _do_convert(fhir_store)
  return jsonify(data), status


# ── Patient records & Unified Adjudication ──

def _get_enriched_patients_list() -> list[dict[str, Any]]:
    """Returns all patients enriched with real-time billing and insurance adjudication status."""
    patients = fhir_store.get_all_patients()
    enriched: list[dict[str, Any]] = []
    for p in patients:
        p_dict = dict(p)
        pid = p["id"]
        bundles = fhir_store.get_bundles_for_patient(pid)
        p_dict["billed_amount"] = 0.0
        p_dict["bill_number"] = ""
        p_dict["is_adjudicated"] = False
        p_dict["insured_amount"] = 0.0
        p_dict["patient_payable"] = 0.0
        p_dict["coverage_percentage"] = 0.0
        p_dict["adjudication_status"] = "Pending Policy Upload"
        p_dict["scheme"] = ""
        p_dict["policy_number"] = ""

        if bundles:
            try:
                b_json = json.loads(bundles[0]["bundle_json"]) if isinstance(bundles[0].get("bundle_json"), str) else bundles[0].get("bundle_json", {})
                entries = b_json.get("entry", [])
                resources = [e.get("resource", {}) for e in entries if e.get("resource")]

                # 1. Hospital Claim / Bill
                claims = [r for r in resources if r.get("resourceType") == "Claim"]
                if claims:
                    demo_claim = next((
                        c for c in claims
                        if any("demo bill" in (s.get("valueString") or "").lower() or "hospital_bill" in (s.get("valueString") or "").lower() for s in c.get("supportingInfo", []))
                    ), claims[0])
                    p_dict["billed_amount"] = float(demo_claim.get("total", {}).get("value", 0.0) or 0.0)
                    p_dict["bill_number"] = demo_claim.get("id", "")

                # 2. Coverage
                cov = next((r for r in resources if r.get("resourceType") == "Coverage"), None)
                if cov:
                    p_dict["policy_number"] = cov.get("subscriberId") or (cov.get("identifier", [{}])[0].get("value") if cov.get("identifier") else "")
                    payor_list = cov.get("payor", [])
                    p_dict["scheme"] = (payor_list[0].get("display") if payor_list else "") or (cov.get("type", {}).get("coding", [{}])[0].get("display") if cov.get("type") else "")

                # 3. ClaimResponse
                cr = next((r for r in resources if r.get("resourceType") == "ClaimResponse"), None)
                if cr and cov:
                    p_dict["is_adjudicated"] = True
                    for tot in cr.get("total", []):
                        codes = [c.get("code") for c in tot.get("category", {}).get("coding", [])]
                        val = float(tot.get("amount", {}).get("value", 0.0) or 0.0)
                        if "benefit" in codes:
                            p_dict["insured_amount"] = val
                        elif "patientoutoppocket" in codes or "copay" in codes:
                            p_dict["patient_payable"] = val
                        elif "submitted" in codes and p_dict["billed_amount"] == 0:
                            p_dict["billed_amount"] = val

                    if p_dict["billed_amount"] > 0:
                        p_dict["coverage_percentage"] = round((p_dict["insured_amount"] / p_dict["billed_amount"]) * 100.0, 1)
                        if p_dict["patient_payable"] == 0 and p_dict["insured_amount"] >= p_dict["billed_amount"]:
                            p_dict["adjudication_status"] = "100% Cashless Approved"
                        elif p_dict["insured_amount"] > 0:
                            p_dict["adjudication_status"] = f"Partially Covered ({p_dict['coverage_percentage']}%)"
                        else:
                            p_dict["adjudication_status"] = "Not Covered"
                    else:
                        p_dict["adjudication_status"] = "Adjudicated"
            except Exception as exc:
                log.warning("Failed to enrich patient summary for %s: %s", pid, exc)

        enriched.append(p_dict)
    return enriched


@app.get("/api/patients")
def list_patients():
    return jsonify(_get_enriched_patients_list())


@app.get("/api/patients/<patient_id>")
def get_patient(patient_id: str):
    patient = fhir_store.get_patient(patient_id)
    if not patient:
        return _error("Patient not found.", 404)
    bundles = fhir_store.get_bundles_for_patient(patient_id)
    observations = fhir_store.get_observations_for_patient(patient_id)
    return jsonify({"patient": patient, "bundles": bundles, "observations": observations})


def _adjudicate_patient_with_policy(
    patient_id: str,
    policy: Any,
    bundle_id: str | None = None,
) -> dict[str, Any]:
    """Adjudicates patient's hospital bills against an insurance policy and updates FHIR resources."""
    from demo.fhir_unifier import (
        _build_coverage_resource,
        _build_claim_response_resource,
        _adjudicate_bill_against_policy,
    )
    from demo.gemini_ocr import ExtractedBillingData, ExtractedBillingItem

    bundles = fhir_store.get_bundles_for_patient(patient_id)
    if not bundles:
        raise ValueError("No clinical bundle found for this patient.")

    target_bundle = None
    if bundle_id:
        target_bundle = next((b for b in bundles if b["id"] == bundle_id), None)
    if not target_bundle:
        target_bundle = bundles[0]

    b_id = target_bundle["id"]
    b_json = json.loads(target_bundle["bundle_json"]) if isinstance(target_bundle.get("bundle_json"), str) else target_bundle.get("bundle_json", {})
    entries = b_json.get("entry", [])
    resources = [e.get("resource", {}) for e in entries if e.get("resource")]
    claims = [r for r in resources if r.get("resourceType") == "Claim"]
    if not claims:
        raise ValueError("No hospital bill (Claim resource) found in patient record.")

    demo_claims = [
        c for c in claims
        if any("demo bill" in (s.get("valueString") or "").lower() or "hospital_bill" in (s.get("valueString") or "").lower() for s in c.get("supportingInfo", []))
    ]
    target_claim = demo_claims[0] if demo_claims else claims[0]

    # Prune other claims if demo bill found
    if demo_claims and len(claims) > 1:
        valid_claim_ids = {target_claim["id"]}
        b_json["entry"] = [
            e for e in b_json.get("entry", [])
            if e.get("resource", {}).get("resourceType") != "Claim" or e.get("resource", {}).get("id") in valid_claim_ids
        ]
        comp = next((e.get("resource") for e in b_json.get("entry", []) if e.get("resource", {}).get("resourceType") == "Composition"), None)
        if comp:
            for s in comp.get("section", []):
                if "Billing" in s.get("title", ""):
                    s["entry"] = [{"reference": f"urn:uuid:{target_claim['id']}"}]

    # Clean existing Coverage and ClaimResponse
    b_json["entry"] = [
        e for e in b_json.get("entry", [])
        if e.get("resource", {}).get("resourceType") not in ("Coverage", "ClaimResponse")
    ]

    p_res = next((r for r in resources if r.get("resourceType") == "Patient"), None)
    pref = f"urn:uuid:{p_res['id']}" if p_res else f"urn:uuid:{patient_id}"
    org_res = next((r for r in resources if r.get("resourceType") == "Organization"), None)
    oref = f"urn:uuid:{org_res['id']}" if org_res else "urn:uuid:org"

    cov_res = _build_coverage_resource(policy, pref, oref)
    bill_total = float(target_claim.get("total", {}).get("value", 0.0) or 0.0)

    bill_items = []
    for item in target_claim.get("item", []):
        cat_obj = item.get("category") or {}
        cat = cat_obj.get("text") or (cat_obj.get("coding") or [{}])[0].get("display") or "General"
        desc = item.get("productOrService", {}).get("text", "")
        net_val = item.get("net", {}).get("value")
        qty = int(item.get("quantity", {}).get("value", 1) or 1)
        unit_val = float(item.get("unitPrice", {}).get("value", 0.0) or 0.0)
        amt = float(net_val) if net_val is not None and float(net_val) > 0 else (unit_val * qty)
        if amt > 0:
            bill_items.append(ExtractedBillingItem(
                description=desc,
                amount=float(amt),
                category=cat,
                quantity=qty,
                unit_price=unit_val,
            ))

    bill_data = [
        ExtractedBillingData(
            bill_number=target_claim.get("id", "BILL"),
            total_amount=bill_total,
            source_document=getattr(policy, "source_document", "") or "Hospital_Bill",
            items=bill_items,
        )
    ]

    adj = _adjudicate_bill_against_policy(bill_data, policy)
    adj["insurer_name"] = adj.get("scheme_or_insurer") or getattr(policy, "scheme_or_insurer", "") or "Insurance Scheme"
    claim_res = _build_claim_response_resource(
        claim_ref=f"urn:uuid:{target_claim['id']}",
        coverage_ref=f"urn:uuid:{cov_res['id']}",
        patient_ref=pref,
        organization_ref=oref,
        adjudication=adj,
    )

    b_json.setdefault("entry", []).append({"fullUrl": f"urn:uuid:{cov_res['id']}", "resource": cov_res})
    b_json.setdefault("entry", []).append({"fullUrl": f"urn:uuid:{claim_res['id']}", "resource": claim_res})

    comp = next((e.get("resource") for e in b_json.get("entry", []) if e.get("resource", {}).get("resourceType") == "Composition"), None)
    if comp:
        comp_secs = comp.setdefault("section", [])
        comp_secs[:] = [s for s in comp_secs if not any(k in s.get("title", "") for k in ("Insurance Coverage", "Adjudication"))]
        comp_secs.append({
            "title": "Insurance Coverage & Claim Adjudication",
            "entry": [
                {"reference": f"urn:uuid:{cov_res['id']}"},
                {"reference": f"urn:uuid:{claim_res['id']}"},
            ],
            "text": {
                "status": "additional",
                "div": (
                    f"<div xmlns='http://www.w3.org/1999/xhtml'>"
                    f"<p><strong>Policy Number:</strong> {policy.policy_number} | <strong>Scheme:</strong> {policy.scheme_or_insurer}</p>"
                    f"<p><strong>Status:</strong> {adj['status']} | <strong>Coverage:</strong> {adj['coverage_percentage']}%</p>"
                    f"<p><strong>Total Billed Amount:</strong> INR {adj['total_billed']:,.2f}</p>"
                    f"<p><strong>Insured / Covered Amount:</strong> INR {adj['insured_amount']:,.2f}</p>"
                    f"<p><strong>Patient Out-of-Pocket Liability:</strong> INR {adj['patient_payable']:,.2f}</p>"
                    f"<p><strong>Adjudication Notes:</strong> {adj['notes']}</p>"
                    f"</div>"
                ),
            },
        })

    fhir_store.update_bundle_json(b_id, json.dumps(b_json))

    return {
        "adjudication": adj,
        "policy": policy.to_dict() if hasattr(policy, "to_dict") else policy,
        "bundle_id": b_id,
        "bundle": b_json,
    }


# ── Portal Authentication Routes ──

@app.post("/api/auth/login")
def auth_login():
    data = request.get_json(silent=True) or {}
    role = (data.get("role") or "").lower().strip()
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()

    if role == "hospital":
        if username in ("hospital", "hospital_admin", "admin", "apollo") or not password or password == "hospital123":
            return jsonify({
                "success": True,
                "user": {
                    "role": "hospital",
                    "username": username or "hospital_admin",
                    "display_name": "Dr. R. Sharma",
                    "role_title": "Hospital Administrator & Clinician",
                    "facility": "Apollo Specialty Hospitals, AP",
                    "portal_label": "Hospital Information System (HIS)",
                }
            })
        return _error("Invalid hospital credentials. Use demo: hospital_admin / hospital123", 401)

    elif role == "insurance":
        if username in ("insurance", "insurance_auditor", "auditor", "tpa", "star") or not password or password == "insurance123":
            return jsonify({
                "success": True,
                "user": {
                    "role": "insurance",
                    "username": username or "insurance_auditor",
                    "display_name": "K. V. Raman",
                    "role_title": "Senior Claims Officer & TPA Auditor",
                    "facility": "Health Insurance Claims Authority (TPA)",
                    "portal_label": "Health Insurance Claims Authority",
                }
            })
        return _error("Invalid insurance credentials. Use demo: insurance_auditor / insurance123", 401)

    return _error("Invalid portal selection. Specify role 'hospital' or 'insurance'.", 400)


@app.post("/api/auth/logout")
def auth_logout():
    return jsonify({"success": True, "message": "Logged out successfully."})


# ── Insurance Policy Upload & Adjudication Routes ──

@app.get("/api/insurance/sample-policies")
def insurance_sample_policies():
    """Lists available pre-loaded insurance policy documents with patient association hints."""
    policies_dir = _DEMO_DIR / "sample" / "insurance policies"
    if not policies_dir.exists():
        return jsonify([])

    PATIENT_HINTS = {
        "AP12363098": "Koppula Venkata Ramudu (Bill: ₹42,962 • 80:20 Co-Pay)",
        "AP12361877": "Ballari Ganganna (Bill: ₹38,500 • Co-Pay)",
        "AP12362013": "MRS. RUKSANA (Surgical Oncology • Cashless)",
        "AP12362282": "Kanala Parvathamma (In-Patient Treatment • Co-Pay)",
        "AP12362881": "Poosuloori Maharaju (Biopsy & Oncology • Cashless)",
    }

    result = []
    for p in sorted(policies_dir.glob("*.pdf")):
        hint = ""
        for k, v in PATIENT_HINTS.items():
            if k in p.name:
                hint = v
                break
        result.append({
            "filename": p.name,
            "patient_hint": hint or "Sample Insurance Policy Document",
            "size_kb": round(p.stat().st_size / 1024, 1),
        })
    return jsonify(result)


@app.post("/api/insurance/patients/<patient_id>/adjudicate-policy")
def insurance_adjudicate_uploaded_policy(patient_id: str):
    """Processes uploaded insurance policy document for patient, recalculates coverage and generates settlement."""
    patient = fhir_store.get_patient(patient_id)
    if not patient:
        return _error("Patient not found.", 404)

    if "file" not in request.files:
        return _error("No policy document file provided in request.", 400)
    uploaded = request.files["file"]
    if not uploaded or not uploaded.filename:
        return _error("Empty policy document file.", 400)

    file_bytes = uploaded.read()
    if not file_bytes:
        return _error("Uploaded policy document is empty.", 400)

    mime_type = uploaded.mimetype or _infer_mime_type(uploaded.filename)
    from demo.gemini_ocr import extract_insurance_policy_from_doc
    policy = extract_insurance_policy_from_doc(file_bytes, mime_type, uploaded.filename)
    if not policy:
        return _error("Could not extract insurance policy details from document. Ensure it is a valid policy PDF or image.", 422)

    try:
        adj_result = _adjudicate_patient_with_policy(patient_id, policy)
        return jsonify({
            "success": True,
            "patient_id": patient_id,
            "patient_name": patient.get("name"),
            "adjudication": adj_result["adjudication"],
            "insurance_policy": adj_result["policy"],
            "bundle_id": adj_result["bundle_id"],
            "message": f"Successfully adjudicated claim for {patient.get('name')}. Approved: INR {adj_result['adjudication']['insured_amount']:,.2f} ({adj_result['adjudication']['coverage_percentage']}%).",
        })
    except Exception as exc:
        log.exception("Adjudication failed for patient %s: %s", patient_id, exc)
        return _error(f"Adjudication failed: {exc}", 500)


@app.post("/api/insurance/patients/<patient_id>/adjudicate-sample-policy")
def insurance_adjudicate_sample_policy(patient_id: str):
    """Adjudicates patient claim using a pre-loaded sample policy file for quick 1-click verification."""
    patient = fhir_store.get_patient(patient_id)
    if not patient:
        return _error("Patient not found.", 404)

    data = request.get_json(silent=True) or {}
    filename = data.get("policy_filename", "")
    if not filename:
        return _error("No policy_filename provided.", 400)

    policy_path = _DEMO_DIR / "sample" / "insurance policies" / filename
    if not policy_path.exists() or not policy_path.is_file():
        return _error(f"Sample policy file '{filename}' not found.", 404)

    file_bytes = policy_path.read_bytes()
    from demo.gemini_ocr import extract_insurance_policy_from_doc
    policy = extract_insurance_policy_from_doc(file_bytes, "application/pdf", filename)
    if not policy:
        return _error("Failed to extract policy details from sample document.", 500)

    try:
        adj_result = _adjudicate_patient_with_policy(patient_id, policy)
        return jsonify({
            "success": True,
            "patient_id": patient_id,
            "patient_name": patient.get("name"),
            "adjudication": adj_result["adjudication"],
            "insurance_policy": adj_result["policy"],
            "bundle_id": adj_result["bundle_id"],
            "message": f"Successfully adjudicated claim for {patient.get('name')} using {filename}.",
        })
    except Exception as exc:
        log.exception("Sample adjudication failed for patient %s: %s", patient_id, exc)
        return _error(f"Adjudication failed: {exc}", 500)


@app.post("/api/insurance/patients/<patient_id>/reset-adjudication")
def insurance_reset_adjudication(patient_id: str):
    """Strips Coverage and ClaimResponse to allow re-testing policy upload flow."""
    bundles = fhir_store.get_bundles_for_patient(patient_id)
    if not bundles:
        return _error("Patient not found.", 404)
    target = bundles[0]
    b_json = json.loads(target["bundle_json"]) if isinstance(target.get("bundle_json"), str) else target.get("bundle_json", {})
    b_json["entry"] = [
        e for e in b_json.get("entry", [])
        if e.get("resource", {}).get("resourceType") not in ("Coverage", "ClaimResponse")
    ]
    comp = next((e.get("resource") for e in b_json.get("entry", []) if e.get("resource", {}).get("resourceType") == "Composition"), None)
    if comp:
        comp_secs = comp.setdefault("section", [])
        comp_secs[:] = [s for s in comp_secs if not any(k in s.get("title", "") for k in ("Insurance Coverage", "Adjudication"))]
    fhir_store.update_bundle_json(target["id"], json.dumps(b_json))
    return jsonify({"success": True, "message": "Adjudication reset. Patient is now awaiting policy upload."})


@app.delete("/api/patients/<patient_id>")
def delete_patient(patient_id: str):
    success = fhir_store.delete_patient(patient_id)
    if not success:
        return _error("Patient not found.", 404)
    return jsonify({"success": True, "message": "Patient record deleted successfully."})


# ── Bundle browsing ──

@app.get("/api/bundles")
def list_bundles():
    return jsonify(fhir_store.get_all_bundles())


@app.get("/api/bundles/<bundle_id>")
def get_bundle(bundle_id: str):
    bundle = fhir_store.get_bundle(bundle_id)
    if not bundle:
        return _error("Bundle not found.", 404)
    # Parse the stored JSON for validation
    bundle_json = json.loads(bundle["bundle_json"]) if bundle.get("bundle_json") else {}
    validation = validate_bundle(bundle_json)
    return jsonify({"bundle": bundle, "validation": validation})


@app.delete("/api/bundles/<bundle_id>")
def delete_bundle(bundle_id: str):
    success = fhir_store.delete_bundle(bundle_id)
    if not success:
        return _error("Bundle not found.", 404)
    return jsonify({"success": True, "message": "Report bundle deleted successfully."})


# ── Validation ──

@app.post("/api/validate")
def validate():
    data = request.get_json(silent=True)
    if not data:
        return _error("No JSON body provided.", 400)
    result = validate_bundle(data)
    return jsonify(result)


# ── Stats ──

@app.get("/api/stats")
def stats():
    return jsonify(fhir_store.get_stats())


# ══════════════════════════════════════════════════════════════
# Insurance Claim Company Portal — Parallel API Routes
# Uses the same processing pipeline but stores data in a
# separate SQLite database (insurance_data.db).
# ══════════════════════════════════════════════════════════════

@app.post("/api/insurance/convert")
def insurance_convert():
    return _do_insurance_convert(insurance_store)


@app.get("/api/insurance/patients")
def insurance_list_patients():
    return jsonify(_get_enriched_patients_list())


@app.get("/api/insurance/patients/<patient_id>")
def insurance_get_patient(patient_id: str):
    patient = insurance_store.get_patient(patient_id)
    if not patient:
        return _error("Patient not found.", 404)
    bundles = insurance_store.get_bundles_for_patient(patient_id)
    observations = insurance_store.get_observations_for_patient(patient_id)
    # Ensure each bundle has claim_summary (only for claim dossiers, not insurance plans)
    for b in bundles:
        try:
            b_json = json.loads(b["bundle_json"]) if isinstance(b.get("bundle_json"), str) else b.get("bundle_json", {})
            if "insurance_plan" not in b_json and "claim_summary" not in b_json:
                cs = summarize_claim(fhir_bundle=b_json)
                b_json["claim_summary"] = cs.to_dict()
                b["bundle_json"] = json.dumps(b_json)
                insurance_store.update_bundle_json(b["id"], b["bundle_json"])
        except Exception as exc:
            log.warning("Failed to auto-populate claim_summary for bundle %s: %s", b.get("id"), exc)
    return jsonify({"patient": patient, "bundles": bundles, "observations": observations})


@app.delete("/api/insurance/patients/<patient_id>")
def insurance_delete_patient(patient_id: str):
    success = insurance_store.delete_patient(patient_id)
    if not success:
        return _error("Patient not found.", 404)
    return jsonify({"success": True, "message": "Patient record deleted successfully."})


@app.get("/api/insurance/bundles")
def insurance_list_bundles():
    return jsonify(insurance_store.get_all_bundles())


@app.get("/api/insurance/bundles/<bundle_id>")
def insurance_get_bundle(bundle_id: str):
    bundle = insurance_store.get_bundle(bundle_id)
    if not bundle:
        return _error("Bundle not found.", 404)
    bundle_json = json.loads(bundle["bundle_json"]) if bundle.get("bundle_json") else {}
    validation = validate_bundle(bundle_json)
    return jsonify({
        "bundle": bundle,
        "validation": validation,
        "insurance_plan": bundle_json.get("insurance_plan"),
        "claim_summary": bundle_json.get("claim_summary"),
    })


@app.delete("/api/insurance/bundles/<bundle_id>")
def insurance_delete_bundle(bundle_id: str):
    success = insurance_store.delete_bundle(bundle_id)
    if not success:
        return _error("Bundle not found.", 404)
    return jsonify({"success": True, "message": "Report bundle deleted successfully."})


@app.post("/api/insurance/validate")
def insurance_validate():
    data = request.get_json(silent=True)
    if not data:
        return _error("No JSON body provided.", 400)
    result = validate_bundle(data)
    return jsonify(result)


@app.get("/api/insurance/stats")
def insurance_stats():
    return jsonify(insurance_store.get_stats())


@app.get("/api/insurance/health")
def insurance_health():
    return health()


def _do_insurance_convert(store: FhirStore):
    """Insurance-specific conversion: extracts InsurancePlan details & benefits, builds FHIR bundle."""
    if "file" not in request.files:
        return _error("No file provided in request.", 400)
    uploaded = request.files["file"]
    if not uploaded or not uploaded.filename:
        return _error("Empty filename provided.", 400)

    filename = uploaded.filename
    file_bytes = uploaded.read()
    if not file_bytes:
        return _error("Uploaded file is empty.", 400)
    uploaded.seek(0)

    # For ZIP archives, use existing archive pipeline
    if filename.lower().endswith(".zip"):
        data, status, gemini_extraction = _do_convert(store)
        if status == 200 and "error" not in data:
            try:
                fhir_bundle = data.get("raw")
                archive_info = data.get("archive_info")
                claim_summary = summarize_claim(
                    gemini_extraction=gemini_extraction,
                    fhir_bundle=fhir_bundle if isinstance(fhir_bundle, dict) else None,
                    archive_info=archive_info,
                )
                cs_dict = claim_summary.to_dict()
                data["claim_summary"] = cs_dict
            except Exception as exc:
                log.warning("Claim summarization failed (non-fatal): %s", exc)
        return jsonify(data), status

    # For PDF or image documents on insurance side:
    mime_type = _infer_mime_type(filename)
    start = time.perf_counter()
    try:
        plan_details, fhir_bundle = extract_insurance_plan(file_bytes, mime_type, filename)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)

        # Store bundle in insurance SQLite store
        bid = store.store_bundle(
            fhir_bundle,
            source_filename=filename,
            document_type="INSURANCE_PLAN",
        )
        plan_details.bundle_id = bid
        plan_dict = plan_details.to_dict()

        # Embed insurance_plan in the stored bundle json
        fhir_bundle["insurance_plan"] = plan_dict
        store.update_bundle_json(bid, json.dumps(fhir_bundle))

        # Validate bundle
        validation = validate_bundle(fhir_bundle)

        # Build summary
        summary = {
            "bundle": {
                "found": True,
                "type": "document",
                "resource_count": len(fhir_bundle.get("entry", [])),
                "profiles": [
                    p for e in fhir_bundle.get("entry", [])
                    for p in e.get("resource", {}).get("meta", {}).get("profile", [])
                ],
            },
            "patient": {
                "name": plan_details.plan_name,
                "gender": "unknown",
                "birthDate": "Not available",
                "address": "Not available",
                "aadhaar": "Not available",
                "abha": "Not available",
                "phone": "Not available",
                "guardian": "Not available",
                "hospital_id": "Not available",
                "mrn": plan_details.uin,
            },
            "observations": [],
        }

        # Also attempt claim summary narrative
        try:
            cs = summarize_claim(fhir_bundle=fhir_bundle)
            claim_summary_dict = cs.to_dict()
        except Exception:
            claim_summary_dict = None

        return jsonify({
            "duration_ms": duration_ms,
            "summary": summary,
            "raw": fhir_bundle,
            "validation": validation,
            "stored_bundle_ids": [bid],
            "insurance_plan": plan_dict,
            "claim_summary": claim_summary_dict,
        }), 200

    except Exception as exc:
        log.warning("Insurance plan extraction failed, falling back to standard convert: %s", exc)
        uploaded.seek(0)
        data, status, gemini_extraction = _do_convert(store)
        return jsonify(data), status


def _error(
    message: str,
    status: int,
    toolkit_status: int | None = None,
    duration_ms: float | None = None,
):
  payload = _error_dict(message, toolkit_status, duration_ms)
  return jsonify(payload), status


def _error_dict(
    message: str,
    toolkit_status: int | None = None,
    duration_ms: float | None = None,
) -> dict[str, Any]:
  """Returns an error payload dict without jsonify (for _do_convert internal use)."""
  payload: dict[str, Any] = {"error": message}
  if toolkit_status is not None:
    payload["toolkit_status"] = toolkit_status
  if duration_ms is not None:
    payload["duration_ms"] = duration_ms
  return payload


def _safe_toolkit_error(response: requests.Response) -> str:
  try:
    data = response.json()
    if isinstance(data, dict):
      return str(data.get("error") or data.get("message") or data)
  except ValueError:
    pass
  return response.text[:1000] if response.text else "No response body."


def _infer_mime_type(filename: str) -> str:
  lower = filename.lower()
  if lower.endswith(".pdf"):
    return "application/pdf"
  if lower.endswith((".jpg", ".jpeg")):
    return "image/jpeg"
  if lower.endswith(".png"):
    return "image/png"
  if lower.endswith(".webp"):
    return "image/webp"
  if lower.endswith(".bmp"):
    return "image/bmp"
  if lower.endswith((".tiff", ".tif")):
    return "image/tiff"
  if lower.endswith((".doc", ".docx")):
    return "application/msword"
  if lower.endswith(".zip"):
    return "application/zip"
  return ""


if __name__ == "__main__":
  logging.basicConfig(
      level=logging.INFO,
      format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
      datefmt="%H:%M:%S",
  )
  app.config["TEMPLATES_AUTO_RELOAD"] = True
  app.jinja_env.auto_reload = True
  app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100 MB max upload
  app.run(host="127.0.0.1", port=int(os.environ.get("DEMO_PORT", "5000")))

