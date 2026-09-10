"""Multi-file batch processor for hospital MIS medical documents.

Handles concurrent or sequential processing of large and multiple PDFs/images:
- Sends documents to Medical Data Toolkit /document_to_fhir
- Validates each resulting FHIR bundle against ABDM R4 requirements
- Stores and indexes resources (Bundles, Patients, Observations) in FhirStore
- Resilient per-document error handling (one failure doesn't stop the batch)
- Real-time progress callback support for UI / polling / CLI
"""

from __future__ import annotations

import concurrent.futures
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import requests

try:
    from fhir_store import FhirStore
    from fhir_validator import validate_bundle
except ImportError:
    from demo.fhir_store import FhirStore
    from demo.fhir_validator import validate_bundle


SUPPORTED_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


def infer_mime_type(filename: str) -> str:
    lower = filename.lower()
    for ext, mime in SUPPORTED_MIME_TYPES.items():
        if lower.endswith(ext):
            return mime
    return "application/octet-stream"


def _optimize_image_if_needed(file_bytes: bytes, mime_type: str) -> tuple[bytes, str]:
    """Optimizes image resolution and compression for faster processing."""
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


@dataclass
class BatchItemResult:
    filename: str
    status: str  # "success", "error", "skipped"
    duration_ms: float = 0.0
    bundle_id: str | None = None
    patient_id: str | None = None
    error_message: str | None = None
    observation_count: int = 0
    compliance_score: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "bundle_id": self.bundle_id,
            "patient_id": self.patient_id,
            "error_message": self.error_message,
            "observation_count": self.observation_count,
            "compliance_score": self.compliance_score,
        }


@dataclass
class BatchSummary:
    batch_id: str
    total: int
    successful: int = 0
    failed: int = 0
    total_duration_ms: float = 0.0
    results: list[BatchItemResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "total": self.total,
            "successful": self.successful,
            "failed": self.failed,
            "total_duration_ms": self.total_duration_ms,
            "results": [r.to_dict() for r in self.results],
        }


class BatchProcessor:
    """Manages batch ingestion of clinical documents into FHIR storage."""

    def __init__(
        self,
        toolkit_url: str | None = None,
        fhir_store: FhirStore | None = None,
        timeout_seconds: float = 180.0,
        max_workers: int = 3,
    ):
        self.toolkit_url = (toolkit_url or os.environ.get("TOOLKIT_URL", "http://localhost:8080")).rstrip("/")
        self.fhir_store = fhir_store or FhirStore()
        self.timeout_seconds = timeout_seconds
        self.max_workers = max_workers

    def process_document(
        self,
        filename: str,
        file_bytes: bytes,
        mime_type: str | None = None,
        batch_id: str | None = None,
    ) -> BatchItemResult:
        """Process a single document through toolkit, validate, and store."""
        actual_mime = mime_type or infer_mime_type(filename)
        bid = batch_id or str(uuid.uuid4())
        log_id = self.fhir_store.log_processing(bid, filename, status="processing")
        start = time.perf_counter()

        if not file_bytes:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            msg = "Empty file content"
            self.fhir_store.update_processing_log(log_id, "error", error_message=msg, duration_ms=duration_ms)
            return BatchItemResult(filename=filename, status="error", duration_ms=duration_ms, error_message=msg)

        file_bytes, actual_mime = _optimize_image_if_needed(file_bytes, actual_mime)

        try:
            resp = requests.post(
                f"{self.toolkit_url}/document_to_fhir",
                data=file_bytes,
                headers={"Content-Type": actual_mime},
                timeout=self.timeout_seconds,
            )
            duration_ms = round((time.perf_counter() - start) * 1000, 2)

            if not resp.ok:
                err_text = f"Toolkit error HTTP {resp.status_code}"
                try:
                    err_json = resp.json()
                    detail = err_json.get("error") or err_json.get("message")
                    if detail:
                        err_text = f"{err_text}: {detail}"
                except Exception:
                    pass
                self.fhir_store.update_processing_log(log_id, "error", error_message=err_text, duration_ms=duration_ms)
                return BatchItemResult(filename=filename, status="error", duration_ms=duration_ms, error_message=err_text)

            payload = resp.json()
            docs = payload.get("standardized_medical_documents", []) or []
            if not docs:
                msg = "No standardized medical documents produced"
                self.fhir_store.update_processing_log(log_id, "error", error_message=msg, duration_ms=duration_ms)
                return BatchItemResult(filename=filename, status="error", duration_ms=duration_ms, error_message=msg)

            # Store the bundles
            primary_bundle_id = None
            primary_patient_id = None
            total_obs = 0
            val_score = None

            for doc in docs:
                fb = doc.get("fhir_bundle") if isinstance(doc, dict) else None
                if isinstance(fb, dict) and fb.get("resourceType") == "Bundle":
                    doc_type = doc.get("document_type", "LABORATORY_REPORT")
                    stored_id = self.fhir_store.store_bundle(
                        fb,
                        source_filename=filename,
                        document_type=doc_type,
                    )
                    if not primary_bundle_id:
                        primary_bundle_id = stored_id
                        # Validate primary bundle
                        val = validate_bundle(fb)
                        val_score = val.get("compliance_score")
                        # Find patient and obs count
                        for entry in fb.get("entry", []) or []:
                            r = entry.get("resource", {})
                            if r.get("resourceType") == "Patient" and not primary_patient_id:
                                primary_patient_id = r.get("id")
                            elif r.get("resourceType") == "Observation":
                                total_obs += 1

            self.fhir_store.update_processing_log(
                log_id,
                status="success",
                duration_ms=duration_ms,
                bundle_id=primary_bundle_id or "",
            )

            return BatchItemResult(
                filename=filename,
                status="success",
                duration_ms=duration_ms,
                bundle_id=primary_bundle_id,
                patient_id=primary_patient_id,
                observation_count=total_obs,
                compliance_score=val_score,
            )

        except Exception as exc:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            err_msg = str(exc)[:500]
            self.fhir_store.update_processing_log(log_id, "error", error_message=err_msg, duration_ms=duration_ms)
            return BatchItemResult(
                filename=filename,
                status="error",
                duration_ms=duration_ms,
                error_message=err_msg,
            )

    def process_files(
        self,
        files: list[tuple[str, bytes, str]],  # (filename, bytes, mime_type)
        batch_id: str | None = None,
        progress_callback: Callable[[int, int, BatchItemResult], None] | None = None,
        concurrent_execution: bool = True,
    ) -> BatchSummary:
        """Process a list of files as a batch."""
        bid = batch_id or str(uuid.uuid4())
        total = len(files)
        summary = BatchSummary(batch_id=bid, total=total)
        start_batch = time.perf_counter()

        def _worker(item: tuple[str, bytes, str], idx: int) -> tuple[int, BatchItemResult]:
            fname, fbytes, fmime = item
            res = self.process_document(fname, fbytes, fmime, batch_id=bid)
            return idx, res

        if concurrent_execution and total > 1 and self.max_workers > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = [executor.submit(_worker, f, i) for i, f in enumerate(files)]
                completed = 0
                for fut in concurrent.futures.as_completed(futures):
                    idx, result = fut.result()
                    summary.results.append(result)
                    completed += 1
                    if result.status == "success":
                        summary.successful += 1
                    else:
                        summary.failed += 1
                    if progress_callback:
                        progress_callback(completed, total, result)
        else:
            for i, f in enumerate(files):
                _, result = _worker(f, i)
                summary.results.append(result)
                if result.status == "success":
                    summary.successful += 1
                else:
                    summary.failed += 1
                if progress_callback:
                    progress_callback(i + 1, total, result)

        summary.total_duration_ms = round((time.perf_counter() - start_batch) * 1000, 2)
        return summary

    def process_directory(
        self,
        dir_path: str | Path,
        batch_id: str | None = None,
        progress_callback: Callable[[int, int, BatchItemResult], None] | None = None,
    ) -> BatchSummary:
        """Scan a directory for supported medical files and process them as a batch."""
        path = Path(dir_path)
        if not path.is_dir():
            raise NotADirectoryError(f"Directory not found: {dir_path}")

        files_to_process: list[tuple[str, bytes, str]] = []
        for p in sorted(path.iterdir()):
            if p.is_file() and p.suffix.lower() in SUPPORTED_MIME_TYPES:
                mime = SUPPORTED_MIME_TYPES[p.suffix.lower()]
                files_to_process.append((p.name, p.read_bytes(), mime))

        return self.process_files(
            files_to_process,
            batch_id=batch_id,
            progress_callback=progress_callback,
        )
