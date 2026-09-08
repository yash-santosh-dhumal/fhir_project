"""Small Flask demo for Google Health Medical Data Toolkit."""

from __future__ import annotations

import os
import time
from typing import Any

import requests
from flask import Flask, jsonify, render_template, request

from fhir_summary import summarize_payload

try:
  from dotenv import load_dotenv

  load_dotenv()
except ImportError:
  pass

TOOLKIT_URL = os.environ.get("TOOLKIT_URL", "http://localhost:8080").rstrip("/")
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("TOOLKIT_TIMEOUT_SECONDS", "180"))
SUPPORTED_MIME_TYPES = {"application/pdf", "image/jpeg", "image/png"}

app = Flask(__name__)


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
        "Medical Data Toolkit is not running. Start the toolkit service on port 8080.",
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

  return jsonify({
      "duration_ms": duration_ms,
      "summary": summary,
      "raw": payload,
  })


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
  app.run(host="127.0.0.1", port=int(os.environ.get("DEMO_PORT", "5000")))
