"""Small Flask demo for Google Health Medical Data Toolkit."""

from __future__ import annotations

import json
import os
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
  from batch_processor import BatchProcessor
  from fhir_store import FhirStore
  from fhir_summary import summarize_payload
  from fhir_validator import validate_bundle
except ImportError:
  from demo.batch_processor import BatchProcessor
  from demo.fhir_store import FhirStore
  from demo.fhir_summary import summarize_payload
  from demo.fhir_validator import validate_bundle

try:
  from dotenv import load_dotenv

  load_dotenv(_REPO_ROOT / ".env", override=True)
except ImportError:
  pass

TOOLKIT_URL = os.environ.get("TOOLKIT_URL", "http://localhost:8088").rstrip("/")
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("TOOLKIT_TIMEOUT_SECONDS", "180"))
SUPPORTED_MIME_TYPES = {"application/pdf", "image/jpeg", "image/png"}

app = Flask(__name__)
fhir_store = FhirStore()
batch_processor = BatchProcessor(
    toolkit_url=TOOLKIT_URL,
    fhir_store=fhir_store,
    timeout_seconds=REQUEST_TIMEOUT_SECONDS,
)


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


@app.post("/api/convert")
def convert():
  uploaded = request.files.get("file")
  if uploaded is None or uploaded.filename == "":
    return _error("No file was uploaded.", 400)

  mime_type = uploaded.mimetype or _infer_mime_type(uploaded.filename)
  if mime_type not in SUPPORTED_MIME_TYPES:
    return _error("Supported formats are PDF, JPEG, and PNG.", 400)

  file_bytes = uploaded.read()
  if not file_bytes:
    return _error("The uploaded file is empty.", 400)

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
    return _error(
        f"Medical Data Toolkit is not reachable at {TOOLKIT_URL}. Ensure the backend service is running on port 8088.",
        503,
    )
  except requests.Timeout:
    return _error("Medical Data Toolkit timed out while processing the document.", 504)
  except requests.RequestException as exc:
    return _error(f"Toolkit request failed: {exc}", 502)

  duration_ms = round((time.perf_counter() - start) * 1000, 2)
  safe_error = _safe_toolkit_error(toolkit_response)
  if not toolkit_response.ok:
    if toolkit_response.status_code == 422:
      message = (
          "This document could not be processed as a supported "
          "laboratory/diagnostic report."
      )
      if safe_error:
        message = f"{message} Toolkit detail: {safe_error}"
      return _error(message, 422, toolkit_response.status_code, duration_ms)
    return _error(
        f"Toolkit API error HTTP {toolkit_response.status_code}: {safe_error}",
        502,
        toolkit_response.status_code,
        duration_ms,
    )

  try:
    payload: dict[str, Any] = toolkit_response.json()
  except ValueError:
    return _error(
        "The toolkit returned a response that could not be parsed as JSON.",
        502,
        toolkit_response.status_code,
        duration_ms,
    )

  summary = summarize_payload(payload)
  standardized_docs = payload.get("standardized_medical_documents", [])
  if not summary.get("bundle", {}).get("found"):
    if not standardized_docs:
      return _error(
          "The toolkit did not classify this upload as a supported laboratory "
          "report. Try again, or upload a clearer typed lab report image/PDF. "
          "If this repeats, the configured Gemini classifier may be overloaded.",
          422,
          toolkit_response.status_code,
          duration_ms,
      )
    return _error(
        "The toolkit processed the upload but did not return a FHIR Bundle.",
        422,
        toolkit_response.status_code,
        duration_ms,
    )

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
    bid = fhir_store.store_bundle(fb, source_filename=src_name, document_type=doc_type)
    stored_bundle_ids.append(bid)

  # Run validation
  validation = validate_bundle(payload)

  return jsonify({
      "duration_ms": duration_ms,
      "summary": summary,
      "raw": payload,
      "validation": validation,
      "stored_bundle_ids": stored_bundle_ids,
  })


# ── Batch upload ──

_batch_jobs: dict[str, dict] = {}


def _process_single_file_for_batch(batch_id: str, filename: str, file_bytes: bytes, mime_type: str):
    """Process a single file as part of a batch (runs in a thread)."""
    batch_processor.process_document(
        filename=filename,
        file_bytes=file_bytes,
        mime_type=mime_type,
        batch_id=batch_id,
    )


@app.post("/api/batch-upload")
def batch_upload():
    files = request.files.getlist("files")
    if not files:
        return _error("No files uploaded.", 400)

    batch_id = str(uuid_mod.uuid4())
    _batch_jobs[batch_id] = {"total": len(files), "started": time.time()}

    for f in files:
        mime = f.mimetype or _infer_mime_type(f.filename or "")
        if mime not in SUPPORTED_MIME_TYPES:
            fhir_store.log_processing(batch_id, f.filename or "unknown", "error", f"Unsupported type: {mime}")
            continue
        file_bytes = f.read()
        if not file_bytes:
            fhir_store.log_processing(batch_id, f.filename or "unknown", "error", "Empty file")
            continue
        # Launch processing in a thread
        t = threading.Thread(
            target=_process_single_file_for_batch,
            args=(batch_id, f.filename or "unknown", file_bytes, mime),
            daemon=True,
        )
        t.start()

    return jsonify({"batch_id": batch_id, "file_count": len(files)})


@app.get("/api/batch-status/<batch_id>")
def batch_status(batch_id: str):
    logs = fhir_store.get_batch_status(batch_id)
    if not logs:
        return _error("Batch not found.", 404)
    total = len(logs)
    done = sum(1 for l in logs if l["status"] in ("success", "error"))
    return jsonify({"batch_id": batch_id, "total": total, "done": done, "files": logs})


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


def _error(
    message: str,
    status: int,
    toolkit_status: int | None = None,
    duration_ms: float | None = None,
):
  payload: dict[str, Any] = {"error": message}
  if toolkit_status is not None:
    payload["toolkit_status"] = toolkit_status
  if duration_ms is not None:
    payload["duration_ms"] = duration_ms
  return jsonify(payload), status


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
  return ""


if __name__ == "__main__":
  app.config["TEMPLATES_AUTO_RELOAD"] = True
  app.jinja_env.auto_reload = True
  app.run(host="127.0.0.1", port=int(os.environ.get("DEMO_PORT", "5000")))

