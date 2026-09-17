"""Small Flask demo for Google Health Medical Data Toolkit."""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
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
    log.info("Processing all %d documents from ZIP '%s' with Gemini high-level OCR...",
             analysis.document_count, analysis.archive_name)

    # 1. High-level OCR and structured information extraction across ALL documents
    gemini_extraction = process_archive_documents_with_gemini(analysis.documents)

    # 2. Check candidate lab reports with toolkit API for LOINC bundles
    lab_keywords = {"lab", "pathology", "blood", "biochemistry", "cbc", "cbp", "serum", "test", "urine", "diagnostic", "culture", "profile", "lipid", "lft", "rft", "kft"}
    candidate_lab_docs = []
    for doc in analysis.documents:
      fname_lower = doc.filename.lower()
      is_candidate = any(k in fname_lower for k in lab_keywords)
      if not is_candidate and gemini_extraction.documents:
        for edoc in gemini_extraction.documents:
          if edoc.filename == doc.filename or edoc.relative_path == doc.relative_path:
            if any(k in edoc.document_type.lower() for k in lab_keywords):
              is_candidate = True
              break
      if is_candidate:
        candidate_lab_docs.append(doc)

    extracted_bundles: list[dict[str, Any]] = []
    source_names: list[str] = []

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

    if candidate_lab_docs:
      log.info("Checking %d candidate lab reports with toolkit API...", len(candidate_lab_docs))
      for doc in candidate_lab_docs:
        try:
          doc_bytes, doc_mime = _optimize_image_if_needed(doc.file_bytes, doc.mime_type)
          pl = _call_toolkit_api(doc_bytes, doc_mime)
          for sdoc in (pl.get("standardized_medical_documents") or []):
            if isinstance(sdoc, dict):
              fb = sdoc.get("fhir_bundle")
              if isinstance(fb, dict) and fb.get("resourceType") == "Bundle":
                extracted_bundles.append(fb)
                source_names.append(doc.relative_path)
        except Exception as exc:
          log.warning("Toolkit API skipped for candidate '%s': %s", doc.relative_path, exc)

    # 3. Unify all bundles and Gemini-extracted demographics & observations
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
    return jsonify({"patient": patient, "bundles": bundles, "observations": observations})


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

