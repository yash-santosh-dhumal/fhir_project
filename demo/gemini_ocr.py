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

GEMINI_MODEL = os.environ.get("GEMINI_OCR_MODEL", "gemini-3.5-flash-lite")
MAX_BATCH_SIZE = int(os.environ.get("GEMINI_OCR_BATCH_SIZE", "10"))
MAX_WORKERS = int(os.environ.get("GEMINI_OCR_MAX_WORKERS", "4"))
MAX_RETRIES = int(os.environ.get("GEMINI_OCR_MAX_RETRIES", "3"))


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
class ExtractedInsurancePolicy:
    """Insurance policy / pre-authorization schedule data extracted from insurance documents."""
    policy_number: str = ""
    scheme_or_insurer: str = ""
    health_card_number: str = ""
    claim_or_preauth_number: str = ""
    policy_status: str = "Active"
    annual_sum_insured: float = 0.0
    copayment_liability: float = 0.0          # Flat copay/deductible, e.g. 0.0
    copayment_percentage: float = 0.0         # Co-pay percentage if applicable
    coverage_type: str = ""                   # Cashless, Floater, etc.
    pre_auth_approved_amount: float = 0.0     # Trust/Insurer Approved Amount
    patient_out_of_pocket: float = 0.0        # Patient Liability stated in document
    covered_categories: list[str] = field(default_factory=list)
    excluded_categories: list[str] = field(default_factory=list)  # Treatments / drugs NOT covered
    deductible_amount: float = 0.0            # Per-claim or per-hospitalization deductible (INR)
    room_rent_sublimit: float = 0.0           # Room rent sub-limit per day (INR, 0 = no sub-limit)
    terms_and_rules: str = ""
    source_document: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ArchiveExtractionResult:
    """Complete aggregated extraction from all archive documents."""
    demographics: ExtractedDemographics = field(default_factory=ExtractedDemographics)
    documents: list[ExtractedDocumentInfo] = field(default_factory=list)
    observations: list[ExtractedObservation] = field(default_factory=list)
    billing_data: list[ExtractedBillingData] = field(default_factory=list)
    insurance_policy: ExtractedInsurancePolicy | None = None
    clinical_notes: list[str] = field(default_factory=list)
    hospital_name: str | None = None
    treating_physician: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "demographics": self.demographics.to_dict(),
            "documents": [d.to_dict() for d in self.documents],
            "observations": [o.to_dict() for o in self.observations],
            "billing_data": [b.to_dict() for b in self.billing_data],
            "insurance_policy": self.insurance_policy.to_dict() if self.insurance_policy else None,
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
<<<<<<< HEAD
                if max_dim > 1400 or len(file_bytes) > 300 * 1024 or img.format != "JPEG":
                    if max_dim > 1400:
                        scale = 1400.0 / max_dim
                        img = img.resize((int(w * scale), int(h * scale)), PIL.Image.Resampling.BILINEAR)
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=82, optimize=True)
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
5. Insurance Policy / Health Insurance Claim / Pre-Authorization Document (if any document is a health insurance certificate, policy schedule, PM-JAY/Aarogyasri card, or pre-authorization claim settlement):
   - policy_number: official policy / card / health schedule number (e.g. "WAP138200500121/04")
   - scheme_or_insurer: scheme or insurer name (e.g. "Dr. YSR Aarogyasri / AB-PMJAY")
   - health_card_number: health card ID
   - claim_or_preauth_number: pre-authorization claim reference number (e.g. "APTRUST/KNL/2025/1/13510681/07")
   - policy_status: policy status (e.g. "ACTIVE / IN-FORCE")
   - annual_sum_insured: total family floater or annual limit in numeric rupees (e.g. 2500000.0 or 500000.0)
   - copayment_liability: beneficiary copayment or deductible as a SPECIFIC RUPEE AMOUNT stated in the document (e.g. 8592.0). Set to 0.0 if the document only mentions a percentage (like 20%) without a specific rupee figure, or if NIL / Cashless
   - copayment_percentage: copay percentage if specified (e.g. 20.0 for "20% co-payment", 0.0 if NIL / 100% cashless)
   - coverage_type: e.g. "Cashless Government Health Assurance Floater" or "Co-Pay Health Assurance Scheme (80:20 Risk-Sharing)"
   - pre_auth_approved_amount: SPECIFIC RUPEE amount approved / sanctioned by trust or insurer for THIS patient's claim (e.g. 42962.0 or 70473.0). Set to 0.0 if the document does NOT mention a specific claim settlement amount — many policy documents only describe general coverage rules without specifying exact patient-specific claim amounts
   - patient_out_of_pocket: SPECIFIC RUPEE amount the patient must pay as stated in the document (e.g. 8592.0). Set to 0.0 if no specific rupee figure is mentioned — the system will automatically calculate this from the bill and copayment_percentage
   - covered_categories: list of covered benefit heads / treatment types that the policy covers (e.g. ["Room & Board", "Professional Fees", "Investigations", "Surgical / Procedures", "Pharmacy / Medications", "ICU & Critical Care", "Oncology", "Day Care Treatment"]). Extract ALL covered treatment categories mentioned in the policy document
   - terms_and_rules: concise policy coverage statement including the cost-sharing rule (e.g. "80% insurer underwritten, 20% beneficiary co-payment" or "100% cashless treatment with NIL co-payment"). Include the coverage percentage split if mentioned

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
  "insurance_policy": {{
    "policy_number": "string",
    "scheme_or_insurer": "string",
    "health_card_number": "string or null",
    "claim_or_preauth_number": "string",
    "policy_status": "Active",
    "annual_sum_insured": 0.0,
    "copayment_liability": 0.0,
    "copayment_percentage": 0.0,
    "coverage_type": "string",
    "pre_auth_approved_amount": 0.0,
    "patient_out_of_pocket": 0.0,
    "covered_categories": ["string"],
    "terms_and_rules": "string",
    "source_document": "string (filename)"
  }},
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
            return _clean_and_parse_json(raw_text)
        except Exception as exc:
            last_err = exc
            log.warning("Batch %d attempt %d failed: %s", batch_index, attempt + 1, exc)
            if attempt < MAX_RETRIES - 1:
                err_str = str(exc)
                retry_match = re.search(r"retry in ([\d\.]+)s", err_str, re.IGNORECASE)
                if retry_match:
                    sleep_time = min(35.0, float(retry_match.group(1)) + 0.5)
                elif "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    sleep_time = min(30.0, 5.0 * (attempt + 1))
                else:
                    sleep_time = (attempt + 1) * 2.0
                time.sleep(sleep_time)

    log.error("Batch %d failed completely after %d attempts: %s", batch_index, MAX_RETRIES, last_err)
    return {}


def _clean_and_parse_json(raw_text: str) -> dict[str, Any]:
    """Robust JSON parser that sanitizes markdown blocks, trailing commas, and boundary issues."""
    if not raw_text:
        return {}

    # 1. Clean markdown wrapping
    if "```" in raw_text:
        match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw_text, re.DOTALL)
        if match:
            raw_text = match.group(1).strip()
        else:
            raw_text = re.sub(r"^```json\s*", "", raw_text)
            raw_text = re.sub(r"```\s*$", "", raw_text).strip()

    # 2. Extract outermost JSON object boundary
    if "{" in raw_text:
        first_brace = raw_text.find("{")
        last_brace = raw_text.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            raw_text = raw_text[first_brace : last_brace + 1]

    # 3. Direct parse attempt
    try:
        return json.loads(raw_text)
    except Exception:
        pass

    # 4. Clean trailing commas before closing braces/brackets (e.g. [1, 2,] or {a: 1,})
    cleaned = re.sub(r",\s*([\}\]])", r"\1", raw_text)
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # 5. Add missing quotes to unquoted property keys (e.g. { name: "val" })
    cleaned_keys = re.sub(r'(?<=[{,])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r' "\1":', cleaned)
    try:
        return json.loads(cleaned_keys)
    except Exception:
        pass

    # Re-attempt with standard parser to propagate informative error if truly unparseable
    return json.loads(raw_text)


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

        # 5. Insurance Policy data
        ip = r.get("insurance_policy")
        if isinstance(ip, dict) and (ip.get("policy_number") or ip.get("scheme_or_insurer") or ip.get("annual_sum_insured")):
            ip_src = str(ip.get("source_document") or "").lower()
            is_dedicated = any(k in ip_src for k in ("insurance_policy", "demo claims", "policy_document", "claim"))
            curr_src = (merged.insurance_policy.source_document or "").lower() if merged.insurance_policy else ""
            curr_is_dedicated = any(k in curr_src for k in ("insurance_policy", "demo claims", "policy_document"))
            curr_appr = merged.insurance_policy.pre_auth_approved_amount if merged.insurance_policy else 0.0
            new_appr = float(ip.get("pre_auth_approved_amount", 0) or 0)
            has_copay = float(ip.get("copayment_percentage", 0) or 0) > 0 or float(ip.get("patient_out_of_pocket", 0) or 0) > 0

            should_update = False
            if not merged.insurance_policy:
                should_update = True
            elif is_dedicated:
                should_update = True
            elif not curr_is_dedicated and has_copay:
                should_update = True
            elif not curr_is_dedicated and new_appr >= curr_appr:
                should_update = True

            if should_update:
                merged.insurance_policy = ExtractedInsurancePolicy(
                    policy_number=str(ip.get("policy_number") or "").strip(),
                    scheme_or_insurer=str(ip.get("scheme_or_insurer") or "").strip(),
                    health_card_number=str(ip.get("health_card_number") or "").strip(),
                    claim_or_preauth_number=str(ip.get("claim_or_preauth_number") or "").strip(),
                    policy_status=str(ip.get("policy_status") or "Active").strip(),
                    annual_sum_insured=float(ip.get("annual_sum_insured", 0) or 0),
                    copayment_liability=float(ip.get("copayment_liability", 0) or 0),
                    copayment_percentage=float(ip.get("copayment_percentage", 0) or 0),
                    coverage_type=str(ip.get("coverage_type") or "").strip(),
                    pre_auth_approved_amount=new_appr,
                    patient_out_of_pocket=float(ip.get("patient_out_of_pocket", 0) or 0),
                    covered_categories=ip.get("covered_categories", []) or [],
                    terms_and_rules=str(ip.get("terms_and_rules") or "").strip(),
                    source_document=str(ip.get("source_document") or "").strip(),
                )

        # 6. Clinical notes & hospital/doctor
        for note in r.get("clinical_notes", []) or []:
            if note and note not in merged.clinical_notes:
                merged.clinical_notes.append(note)

        if r.get("hospital_name") and not merged.hospital_name:
            merged.hospital_name = r["hospital_name"]
        if r.get("treating_physician") and not merged.treating_physician:
            merged.treating_physician = r["treating_physician"]

    # Filter billing data: if a demo bill is present, consider only the demo bill as the final bill
    demo_bills = [
        b for b in merged.billing_data
        if "demo bill" in (b.source_document or "").lower()
        or "demo_bill" in (b.source_document or "").lower()
        or "hospital_bill" in (b.source_document or "").lower()
    ]
    if demo_bills:
        merged.billing_data = demo_bills

    return merged


def process_archive_documents_with_gemini(
    documents: list[Any],  # list of ArchiveDocument
    batch_size: int = MAX_BATCH_SIZE,
    skip_insurance: bool = False,
) -> ArchiveExtractionResult:
    """Runs high-level OCR and information extraction on ALL archive documents.

    Separates clinical text documents from non-text visual evidence photos to
    eliminate unnecessary vision model calls, and extracts priority financial
    documents (hospital bill and insurance policy) in a dedicated fast batch.

    Args:
        documents: List of ArchiveDocument objects extracted from the ZIP archive.
        batch_size: Number of documents to send per Gemini API call.
        skip_insurance: If True, ignores insurance policy documents (hospital mode).

    Returns:
        ArchiveExtractionResult containing demographics, document classification,
        observations, billing, and insurance policy data.
    """
    if not documents:
        return ArchiveExtractionResult()

    client = _get_gemini_client()
    log.info("Starting Gemini high-level OCR on %d documents (skip_insurance=%s)...", len(documents), skip_insurance)

    # Separate clinical text documents from non-text visual evidence photos
    text_docs = [d for d in documents if getattr(d, "is_clinical_text_document", True)]
    visual_docs = [d for d in documents if not getattr(d, "is_clinical_text_document", True)]
    log.info("Processing %d clinical text documents with Gemini vision (skipping %d non-text visual evidence photos)...",
             len(text_docs), len(visual_docs))

    # Deduplicate clinical text documents with identical file content bytes
    import hashlib
    content_hash_map: dict[str, Any] = {}
    dup_to_canonical: dict[str, str] = {}
    unique_text_docs: list[Any] = []
    for d in text_docs:
        h = hashlib.sha256(d.file_bytes).hexdigest()
        if h in content_hash_map:
            dup_to_canonical[d.relative_path] = content_hash_map[h].relative_path
        else:
            content_hash_map[h] = d
            unique_text_docs.append(d)

    if dup_to_canonical:
        log.info("Deduplicated %d identical document(s) in archive (%d unique clinical documents to process)",
                 len(dup_to_canonical), len(unique_text_docs))

    # Separate priority financial documents (bill and insurance claim) to guarantee fast & exact extraction
    priority_financial_docs: list[Any] = []
    regular_clinical_docs: list[Any] = []
    for d in unique_text_docs:
        rel_lower = d.relative_path.lower()
        is_bill = "demo bill" in rel_lower or "hospital_bill" in rel_lower
        is_policy = not skip_insurance and ("demo claims" in rel_lower or "insurance_policy" in rel_lower)
        if is_bill or is_policy:
            priority_financial_docs.append(d)
        elif not (skip_insurance and ("demo claims" in rel_lower or "insurance_policy" in rel_lower)):
            regular_clinical_docs.append(d)

    # Prepare batch tuples: (filename, relative_path, file_bytes, mime_type)
    batches: list[list[tuple[str, str, bytes, str]]] = []

    # Priority batch (Financial: Bill + Insurance Policy)
    if priority_financial_docs:
        batches.append([
            (doc.filename, doc.relative_path, doc.file_bytes, doc.mime_type)
            for doc in priority_financial_docs
        ])

    # Regular clinical batches chunked by batch_size
    reg_tuples = [
        (doc.filename, doc.relative_path, doc.file_bytes, doc.mime_type)
        for doc in regular_clinical_docs
    ]
    for i in range(0, len(reg_tuples), batch_size):
        batches.append(reg_tuples[i : i + batch_size])

    log.info("Divided %d unique clinical documents into %d parallel batches (max_workers=%d)",
             len(unique_text_docs), len(batches), min(MAX_WORKERS, len(batches)))

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
                import gc
                gc.collect()
            except Exception as exc:
                log.error("Batch %d failed with error: %s", idx + 1, exc)
                results[idx] = {}

    elapsed = round(time.perf_counter() - start_time, 2)
    log.info("Gemini high-level OCR on %d unique documents finished in %.2fs", len(unique_text_docs), elapsed)

    merged = _merge_batch_results(
        [r for r in results if r],
        [d.relative_path for d in unique_text_docs],
    )

    if skip_insurance:
        merged.insurance_policy = None

    # Reconstruct merged.documents in the original order of all archive documents,
    # ensuring visual evidence photos are clearly classified without omission,
    # and duplicates are seamlessly populated from the canonical document.
    doc_map = {d.relative_path: d for d in merged.documents}
    ordered_docs: list[ExtractedDocumentInfo] = []
    for d in documents:
        if d.relative_path in doc_map:
            ordered_docs.append(doc_map[d.relative_path])
        elif d.relative_path in dup_to_canonical and dup_to_canonical[d.relative_path] in doc_map:
            canon = doc_map[dup_to_canonical[d.relative_path]]
            cloned = ExtractedDocumentInfo(
                filename=d.filename,
                relative_path=d.relative_path,
                document_type=canon.document_type,
                summary=canon.summary,
                clinical_notes=list(canon.clinical_notes),
            )
            ordered_docs.append(cloned)
        else:
            fn_lower = d.filename.lower()
            fp_lower = d.relative_path.lower()
            if any(k in fn_lower for k in ("qr", "emblem")):
                doc_type = "Administrative Asset / QR Code"
            elif any(k in fn_lower for k in ("photo", "selfie", "portrait")):
                doc_type = "Patient Identification / Photo Evidence"
            elif "page_" in fn_lower:
                doc_type = "Insurance Policy Page (Pre-Rendered Image)"
            elif any(k in fp_lower for k in ("ward", "bed", "surgery", "intra op")):
                doc_type = "Ward / Treatment Visual Evidence"
            else:
                doc_type = "Visual Evidence / Clinical Photo"

            ordered_docs.append(
                ExtractedDocumentInfo(
                    filename=d.filename,
                    relative_path=d.relative_path,
                    document_type=doc_type,
                    summary=f"Visual evidence photo ({d.mime_type})",
                    clinical_notes=[],
                )
            )
    merged.documents = ordered_docs

    log.info("Extraction summary:")
    log.info("  Patient Name: %s", merged.demographics.name)
    log.info("  DOB: %s | Gender: %s", merged.demographics.birth_date, merged.demographics.gender)
    log.info("  Aadhaar: %s", merged.demographics.aadhaar_number)
    log.info("  Address: %s", merged.demographics.address)
    log.info("  Total Classified Docs: %d (all %d archive entries)", len(merged.documents), len(documents))
    log.info("  Total Extracted Observations: %d", len(merged.observations))
    log.info("  Total Extracted Bills: %d", len(merged.billing_data))
    if merged.insurance_policy:
        log.info("  Extracted Insurance Policy: %s (%s)",
                 merged.insurance_policy.policy_number, merged.insurance_policy.scheme_or_insurer)

    return merged


def extract_insurance_policy_from_doc(
    file_bytes: bytes,
    mime_type: str,
    filename: str,
) -> ExtractedInsurancePolicy | None:
    """Extracts insurance policy parameters, copayment rules, and coverage categories from a single document."""
    client = _get_gemini_client()
    parts: list[Any] = []

    # Infer mime if not provided or generic
    if not mime_type or mime_type in ("application/octet-stream", "application/x-download"):
        fn_lower = filename.lower()
        if fn_lower.endswith(".pdf"):
            mime_type = "application/pdf"
        elif fn_lower.endswith((".jpg", ".jpeg")):
            mime_type = "image/jpeg"
        elif fn_lower.endswith(".png"):
            mime_type = "image/png"
        else:
            mime_type = "application/pdf"

    from google.genai import types
    part = _prepare_document_part(file_bytes, mime_type, filename)
    parts.append(part)

    prompt = f"""You are an expert health insurance claims auditor.
Analyze the attached insurance policy document ({filename}) and extract all policy, coverage, and adjudication parameters.

Extract:
1. scheme_or_insurer: Insurance company name or government scheme (e.g. "Dr. YSR Aarogyasri / AB-PMJAY", "Star Health", "ICICI Lombard", etc.)
2. policy_number: Policy / Identification number
3. health_card_number: Health card / Beneficiary ID (if present)
4. claim_or_preauth_number: Pre-authorization or claim reference number (if present)
5. policy_status: "Active", "Sanctioned", etc.
6. annual_sum_insured: Annual coverage sum limit (float, in INR, e.g. 2500000.0)
7. copayment_percentage: Patient co-payment liability percentage as a number (e.g. 20.0 for 80:20 risk-sharing / cost-sharing mandate. If 100% cashless, return 0.0)
8. coverage_type: e.g. "Co-Pay Health Assurance Scheme (80:20 Risk-Sharing)" or "Cashless Health Insurance Floater"
9. pre_auth_approved_amount: Exact pre-approved / sanctioned amount in INR (if explicitly stated, else 0.0)
10. patient_out_of_pocket: Patient payable amount in INR (if explicitly stated, else 0.0)
11. covered_categories: List of treatments, procedures, and charges covered under the policy (e.g. ["In-Patient Hospitalization", "Medical & Surgical Oncology", "ICU", "Investigations", "Pharmacy", "Bed Charges", "General Ward"])
12. excluded_categories: List of treatments, procedures, drugs, or charges SPECIFICALLY EXCLUDED or NOT COVERED under this policy. Look for any exclusion clauses, policy addendums, or "not covered" statements (e.g. ["Oral Chemotherapy Drugs", "Dental Treatment", "Cosmetic Surgery"]). If no specific exclusions beyond standard waiting-period exclusions, return an empty list.
13. deductible_amount: Per-claim or per-hospitalization deductible amount in INR that the insured must bear before the insurer pays (e.g. 5000.0). If NIL or not mentioned, return 0.0
14. room_rent_sublimit: Room rent sub-limit per day in INR (e.g. 500.0 for General Ward, or the lowest sub-limit if multiple). If no room rent sub-limit or if room rent is "as per actuals", return 0.0
15. terms_and_rules: Concise policy clause statement explaining the cost-sharing terms, deductible rules, exclusions, and room rent sub-limits (e.g. "80% sanctioned by Trust/Insurer, 20% patient out-of-pocket co-payment liability. INR 5000 deductible per claim. Oral chemotherapy drugs excluded.")

OUTPUT REQUIREMENT: Return ONLY a valid JSON object matching this schema:
{{
  "insurance_policy": {{
    "scheme_or_insurer": "string",
    "policy_number": "string",
    "health_card_number": "string or null",
    "claim_or_preauth_number": "string",
    "policy_status": "Active",
    "annual_sum_insured": 0.0,
    "copayment_liability": 0.0,
    "copayment_percentage": 0.0,
    "coverage_type": "string",
    "pre_auth_approved_amount": 0.0,
    "patient_out_of_pocket": 0.0,
    "covered_categories": ["string"],
    "excluded_categories": ["string"],
    "deductible_amount": 0.0,
    "room_rent_sublimit": 0.0,
    "terms_and_rules": "string"
  }}
}}
"""
    parts.append(prompt)
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
            data = _clean_and_parse_json(raw_text)
            ip = data.get("insurance_policy") or data
            if ip and isinstance(ip, dict):
                # Clean copay percentage
                copay_raw = ip.get("copayment_percentage", 0)
                copay_val = 0.0
                if isinstance(copay_raw, str):
                    m = re.search(r"(\d+(?:\.\d+)?)", copay_raw)
                    if m:
                        copay_val = float(m.group(1))
                else:
                    copay_val = float(copay_raw or 0)

                # Check terms for percentage split if copay is 0
                terms_text = str(ip.get("terms_and_rules") or "")
                cov_type_text = str(ip.get("coverage_type") or "")
                if copay_val == 0.0:
                    for txt in (terms_text, cov_type_text):
                        m2 = re.search(r"(\d{2})\s*:\s*(\d{2})", txt)
                        if m2:
                            copay_val = float(m2.group(2))
                            break

                # Extract terms and rules
                raw_terms = ip.get("terms_and_rules", "")
                if isinstance(raw_terms, list):
                    terms_str = "; ".join(str(t) for t in raw_terms)
                else:
                    terms_str = str(raw_terms or "")

                raw_categories = ip.get("covered_categories", [])
                if isinstance(raw_categories, str):
                    categories_list = [c.strip() for c in raw_categories.split(",") if c.strip()]
                elif isinstance(raw_categories, list):
                    categories_list = [str(c).strip() for c in raw_categories if str(c).strip()]
                else:
                    categories_list = []

                # Parse excluded categories
                raw_excluded = ip.get("excluded_categories", [])
                if isinstance(raw_excluded, str):
                    excluded_list = [c.strip() for c in raw_excluded.split(",") if c.strip()]
                elif isinstance(raw_excluded, list):
                    excluded_list = [str(c).strip() for c in raw_excluded if str(c).strip()]
                else:
                    excluded_list = []

                return ExtractedInsurancePolicy(
                    policy_number=str(ip.get("policy_number") or "").strip() or "POL-UNKNOWN",
                    scheme_or_insurer=str(ip.get("scheme_or_insurer") or "").strip() or "Insurance Policy",
                    health_card_number=str(ip.get("health_card_number") or "").strip(),
                    claim_or_preauth_number=str(ip.get("claim_or_preauth_number") or "").strip(),
                    policy_status=str(ip.get("policy_status") or "Active").strip(),
                    annual_sum_insured=float(ip.get("annual_sum_insured", 0) or 0),
                    copayment_liability=float(ip.get("copayment_liability", 0) or 0),
                    copayment_percentage=copay_val,
                    coverage_type=cov_type_text or ("Co-Pay Policy" if copay_val > 0 else "Cashless Policy"),
                    pre_auth_approved_amount=float(ip.get("pre_auth_approved_amount", 0) or 0),
                    patient_out_of_pocket=float(ip.get("patient_out_of_pocket", 0) or 0),
                    covered_categories=categories_list,
                    excluded_categories=excluded_list,
                    deductible_amount=float(ip.get("deductible_amount", 0) or 0),
                    room_rent_sublimit=float(ip.get("room_rent_sublimit", 0) or 0),
                    terms_and_rules=terms_str,
                    source_document=filename,
                )
        except Exception as exc:
            log.warning("extract_insurance_policy_from_doc attempt %d failed: %s", attempt + 1, exc)
            if attempt < MAX_RETRIES - 1:
                time.sleep(2.0)
    return None

