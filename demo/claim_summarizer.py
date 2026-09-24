"""Insurance Claim Summarizer — Extracts key claim fields and document section summaries.

Uses Gemini Vision API to produce a structured claim summary from the already-extracted
FHIR bundle and Gemini OCR results.  This module is ONLY used by the insurance portal
routes; the hospital pipeline is untouched.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

GEMINI_MODEL = os.environ.get("GEMINI_OCR_MODEL", "gemini-3.1-flash-lite")
MAX_RETRIES = 3


# ── Data Classes ──────────────────────────────────────────────

@dataclass
class ClaimKeyFields:
    """Top-level key claim data to display prominently in the report."""
    claim_number: str = ""
    policy_number: str = ""
    insured_name: str = ""
    insurer_tpa_name: str = ""
    primary_diagnosis: str = ""
    icd_code: str = ""
    hospital_name: str = ""
    admission_date: str = ""
    discharge_date: str = ""
    total_claimed_amount: str = ""
    gross_bill_amount: str = ""
    net_claimed_amount: str = ""
    pre_authorized_amount: str = ""
    approved_amount: str = ""
    sum_insured: str = ""
    claim_type: str = ""              # Cashless / Reimbursement
    patient_age: str = ""
    patient_gender: str = ""
    treating_doctor: str = ""
    room_category: str = ""
    length_of_stay: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DocumentSection:
    """Summary of one logical section extracted from a multi-page document."""
    title: str                        # e.g. "Discharge Summary", "Hospital Bill"
    summary: str                      # 2-3 sentence summary
    key_data_points: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ClaimSummary:
    """Complete structured claim summary returned to the frontend."""
    key_fields: ClaimKeyFields = field(default_factory=ClaimKeyFields)
    document_sections: list[DocumentSection] = field(default_factory=list)
    claim_narrative: str = ""         # 3-4 sentence executive summary
    flags: list[str] = field(default_factory=list)  # e.g. "High-value claim", "ICU stay"

    def to_dict(self) -> dict[str, Any]:
        return {
            "key_fields": self.key_fields.to_dict(),
            "document_sections": [s.to_dict() for s in self.document_sections],
            "claim_narrative": self.claim_narrative,
            "flags": self.flags,
        }


# ── Gemini Client ─────────────────────────────────────────────

def _get_gemini_client():
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Neither GOOGLE_API_KEY nor GEMINI_API_KEY is set in environment or .env"
        )
    from google import genai
    return genai.Client(api_key=api_key)


# ── Core Summarization ────────────────────────────────────────

def summarize_claim(
    gemini_extraction: Any | None = None,
    fhir_bundle: dict[str, Any] | None = None,
    archive_info: dict[str, Any] | None = None,
) -> ClaimSummary:
    """Generates a structured insurance claim summary.

    Uses data already extracted by gemini_ocr + fhir_unifier to build the
    claim summary.  Falls back to rule-based extraction from the FHIR bundle
    when Gemini extraction is not available.

    Args:
        gemini_extraction: ArchiveExtractionResult from gemini_ocr.py
        fhir_bundle: The unified FHIR bundle dict
        archive_info: Optional archive_info dict from the convert response

    Returns:
        A ClaimSummary with key fields, document sections, and narrative.
    """
    summary = ClaimSummary()

    # ── 1. Extract key fields from Gemini OCR extraction ──────
    if gemini_extraction:
        _populate_from_gemini(summary, gemini_extraction)

    # ── 2. Enrich / fill gaps from FHIR bundle ───────────────
    if fhir_bundle:
        _populate_from_bundle(summary, fhir_bundle)

    # ── 3. Enrich from archive_info ──────────────────────────
    if archive_info:
        _populate_from_archive_info(summary, archive_info)

    # ── 4. Generate claim narrative from Gemini ──────────────
    try:
        _generate_claim_narrative(summary, gemini_extraction, fhir_bundle)
    except Exception as exc:
        log.warning("Claim narrative generation failed: %s", exc)
        # Build a fallback narrative from available data
        summary.claim_narrative = _build_fallback_narrative(summary)

    # ── 5. Generate flags ────────────────────────────────────
    _generate_flags(summary)

    return summary


def _populate_from_gemini(summary: ClaimSummary, extraction: Any) -> None:
    """Fills claim key fields from Gemini extraction data."""
    kf = summary.key_fields
    demo = getattr(extraction, "demographics", None)

    if demo:
        d = demo.to_dict() if hasattr(demo, "to_dict") else (demo or {})
        if isinstance(d, dict):
            if not kf.insured_name:
                kf.insured_name = d.get("name") or ""
            if not kf.patient_age:
                kf.patient_age = d.get("birth_date") or ""
            if not kf.patient_gender:
                kf.patient_gender = d.get("gender") or ""
            if not kf.policy_number:
                kf.policy_number = d.get("health_card_number") or ""
            if not kf.claim_number:
                kf.claim_number = d.get("patient_id") or ""

    if not kf.hospital_name:
        kf.hospital_name = getattr(extraction, "hospital_name", "") or ""
    if not kf.treating_doctor:
        kf.treating_doctor = getattr(extraction, "treating_physician", "") or ""

    # Build document sections from classified documents
    docs = getattr(extraction, "documents", []) or []
    for doc in docs:
        d_dict = doc.to_dict() if hasattr(doc, "to_dict") else (doc if isinstance(doc, dict) else {})
        title = d_dict.get("document_type") or d_dict.get("filename") or "Document"
        doc_summary = d_dict.get("summary") or ""
        notes = d_dict.get("clinical_notes") or []

        key_points = []
        if notes:
            key_points = [str(n) for n in notes[:5]]

        summary.document_sections.append(DocumentSection(
            title=title,
            summary=doc_summary,
            key_data_points=key_points,
        ))

    # Extract claim-specific fields from clinical notes and document sections
    clinical_notes = getattr(extraction, "clinical_notes", []) or []
    all_data_points = list(clinical_notes)
    for sec in summary.document_sections:
        if sec.summary:
            all_data_points.append(sec.summary)
        all_data_points.extend(sec.key_data_points)

    for note in all_data_points:
        _extract_financials_into_keyfields(kf, str(note))


def _clean_amount(amt_str: str) -> str:
    """Format amount string cleanly with Rupee symbol if numeric."""
    if not amt_str:
        return ""
    s = str(amt_str).strip()
    if not re.search(r'\d', s):
        return ""
    # Strip any leading stray currency, commas, colons
    s_core = re.sub(r'^[₹Rs.INR\s,:-]+', '', s, flags=re.I)
    m = re.search(r'(\d[\d,]*(?:\.\d{1,2})?)', s_core)
    if not m:
        return ""
    num_str = m.group(1).rstrip(',')
    try:
        clean_num = float(num_str.replace(',', ''))
        if clean_num <= 0:
            return ""
        if clean_num % 1 != 0:
            return f"₹{clean_num:,.2f}"
        return f"₹{int(clean_num):,}"
    except (ValueError, TypeError):
        return f"₹{num_str}"


def _extract_financials_into_keyfields(kf: ClaimKeyFields, text: str) -> None:
    """Extracts financial figures and dates from any text note or summary."""
    s = str(text).strip()
    if not s:
        return
    s_lower = s.lower()

    # Gross bill amount - requires at least one digit
    if any(k in s_lower for k in ("gross", "hospital bill", "total bill", "bill amount")):
        m = (re.search(r'([\u20b9Rs.INR\s]*\d[\d,]*(?:\.\d{2})?)\s*gross', s, re.I) or
             re.search(r'(?:gross|hospital bill|total bill)[^\d\u20b9]*([\u20b9Rs.INR\s]*\d[\d,]*(?:\.\d{2})?)', s, re.I))
        if m and not kf.gross_bill_amount:
            amt = _clean_amount(m.group(1))
            if amt:
                kf.gross_bill_amount = amt

    # Net claimed amount - requires at least one digit
    if any(k in s_lower for k in ("net claimed", "claimed amount", "net amount", "net bill")):
        m = (re.search(r'([\u20b9Rs.INR\s]*\d[\d,]*(?:\.\d{2})?)\s*net', s, re.I) or
             re.search(r'(?:net claimed|claimed amount|net amount)[^\d\u20b9]*([\u20b9Rs.INR\s]*\d[\d,]*(?:\.\d{2})?)', s, re.I))
        if m and not kf.net_claimed_amount:
            amt = _clean_amount(m.group(1))
            if amt:
                kf.net_claimed_amount = amt

    # Pre-authorized amount - requires at least one digit
    if any(k in s_lower for k in ("pre-auth", "preauth", "pre auth")):
        m = (re.search(r'([\u20b9Rs.INR\s]*\d[\d,]*(?:\.\d{2})?)\s*pre-?auth', s, re.I) or
             re.search(r'pre-?auth[^\d\u20b9]*([\u20b9Rs.INR\s]*\d[\d,]*(?:\.\d{2})?)', s, re.I))
        if m and not kf.pre_authorized_amount:
            amt = _clean_amount(m.group(1))
            if amt:
                kf.pre_authorized_amount = amt

    # Approved / Sanctioned amount - requires at least one digit
    if any(k in s_lower for k in ("approved", "sanctioned", "settled")):
        m = (re.search(r'([\u20b9Rs.INR\s]*\d[\d,]*(?:\.\d{2})?)\s*(?:approved|sanctioned|settled)', s, re.I) or
             re.search(r'(?:approved|sanctioned|settled)[^\d\u20b9]*([\u20b9Rs.INR\s]*\d[\d,]*(?:\.\d{2})?)', s, re.I))
        if m and not kf.approved_amount:
            amt = _clean_amount(m.group(1))
            if amt:
                kf.approved_amount = amt

    # Sum insured - requires at least one digit
    if any(k in s_lower for k in ("sum insured", "sum-insured", "cover amount", "policy amount", "sum assured")):
        m = (re.search(r'([\u20b9Rs.INR\s]*\d[\d,]*(?:\.\d{2})?[A-Za-z]*)\s*sum\s*insured', s, re.I) or
             re.search(r'sum\s*insured[^\d\u20b9]*([\u20b9Rs.INR\s]*\d[\d,]*(?:\.\d{2})?[A-Za-z]*)', s, re.I))
        if m and not kf.sum_insured:
            amt = _clean_amount(m.group(1))
            if amt:
                kf.sum_insured = amt

    # Clinical fields & dates
    if not kf.primary_diagnosis and any(k in s_lower for k in ("diagnosis", "diagnosed", "condition")):
        kf.primary_diagnosis = s
    if not kf.admission_date and any(k in s_lower for k in ("admitted", "admission", "date of admission")):
        date_match = re.search(r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}', s)
        if date_match:
            kf.admission_date = date_match.group(0)
    if not kf.discharge_date and any(k in s_lower for k in ("discharged", "discharge", "date of discharge")):
        date_match = re.search(r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}', s)
        if date_match:
            kf.discharge_date = date_match.group(0)

    # Room category: exclude medication phrases, prescriptions, and postal addresses
    if not kf.room_category:
        bad_room_words = ("prescrib", "tab", "tablet", "capsule", "mg", "syrup", "daily", "imatinib", "dose", "chemo", "village", "mandal", "district", "pin", "ward-", "street", "road")
        if not any(w in s_lower for w in bad_room_words):
            if re.search(r'\b(?:general ward|private room|semi-private|icu|deluxe|twin sharing|daycare|day care|single room|executive room)\b', s_lower):
                m_room = re.search(r'\b(?:general ward|private room|semi-private(?: ward)?|icu|deluxe(?: room)?|twin sharing|daycare|day care|single(?: private)? room|executive room)\b', s, re.I)
                if m_room:
                    kf.room_category = m_room.group(0).title()
            elif re.search(r'\b(?:room category|accommodation|bed type)\s*[:\-]\s*([a-zA-Z0-9\s\/-]+)', s, re.I):
                m_room = re.search(r'\b(?:room category|accommodation|bed type)\s*[:\-]\s*([a-zA-Z0-9\s\/-]+)', s, re.I)
                if m_room:
                    cand = m_room.group(1).split(',')[0].split(';')[0].strip()
                    if not any(w in cand.lower() for w in bad_room_words) and len(cand) < 40:
                        kf.room_category = cand

    if not kf.claim_type and any(k in s_lower for k in ("cashless", "reimbursement")):
        kf.claim_type = "Cashless" if "cashless" in s_lower else "Reimbursement"
    if not kf.length_of_stay and any(k in s_lower for k in ("length of stay", "days of stay", "duration")):
        days_match = re.search(r'(\d+)\s*(?:days?|nights?)', s, re.IGNORECASE)
        if days_match:
            kf.length_of_stay = f"{days_match.group(1)} days"

    # Total claimed amount priority: net claimed > gross bill > any other claimed
    if kf.net_claimed_amount:
        kf.total_claimed_amount = kf.net_claimed_amount
    elif kf.gross_bill_amount:
        kf.total_claimed_amount = kf.gross_bill_amount


def _populate_from_bundle(summary: ClaimSummary, bundle: dict[str, Any]) -> None:
    """Fills claim fields from FHIR bundle resources."""
    kf = summary.key_fields
    entries = bundle.get("entry", []) or []
    resources = [e.get("resource") for e in entries if isinstance(e, dict) and e.get("resource")]

    for r in resources:
        rtype = r.get("resourceType")

        if rtype == "Patient":
            if not kf.insured_name:
                names = r.get("name", []) or []
                if names:
                    kf.insured_name = names[0].get("text") or ""
            if not kf.patient_gender:
                kf.patient_gender = r.get("gender") or ""
            if not kf.patient_age:
                kf.patient_age = r.get("birthDate") or ""
            # Check identifiers for policy/claim numbers
            for ident in r.get("identifier", []) or []:
                sys = str(ident.get("system") or "").lower()
                val = str(ident.get("value") or "").strip()
                ident_type = ident.get("type", {})
                codings = ident_type.get("coding", [{}]) if isinstance(ident_type, dict) else [{}]
                display = str(codings[0].get("display", "")).lower() if codings else ""
                if not kf.policy_number and ("abha" in sys or "insurance" in sys or "policy" in display):
                    kf.policy_number = val
                if not kf.claim_number and ("hospital" in sys or "patient-id" in sys):
                    kf.claim_number = val

        elif rtype == "Organization":
            if not kf.hospital_name:
                kf.hospital_name = r.get("name") or ""

        elif rtype == "Practitioner":
            if not kf.treating_doctor:
                names = r.get("name", []) or []
                if names:
                    kf.treating_doctor = names[0].get("text") or ""

        elif rtype == "DiagnosticReport":
            if not kf.primary_diagnosis and r.get("conclusion"):
                kf.primary_diagnosis = r["conclusion"]

        elif rtype == "Composition":
            # Extract sections and financial notes from composition sections
            for sec in r.get("section", []) or []:
                sec_title = str(sec.get("title") or "")
                sec_title_lower = sec_title.lower()
                div_text = sec.get("text", {}).get("div", "")

                items = re.findall(r'<li[^>]*>(.*?)</li>', div_text, re.IGNORECASE)
                cleaned_items = [re.sub(r'<[^>]+>', '', it).strip() for it in items if it.strip()]

                if not kf.primary_diagnosis and ("diagnos" in sec_title_lower or "clinical" in sec_title_lower):
                    if cleaned_items:
                        kf.primary_diagnosis = cleaned_items[0]

                # Parse document manifest into document_sections if currently empty
                if ("manifest" in sec_title_lower or "inventory" in sec_title_lower) and not summary.document_sections:
                    for it in cleaned_items:
                        m_doc = re.match(r'(?:[^(]+\()([^)]+)\):\s*(.*)', it)
                        if m_doc:
                            doc_title = m_doc.group(1).strip()
                            doc_sum = m_doc.group(2).strip()
                        else:
                            doc_title = sec_title
                            doc_sum = it
                        summary.document_sections.append(DocumentSection(
                            title=doc_title,
                            summary=doc_sum,
                            key_data_points=[it],
                        ))

                # Scan all items for financial and clinical data
                for it in cleaned_items:
                    _extract_financials_into_keyfields(kf, it)

        elif rtype == "Claim":
            tot = r.get("total", {})
            val = tot.get("value")
            if val is not None:
                amt = _clean_amount(str(val))
                if amt and not kf.gross_bill_amount:
                    kf.gross_bill_amount = amt
                if amt and not kf.total_claimed_amount:
                    kf.total_claimed_amount = amt

            if not kf.room_category:
                for item in r.get("item", []) or []:
                    cat_text = str(item.get("category", {}).get("text", "")).lower()
                    prod_text = str(item.get("productOrService", {}).get("text", ""))
                    prod_lower = prod_text.lower()
                    if ("room" in cat_text or "board" in cat_text or "accommodation" in cat_text) or any(w in prod_lower for w in ("ward", "room", "bed", "icu")):
                        clean_room = re.sub(r'\s*(?:charges|tariff|rent|fee|bill).*$', '', prod_text, flags=re.I).strip()
                        if clean_room and not any(w in clean_room.lower() for w in ("prescrib", "tab", "mg", "ward-")):
                            kf.room_category = clean_room
                            break

        elif rtype == "ClaimResponse":
            for tot in r.get("total", []) or []:
                codes = [c.get("code") for c in tot.get("category", {}).get("coding", [])]
                amt_val = tot.get("amount", {}).get("value")
                if amt_val is not None:
                    amt = _clean_amount(str(amt_val))
                    if amt:
                        if "submitted" in codes and not kf.gross_bill_amount:
                            kf.gross_bill_amount = amt
                        elif "benefit" in codes and not kf.approved_amount:
                            kf.approved_amount = amt
            if not kf.approved_amount and r.get("payment", {}).get("amount", {}).get("value"):
                kf.approved_amount = _clean_amount(str(r["payment"]["amount"]["value"]))
            if r.get("preAuthRef") and not kf.claim_number:
                kf.claim_number = str(r["preAuthRef"])

        elif rtype == "Coverage":
            for cls in r.get("class", []) or []:
                c_type = cls.get("type", {}).get("coding", [{}])[0].get("code", "")
                c_val = str(cls.get("value") or "")
                if c_type in ("sum_insured", "subplan") and c_val and not kf.sum_insured:
                    kf.sum_insured = _clean_amount(c_val)


def _populate_from_archive_info(summary: ClaimSummary, info: dict[str, Any]) -> None:
    """Fills fields from archive_info metadata."""
    kf = summary.key_fields
    if not kf.insured_name:
        kf.insured_name = info.get("patient_name") or ""
    if not kf.patient_gender:
        kf.patient_gender = info.get("gender") or ""
    if not kf.patient_age:
        kf.patient_age = info.get("birth_date") or ""


def _generate_claim_narrative(
    summary: ClaimSummary,
    gemini_extraction: Any | None,
    fhir_bundle: dict[str, Any] | None,
) -> None:
    """Uses Gemini to generate a concise executive claim narrative."""
    kf = summary.key_fields

    # Build context string from available data
    context_parts = []
    if kf.insured_name:
        context_parts.append(f"Insured: {kf.insured_name}")
    if kf.patient_gender:
        context_parts.append(f"Gender: {kf.patient_gender}")
    if kf.patient_age:
        context_parts.append(f"DOB/Age: {kf.patient_age}")
    if kf.primary_diagnosis:
        context_parts.append(f"Diagnosis: {kf.primary_diagnosis}")
    if kf.hospital_name:
        context_parts.append(f"Hospital: {kf.hospital_name}")
    if kf.treating_doctor:
        context_parts.append(f"Doctor: {kf.treating_doctor}")
    if kf.admission_date:
        context_parts.append(f"Admission: {kf.admission_date}")
    if kf.discharge_date:
        context_parts.append(f"Discharge: {kf.discharge_date}")
    if kf.total_claimed_amount:
        context_parts.append(f"Claimed Amount: {kf.total_claimed_amount}")
    if kf.claim_type:
        context_parts.append(f"Claim Type: {kf.claim_type}")

    # Add document sections info
    if summary.document_sections:
        sec_list = ", ".join(s.title for s in summary.document_sections[:8])
        context_parts.append(f"Documents: {sec_list}")

    # Add clinical notes
    if gemini_extraction and getattr(gemini_extraction, "clinical_notes", None):
        notes = gemini_extraction.clinical_notes[:5]
        context_parts.append(f"Clinical Notes: {'; '.join(str(n) for n in notes)}")

    if not context_parts:
        summary.claim_narrative = "Insurance claim document processed. Detailed data extracted and stored in FHIR format."
        return

    context_str = "\n".join(context_parts)

    try:
        client = _get_gemini_client()
        from google.genai import types

        prompt = f"""You are an insurance claim processing assistant. Based on the following extracted claim data, write a concise executive summary (3-4 sentences) of this insurance claim for a claims reviewer. Focus on: who the claimant is, what the medical condition/treatment was, where it was treated, and the financial aspects.

Extracted Data:
{context_str}

Write ONLY the narrative summary paragraph, no headers or labels."""

        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[prompt],
            config=types.GenerateContentConfig(
                temperature=0.3,
                max_output_tokens=300,
            ),
        )
        narrative = resp.text.strip()
        if narrative:
            summary.claim_narrative = narrative
        else:
            summary.claim_narrative = _build_fallback_narrative(summary)

    except Exception as exc:
        log.warning("Gemini narrative generation failed: %s", exc)
        summary.claim_narrative = _build_fallback_narrative(summary)


def _build_fallback_narrative(summary: ClaimSummary) -> str:
    """Builds a simple fallback narrative when Gemini is unavailable."""
    kf = summary.key_fields
    parts = []
    if kf.insured_name:
        parts.append(f"Insurance claim filed by {kf.insured_name}")
    else:
        parts.append("Insurance claim document processed")

    if kf.primary_diagnosis:
        parts.append(f"for {kf.primary_diagnosis}")

    if kf.hospital_name:
        parts.append(f"at {kf.hospital_name}")

    narrative = " ".join(parts) + "."

    if kf.total_claimed_amount:
        narrative += f" Total claimed amount: {kf.total_claimed_amount}."

    if kf.admission_date and kf.discharge_date:
        narrative += f" Hospitalization period: {kf.admission_date} to {kf.discharge_date}."

    num_docs = len(summary.document_sections)
    if num_docs:
        narrative += f" {num_docs} document section(s) identified and extracted."

    return narrative


def _generate_flags(summary: ClaimSummary) -> None:
    """Generates attention flags for the claim."""
    kf = summary.key_fields
    flags = []

    # Check for high-value claim
    if kf.total_claimed_amount:
        try:
            amt_str = re.sub(r'[^\d.]', '', kf.total_claimed_amount)
            if amt_str:
                amt = float(amt_str)
                if amt > 500000:
                    flags.append("High-Value Claim (> ₹5L)")
                if amt > 1000000:
                    flags.append("Very High-Value Claim (> ₹10L)")
        except (ValueError, TypeError):
            pass

    # Check for ICU mention in sections
    for sec in summary.document_sections:
        sec_text = (sec.title + " " + sec.summary).lower()
        if "icu" in sec_text or "intensive care" in sec_text:
            flags.append("ICU Stay Indicated")
            break

    # Check for surgical procedures
    for sec in summary.document_sections:
        sec_text = (sec.title + " " + sec.summary).lower()
        if any(k in sec_text for k in ("surgery", "surgical", "operation", "procedure")):
            flags.append("Surgical Procedure")
            break

    summary.flags = flags
