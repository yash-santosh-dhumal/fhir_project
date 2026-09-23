"""Intelligent OCR and medical document understanding using Gemini Vision API.

Performs high-level OCR and structured information extraction across ALL documents
found in patient archives:
- Patient demographics (Full legal name, DOB/age, gender, Aadhaar UID, ABHA, address, phone)
- Document classification and clinical summaries for every file (Aadhaar, Discharge Summary,
  Lab Reports, Consent Forms, Bills, Case Sheets, Radiographs, etc.)
- Clinical findings, final diagnoses, medications, treating doctor, hospital name
- Diagnostic / laboratory test observations with analyte names, values, units, and ranges
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import PIL.Image

try:
    from dotenv import load_dotenv
    _ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(_ENV_PATH, override=False)
except ImportError:
    pass

log = logging.getLogger(__name__)

GEMINI_MODEL = os.environ.get("GEMINI_OCR_MODEL", "gemini-3.1-flash-lite")
MAX_BATCH_SIZE = 6
MAX_WORKERS = 3
MAX_RETRIES = 3


@dataclass
class ExtractedDemographics:
    """Consolidated patient demographics extracted across all documents."""
    name: str | None = None
    birth_date: str | None = None  # YYYY-MM-DD or YYYY
    gender: str | None = None      # male, female, other, unknown
    aadhaar_number: str | None = None
    health_card_number: str | None = None
    patient_id: str | None = None  # MRN / IP / OP number
    address: str | None = None
    city: str | None = None
    district: str | None = None
    state: str | None = None
    pincode: str | None = None
    guardian_name: str | None = None
    phone: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExtractedDocumentInfo:
    """Document classification and clinical summary for a single document."""
    filename: str
    relative_path: str
    document_type: str
    summary: str
    clinical_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExtractedObservation:
    """Extracted laboratory or diagnostic test observation."""
    test_name: str
    value: str
    unit: str = ""
    reference_range: str = ""
    interpretation: str = ""
    source_document: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExtractedBillingItem:
    """A single line-item from a hospital bill or invoice."""
    description: str
    quantity: int = 1
    unit_price: float = 0.0
    amount: float = 0.0
    category: str = "General"  # e.g. Room & Board, Investigations, Pharmacy, etc.

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExtractedBillingData:
    """Complete billing/invoice data extracted from a hospital bill document."""
    bill_number: str = ""
    bill_date: str = ""
    total_amount: float = 0.0
    payment_mode: str = ""
    items: list[ExtractedBillingItem] = field(default_factory=list)
    source_document: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["items"] = [item.to_dict() for item in self.items]
        return d


@dataclass
class ArchiveExtractionResult:
    """Complete aggregated extraction from all archive documents."""
    demographics: ExtractedDemographics = field(default_factory=ExtractedDemographics)
    documents: list[ExtractedDocumentInfo] = field(default_factory=list)
    observations: list[ExtractedObservation] = field(default_factory=list)
    billing_data: list[ExtractedBillingData] = field(default_factory=list)
    clinical_notes: list[str] = field(default_factory=list)
    hospital_name: str | None = None
    treating_physician: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "demographics": self.demographics.to_dict(),
            "documents": [d.to_dict() for d in self.documents],
            "observations": [o.to_dict() for o in self.observations],
            "billing_data": [b.to_dict() for b in self.billing_data],
            "clinical_notes": self.clinical_notes,
            "hospital_name": self.hospital_name,
            "treating_physician": self.treating_physician,
        }


def _get_gemini_client():
    """Initializes the google.genai Client using configured API key."""
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Neither GOOGLE_API_KEY nor GEMINI_API_KEY is set in environment or .env"
        )
    from google import genai
    return genai.Client(api_key=api_key)


def _prepare_document_part(file_bytes: bytes, mime_type: str, filename: str) -> Any:
    """Prepares an API Part for an image, PDF, or text/docx document."""
    from google.genai import types

    # 1. Image optimization
    if mime_type.startswith("image/"):
        try:
            with PIL.Image.open(io.BytesIO(file_bytes)) as img:
                w, h = img.size
                max_dim = max(w, h)
                if max_dim > 1600 or len(file_bytes) > 400 * 1024 or img.format != "JPEG":
                    if max_dim > 1600:
                        scale = 1600.0 / max_dim
                        img = img.resize((int(w * scale), int(h * scale)), PIL.Image.Resampling.LANCZOS)
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=85, optimize=True)
                    file_bytes = buf.getvalue()
                    mime_type = "image/jpeg"
                return types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
        except Exception as exc:
            log.debug("Image invalid or mock in %s: %s", filename, exc)
            return f"[Simulated Image Document {filename}]"

    # 2. PDF documents
    if mime_type == "application/pdf":
        if len(file_bytes) < 100:
            return f"[Simulated PDF Document {filename}]"
        return types.Part.from_bytes(data=file_bytes, mime_type="application/pdf")

    # 3. Word (.docx) documents - extract XML text directly
    if filename.lower().endswith(".docx"):
        try:
            import zipfile
            with zipfile.ZipFile(io.BytesIO(file_bytes)) as dz:
                xml_content = dz.read("word/document.xml").decode("utf-8", errors="ignore")
                text = " ".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", xml_content))
                return f"[DOCX Content for {filename}]: {text[:8000]}"
        except Exception as exc:
            log.warning("Could not extract docx text for %s: %s", filename, exc)
            return f"[DOCX File {filename}]"

    # Fallback to text representation
    try:
        text = file_bytes.decode("utf-8", errors="ignore")[:5000]
        return f"[Document Text for {filename}]: {text}"
    except Exception:
        return f"[Binary Document {filename}]"


def _extract_batch(
    client: Any,
    batch_docs: list[tuple[str, str, bytes, str]],  # (filename, rel_path, bytes, mime)
    batch_index: int,
) -> dict[str, Any]:
    """Sends a batch of documents to Gemini for high-level OCR and structured extraction."""
    from google.genai import types

    parts: list[Any] = []
    file_manifest: list[dict[str, str]] = []

    for idx, (fname, rel_path, fbytes, mime) in enumerate(batch_docs, 1):
        doc_header = f"=== Document {idx} [Filename: {fname}, Path: {rel_path}] ==="
        parts.append(doc_header)
        part = _prepare_document_part(fbytes, mime, fname)
        parts.append(part)
        file_manifest.append({"index": idx, "filename": fname, "relative_path": rel_path})

    prompt = f"""You are a specialized medical document understanding and OCR system.
These {len(batch_docs)} documents all belong to the SAME patient from a hospital MIS archive.
Perform thorough, high-level OCR across all documents (including non-medical identity cards like Aadhaar,
Discharge Summaries, Lab Reports, Consent Forms, Invoices, Case Sheets, etc.).

Carefully extract:
1. Patient Demographics (synthesize across all documents):
   - full legal name (e.g. from Aadhaar card front, discharge summary, or report header)
   - date of birth in strict YYYY-MM-DD format (if only year/age available, format accordingly)
   - gender ("male", "female", "other", or "unknown")
   - aadhaar_number (12 digits, e.g. "4644 5349 4506" or unspaced)
   - health_card_number (e.g. Dr. YSR Aarogyasri / ABHA / Insurance ID)
   - patient_id (Hospital MRN, IP number, OP number, or Registration ID)
   - full residential address (especially from Aadhaar back side or hospital admission records)
   - guardian_name (Father's, Husband's, or Caregiver's name, e.g. S/O Chinna Balanna)
   - phone number
2. Document Details for EACH document provided in this batch:
   - filename
   - relative_path
   - document_type (e.g. "Aadhaar Card", "Health Card", "Discharge Summary", "Complete Blood Picture",
     "Biochemistry Report", "Pre-Authorization Form", "Operation Note", "Bill / Invoice", "Clinical Photo")
   - summary: concise clinical and functional summary of the document
   - clinical_notes: any key diagnoses, procedures, advice, or impressions in this document
3. Laboratory / Diagnostic Test Observations (if any quantitative or qualitative test results are visible):
   - test_name: analyte/test name (e.g. Hemoglobin, PCV, Total Bilirubin, Blood Group, Serum Creatinine)
   - value: test value (e.g. "11.8", "35.0", "B+", "0.8")
   - unit: unit of measurement (e.g. "gms%", "vol %", "mg/dl")
   - reference_range: normal reference interval if stated (e.g. "13.0 - 17.0")
   - interpretation: "normal", "high", "low", or "abnormal"
   - source_document: filename where this test was found
4. Hospital / Clinic Name & Treating Physician (if visible anywhere in these documents).

OUTPUT REQUIREMENT: Return ONLY a valid JSON object matching this schema:
{{
  "patient": {{
    "name": "string or null",
    "birth_date": "YYYY-MM-DD or null",
    "gender": "male|female|other|unknown",
    "aadhaar_number": "string or null",
    "health_card_number": "string or null",
    "patient_id": "string or null",
    "address": "string or null",
    "city": "string or null",
    "district": "string or null",
    "state": "string or null",
    "pincode": "string or null",
    "guardian_name": "string or null",
    "phone": "string or null"
  }},
  "documents": [
    {{
      "filename": "string",
      "relative_path": "string",
      "document_type": "string",
      "summary": "string",
      "clinical_notes": ["string"]
    }}
  ],
  "observations": [
    {{
      "test_name": "string",
      "value": "string",
      "unit": "string",
      "reference_range": "string",
      "interpretation": "string",
      "source_document": "string"
    }}
  ],
  "billing": [
    {{
      "bill_number": "string",
      "bill_date": "string (DD/MM/YYYY or as printed)",
      "total_amount": 0.0,
      "payment_mode": "string (e.g. Cash, Insurance, Government Scheme)",
      "items": [
        {{
          "description": "string (line-item description e.g. General Ward Charges, CBC, Imatinib 400mg)",
          "quantity": 1,
          "unit_price": 0.0,
          "amount": 0.0,
          "category": "string (Room & Board, Professional Fees, Investigations, Surgical / Procedures, Pharmacy / Medications, or General)"
        }}
      ],
      "source_document": "string (filename)"
    }}
  ],
  "clinical_notes": ["string"],
  "hospital_name": "string or null",
  "treating_physician": "string or null"
}}
"""
    parts.append(prompt)

    # Call with exponential backoff on rate limits
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=parts,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )
            raw_text = resp.text.strip()
            # Clean possible markdown wrapping
            if raw_text.startswith("```"):
                raw_text = re.sub(r"^```json\s*", "", raw_text)
                raw_text = re.sub(r"```$", "", raw_text).strip()
            return json.loads(raw_text)
        except Exception as exc:
            last_err = exc
            log.warning("Batch %d attempt %d failed: %s", batch_index, attempt + 1, exc)
            if attempt < MAX_RETRIES - 1:
                sleep_time = (attempt + 1) * 2.0
                time.sleep(sleep_time)

    log.error("Batch %d failed completely after %d attempts: %s", batch_index, MAX_RETRIES, last_err)
    return {}


def _normalize_birth_date(dob_str: str | None) -> str | None:
    """Normalizes various DOB formats (e.g., DD/MM/YYYY, DD-MM-YYYY, YYYY) into YYYY-MM-DD."""
    if not dob_str:
        return None
    dob = dob_str.strip()
    # Check YYYY-MM-DD
    if re.match(r"^\d{4}-\d{2}-\d{2}$", dob):
        return dob
    # Check DD/MM/YYYY or DD-MM-YYYY
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", dob)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), m.group(3)
        return f"{year}-{month:02d}-{day:02d}"
    # Check YYYY
    if re.match(r"^\d{4}$", dob):
        return f"{dob}-01-01"
    return dob


def _merge_batch_results(
    results: list[dict[str, Any]],
    all_doc_paths: list[str],
) -> ArchiveExtractionResult:
    """Merges structured data from multiple Gemini batches into a unified result."""
    merged = ArchiveExtractionResult()

    # Demographics fields to prioritize
    best_demo = merged.demographics

    seen_tests: set[tuple[str, str]] = set()

    for r in results:
        if not r or not isinstance(r, dict):
            continue

        # 1. Demographics
        pat = r.get("patient") or {}
        if isinstance(pat, dict):
            # Name: prefer longer/more complete names
            pname = pat.get("name")
            if pname and (not best_demo.name or len(pname) > len(best_demo.name)):
                best_demo.name = pname.strip()

            # DOB: prefer standard normalized date
            pdob = _normalize_birth_date(pat.get("birth_date") or pat.get("date_of_birth"))
            if pdob and (not best_demo.birth_date or len(pdob) > len(best_demo.birth_date)):
                best_demo.birth_date = pdob

            # Gender
            pgender = pat.get("gender")
            if pgender and pgender.lower() in ("male", "female", "other"):
                best_demo.gender = pgender.lower()

            # Aadhaar
            paadhaar = pat.get("aadhaar_number")
            if paadhaar and (not best_demo.aadhaar_number or len(paadhaar) > len(best_demo.aadhaar_number)):
                best_demo.aadhaar_number = paadhaar.strip()

            # Health card
            phc = pat.get("health_card_number")
            if phc and not best_demo.health_card_number:
                best_demo.health_card_number = phc.strip()

            # Patient ID
            ppid = pat.get("patient_id")
            if ppid and not best_demo.patient_id:
                best_demo.patient_id = ppid.strip()

            # Address: prefer richer address
            paddr = pat.get("address")
            if paddr and (not best_demo.address or len(paddr) > len(best_demo.address)):
                best_demo.address = paddr.strip()

            for field_name in ("city", "district", "state", "pincode", "guardian_name", "phone"):
                val = pat.get(field_name)
                if val and not getattr(best_demo, field_name):
                    setattr(best_demo, field_name, str(val).strip())

        # 2. Documents
        for doc in r.get("documents", []) or []:
            if not isinstance(doc, dict):
                continue
            merged.documents.append(
                ExtractedDocumentInfo(
                    filename=doc.get("filename", ""),
                    relative_path=doc.get("relative_path", ""),
                    document_type=doc.get("document_type", "Medical Document"),
                    summary=doc.get("summary", ""),
                    clinical_notes=doc.get("clinical_notes", []) or [],
                )
            )

        # 3. Observations
        for obs in r.get("observations", []) or []:
            if not isinstance(obs, dict):
                continue
            tname = (obs.get("test_name") or "").strip()
            tval = str(obs.get("value") or "").strip()
            if not tname or not tval:
                continue
            key = (tname.lower(), tval.lower())
            if key in seen_tests:
                continue
            seen_tests.add(key)

            merged.observations.append(
                ExtractedObservation(
                    test_name=tname,
                    value=tval,
                    unit=obs.get("unit") or "",
                    reference_range=obs.get("reference_range") or "",
                    interpretation=obs.get("interpretation") or "",
                    source_document=obs.get("source_document") or "",
                )
            )

        # 4. Billing data
        for bill in r.get("billing", []) or []:
            if not isinstance(bill, dict):
                continue
            items = []
            for item in bill.get("items", []) or []:
                if isinstance(item, dict) and item.get("description"):
                    items.append(ExtractedBillingItem(
                        description=item.get("description", ""),
                        quantity=int(item.get("quantity", 1) or 1),
                        unit_price=float(item.get("unit_price", 0) or 0),
                        amount=float(item.get("amount", 0) or 0),
                        category=item.get("category", "General"),
                    ))
            if items or bill.get("total_amount"):
                merged.billing_data.append(ExtractedBillingData(
                    bill_number=bill.get("bill_number", "") or "",
                    bill_date=bill.get("bill_date", "") or "",
                    total_amount=float(bill.get("total_amount", 0) or 0),
                    payment_mode=bill.get("payment_mode", "") or "",
                    items=items,
                    source_document=bill.get("source_document", "") or "",
                ))

        # 5. Clinical notes & hospital/doctor
        for note in r.get("clinical_notes", []) or []:
            if note and note not in merged.clinical_notes:
                merged.clinical_notes.append(note)

        if r.get("hospital_name") and not merged.hospital_name:
            merged.hospital_name = r["hospital_name"]
        if r.get("treating_physician") and not merged.treating_physician:
            merged.treating_physician = r["treating_physician"]

    return merged


def process_archive_documents_with_gemini(
    documents: list[Any],  # list of ArchiveDocument
    batch_size: int = MAX_BATCH_SIZE,
) -> ArchiveExtractionResult:
    """Runs high-level OCR and information extraction on ALL archive documents.

    Args:
        documents: List of ArchiveDocument objects extracted from the ZIP archive.
        batch_size: Number of documents to send per Gemini API call.

    Returns:
        ArchiveExtractionResult containing demographics, document classification,
        observations, and clinical notes.
    """
    if not documents:
        return ArchiveExtractionResult()

    client = _get_gemini_client()
    log.info("Starting Gemini high-level OCR on %d documents...", len(documents))

    # Prepare document tuples
    doc_tuples = [
        (doc.filename, doc.relative_path, doc.file_bytes, doc.mime_type)
        for doc in documents
    ]

    # Chunk into batches
    batches: list[list[tuple[str, str, bytes, str]]] = []
    for i in range(0, len(doc_tuples), batch_size):
        batches.append(doc_tuples[i : i + batch_size])

    log.info("Divided %d documents into %d batches (batch_size=%d)",
             len(documents), len(batches), batch_size)

    results: list[dict[str, Any]] = [None] * len(batches)  # type: ignore

    start_time = time.perf_counter()

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(batches))) as executor:
        future_to_idx = {
            executor.submit(_extract_batch, client, batch, idx): idx
            for idx, batch in enumerate(batches)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                res = future.result()
                results[idx] = res
                log.info("  ✓ Batch %d/%d completed (%d documents processed)",
                         idx + 1, len(batches), len(batches[idx]))
            except Exception as exc:
                log.error("Batch %d failed with error: %s", idx + 1, exc)
                results[idx] = {}

    elapsed = round(time.perf_counter() - start_time, 2)
    log.info("Gemini high-level OCR on %d documents finished in %.2fs", len(documents), elapsed)

    merged = _merge_batch_results(
        [r for r in results if r],
        [d.relative_path for d in documents],
    )

    log.info("Extraction summary:")
    log.info("  Patient Name: %s", merged.demographics.name)
    log.info("  DOB: %s | Gender: %s", merged.demographics.birth_date, merged.demographics.gender)
    log.info("  Aadhaar: %s", merged.demographics.aadhaar_number)
    log.info("  Address: %s", merged.demographics.address)
    log.info("  Total Classified Docs: %d", len(merged.documents))
    log.info("  Total Extracted Observations: %d", len(merged.observations))
    log.info("  Total Extracted Bills: %d", len(merged.billing_data))

    return merged
