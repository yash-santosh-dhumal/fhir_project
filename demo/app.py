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
insurance_store = FhirStore(db_path=_DEMO_DIR / "insurance_data.db")
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


def _do_convert(store: FhirStore):
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

    # 1. Identify candidate lab reports using comprehensive clinical keywords
    lab_keywords = {
        "lab", "pathology", "blood", "biochemistry", "cbc", "cbp", "serum",
        "test", "urine", "diagnostic", "culture", "profile", "lipid", "lft",
        "rft", "kft", "dtrs", "bone", "marrow", "hpe", "biopsy", "colonoscopy",
        "investigation", "report",
    }
    candidate_lab_docs = [
        doc for doc in analysis.documents
        if getattr(doc, "is_clinical_text_document", True)
        and (
            any(k in doc.filename.lower() for k in lab_keywords)
            or any(k in doc.folder_path.lower() for k in lab_keywords)
        )
    ]

    def _call_toolkit_api(doc_bytes: bytes, doc_mime: str, retry: int = 0) -> dict:
      resp = requests.post(
          f"{TOOLKIT_URL}/document_to_fhir",
          data=doc_bytes,
          headers={"Content-Type": doc_mime},
          timeout=REQUEST_TIMEOUT_SECONDS,
      )
      if not resp.ok:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
      pl = resp.json()
      std_docs = pl.get("standardized_medical_documents") or []
      if not std_docs and retry < 1:
        time.sleep(1.5)
        return _call_toolkit_api(doc_bytes, doc_mime, retry + 1)
      return pl

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
      gemini_future = pipeline_executor.submit(process_archive_documents_with_gemini, analysis.documents)
      toolkit_future = pipeline_executor.submit(_run_toolkit_for_docs, candidate_lab_docs)

      gemini_extraction = gemini_future.result()
      extracted_bundles, source_names = toolkit_future.result()

    # Safety check: if Gemini OCR identified any additional lab document that wasn't in candidate_lab_docs,
    # process only those missed documents with toolkit API.
    already_processed_paths = {d.relative_path for d in candidate_lab_docs}
    additional_candidates = []
    for doc in analysis.documents:
      if doc.relative_path not in already_processed_paths and gemini_extraction.documents:
        for edoc in gemini_extraction.documents:
          if (edoc.filename == doc.filename or edoc.relative_path == doc.relative_path):
            if any(k in edoc.document_type.lower() for k in lab_keywords):
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
      gemini_extraction = process_archive_documents_with_gemini([single_doc])
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


# ── Patient records ──

@app.get("/api/patients")
def list_patients():
    return jsonify(fhir_store.get_all_patients())


@app.get("/api/patients/<patient_id>")
def get_patient(patient_id: str):
    patient = fhir_store.get_patient(patient_id)
    if not patient:
        return _error("Patient not found.", 404)
    bundles = fhir_store.get_bundles_for_patient(patient_id)
    observations = fhir_store.get_observations_for_patient(patient_id)
    # Ensure any bundle with bills has Coverage and ClaimResponse
    for b in bundles:
        try:
            b_json = json.loads(b["bundle_json"]) if isinstance(b.get("bundle_json"), str) else b.get("bundle_json", {})
            entries = b_json.get("entry", [])
            resources = [e.get("resource", {}) for e in entries if e.get("resource")]
            claims = [r for r in resources if r.get("resourceType") == "Claim"]
            cov = next((r for r in resources if r.get("resourceType") == "Coverage"), None)
            cr = next((r for r in resources if r.get("resourceType") == "ClaimResponse"), None)

            needs_enrich = False
            if claims and (not cov or not cr):
                needs_enrich = True
            elif cr:
                tot_items = cr.get("total", [])
                benefit = next((t.get("amount", {}).get("value") for t in tot_items if "benefit" in [c.get("code") for c in t.get("category", {}).get("coding", [])]), None)
                if benefit == 42962.0:
                    needs_enrich = True
            if len(claims) > 1 and any("demo bill" in (s.get("valueString") or "").lower() or "hospital_bill" in (s.get("valueString") or "").lower() for c in claims for s in c.get("supportingInfo", [])):
                needs_enrich = True

            if needs_enrich:
                _ensure_bundle_insurance(b_json, claims, resources, fhir_store, b["id"])
                b["bundle_json"] = json.dumps(b_json)
        except Exception as exc:
            log.warning("Insurance auto-enrich failed for bundle %s: %s", b.get("id"), exc)
    return jsonify({"patient": patient, "bundles": bundles, "observations": observations})


def _ensure_bundle_insurance(
    b_json: dict[str, Any],
    claims: list[dict[str, Any]],
    resources: list[dict[str, Any]],
    store: FhirStore,
    bundle_id: str,
) -> None:
    """Enriches a bundle with ABDM Coverage and ClaimResponse matching policy document."""
    from demo.fhir_unifier import (
        _build_coverage_resource,
        _build_claim_response_resource,
        _adjudicate_bill_against_policy,
    )
    from demo.gemini_ocr import ExtractedInsurancePolicy, ExtractedBillingData, ExtractedBillingItem

    # Try to reconstruct the insurance policy from existing Coverage/ClaimResponse in the bundle
    existing_cov = next((r for r in resources if r.get("resourceType") == "Coverage"), None)
    policy = None
    if existing_cov:
        # Extract policy details from the Coverage resource
        payor_name = ""
        payors = existing_cov.get("payor", [])
        if payors and isinstance(payors[0], dict):
            payor_name = payors[0].get("display", "")
        
        cov_class = existing_cov.get("class", [])
        policy_num = ""
        copay_pct = 0.0
        covered_cats = []
        terms = ""
        sum_insured = 0.0
        for cls_item in cov_class:
            code = cls_item.get("type", {}).get("coding", [{}])[0].get("code", "")
            val = cls_item.get("value", "")
            if code == "plan":
                policy_num = val
            elif code == "copay_percentage":
                try:
                    copay_pct = float(val)
                except (ValueError, TypeError):
                    pass
            elif code == "sum_insured":
                try:
                    sum_insured = float(val)
                except (ValueError, TypeError):
                    pass
            elif code == "covered_categories":
                covered_cats = [c.strip() for c in val.split(",") if c.strip()]
            elif code == "terms_and_rules":
                terms = val

        # Also try to get coverage_type from the type field
        cov_type_coding = existing_cov.get("type", {}).get("coding", [{}])
        coverage_type = cov_type_coding[0].get("display", "") if cov_type_coding else ""

        if policy_num or copay_pct > 0 or terms:
            policy = ExtractedInsurancePolicy(
                policy_number=policy_num,
                scheme_or_insurer=payor_name or "Insurance Policy",
                copayment_percentage=copay_pct,
                coverage_type=coverage_type,
                annual_sum_insured=sum_insured,
                covered_categories=covered_cats,
                terms_and_rules=terms,
                source_document="Extracted from FHIR Coverage resource",
            )

    # Fallback: use default policy if no policy data found in bundle
    if not policy:
        policy = ExtractedInsurancePolicy(
            policy_number="WAP138200500121/04",
            scheme_or_insurer="Dr. YSR Aarogyasri / AB-PMJAY",
            health_card_number="10116903095-04",
            claim_or_preauth_number="APTRUST/KNL/2025/1/13510681/07",
            policy_status="Active",
            annual_sum_insured=2500000.0,
            copayment_liability=0.0,
            copayment_percentage=20.0,
            coverage_type="Co-Pay Health Assurance Scheme (80:20 Risk-Sharing)",
            pre_auth_approved_amount=0.0,
            patient_out_of_pocket=0.0,
            covered_categories=[
                "In-Patient Hospitalization",
                "ICU & Critical Care",
                "Medical & Surgical Oncology",
                "Day Care Treatment",
                "Pre and Post-Hospitalization",
                "Pharmacy & Medications",
                "Room & Board",
                "Professional Fees",
                "Investigations",
                "Surgical / Procedures",
            ],
            terms_and_rules="Claims adjudicated under 80% Insurer Underwritten and 20% Beneficiary Co-Payment Schedule.",
            source_document="demo claims/Insurance_Policy_Document_AP12363098.pdf",
        )
    p_res = next((r for r in resources if r.get("resourceType") == "Patient"), None)
    pref = f"urn:uuid:{p_res['id']}" if p_res else "urn:uuid:patient"
    org_res = next((r for r in resources if r.get("resourceType") == "Organization"), None)
    oref = f"urn:uuid:{org_res['id']}" if org_res else "urn:uuid:org"

    # Filter claims to keep ONLY the demo bill as the final bill
    demo_claims = [
        c for c in claims
        if any("demo bill" in (s.get("valueString") or "").lower() or "hospital_bill" in (s.get("valueString") or "").lower() for s in c.get("supportingInfo", []))
    ]
    target_claim = demo_claims[0] if demo_claims else (claims[0] if claims else None)
    if not target_claim:
        return

    # If multiple claims exist and demo claim was found, prune other claims
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

    # Remove existing Coverage and ClaimResponse so they are cleanly recreated
    b_json["entry"] = [
        e for e in b_json.get("entry", [])
        if e.get("resource", {}).get("resourceType") not in ("Coverage", "ClaimResponse")
    ]

    cov_res = _build_coverage_resource(policy, pref, oref)
    bill_total = float(target_claim.get("total", {}).get("value", 42962.0) or 42962.0)

    # Extract bill items from the Claim resource for category-based adjudication
    bill_items = []
    for item in target_claim.get("item", []):
        cat = item.get("category", {}).get("coding", [{}])[0].get("display", "General")
        desc = item.get("productOrService", {}).get("text", "")
        amt = item.get("unitPrice", {}).get("value", 0.0)
        if amt > 0:
            bill_items.append(ExtractedBillingItem(
                description=desc,
                amount=float(amt),
                category=cat,
            ))

    bill_data = [
        ExtractedBillingData(
            bill_number=target_claim.get("id", "BILL"),
            total_amount=bill_total,
            source_document="demo bill/Hospital_Bill_AP12363098.pdf",
            items=bill_items,
        )
    ]
    adj = _adjudicate_bill_against_policy(bill_data, policy)
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
    store.update_bundle_json(bundle_id, json.dumps(b_json))


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
    return jsonify(insurance_store.get_all_patients())


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

