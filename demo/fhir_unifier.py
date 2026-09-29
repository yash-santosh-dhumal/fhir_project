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
ABDM_COVERAGE_PROFILE = "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Coverage"
ABDM_CLAIM_RESPONSE_PROFILE = (
    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/ClaimResponse"
)

def _safe_float(val: Any, default: float = 0.0) -> float:
    """Safely converts any value (string with commas, currency symbols, None, empty string) to float."""
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        val_clean = re.sub(r"[^\d.-]", "", val).strip()
        if not val_clean or val_clean in ("-", ".", "-.", ".-"):
            return default
        try:
            return float(val_clean)
        except (ValueError, TypeError):
            return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

_LOINC_CACHE: dict[str, tuple[str, str]] | None = None
_LOINC_DB_CONN: Any | None = None


def _build_loinc_database_from_zip(zip_path: Path, db_path: Path) -> None:
    """Builds a fast queryable SQLite LOINC database from Loinc_2.83.zip."""
    import csv
    import io
    import sqlite3
    import zipfile

    log.info("Building LOINC 2.83 SQLite database from %s...", zip_path)
    temp_db = db_path.with_suffix(".tmp")
    if temp_db.exists():
        try:
            temp_db.unlink()
        except OSError:
            pass

    conn = sqlite3.connect(str(temp_db))
    c = conn.cursor()
    c.execute("PRAGMA synchronous = OFF")
    c.execute("PRAGMA journal_mode = MEMORY")
    c.execute("""
        CREATE TABLE loinc_terms (
            term TEXT NOT NULL,
            loinc_num TEXT NOT NULL,
            long_common_name TEXT NOT NULL,
            rank INTEGER DEFAULT 999999,
            is_lab INTEGER DEFAULT 0
        )
    """)

    with zipfile.ZipFile(zip_path) as z:
        with z.open("LoincTable/Loinc.csv") as f:
            reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
            ignore_terms = {
                "level", "point in time", "qnt", "quan", "quant", "quantitative",
                "screen", "pl", "plasma", "plsm", "ser", "serum", "bld", "blood",
                "urn", "urine", "chemistry", "hematology", "cell counts", "pathology",
            }
            batch = []
            seen = set()

            def _add(t_str: str, num_str: str, name_str: str, r_val: int, lab_flag: int):
                clean = t_str.strip().lower()
                if not clean or len(clean) < 2 or clean in ignore_terms or len(clean) > 90:
                    return
                key = (clean, num_str)
                if key in seen:
                    return
                seen.add(key)
                batch.append((clean, num_str, name_str, r_val, lab_flag))

            for row in reader:
                if row.get("STATUS") != "ACTIVE":
                    continue
                num = (row.get("LOINC_NUM") or "").strip()
                long_name = (row.get("LONG_COMMON_NAME") or "").strip()
                comp = (row.get("COMPONENT") or "").strip()
                short = (row.get("SHORTNAME") or "").strip()
                disp = (row.get("DisplayName") or "").strip()
                related = (row.get("RELATEDNAMES2") or "").strip()
                rank_str = (row.get("COMMON_TEST_RANK") or "").strip()
                rank = int(rank_str) if rank_str.isdigit() and int(rank_str) > 0 else 999999
                is_lab = 1 if row.get("CLASSTYPE") == "1" else 0

                if comp:
                    _add(comp, num, long_name, rank, is_lab)
                    if "." in comp:
                        _add(comp.replace(".", " "), num, long_name, rank, is_lab)
                if long_name:
                    _add(long_name, num, long_name, rank, is_lab)
                if short:
                    _add(short, num, long_name, rank, is_lab)
                if disp and "[" not in disp:
                    _add(disp, num, long_name, rank, is_lab)
                for s in related.split(";"):
                    _add(s, num, long_name, rank, is_lab)

    clinical_aliases = [
        ("hemoglobin", "718-7", "Hemoglobin [Mass/volume] in Blood", 1, 1),
        ("hb", "718-7", "Hemoglobin [Mass/volume] in Blood", 1, 1),
        ("hgb", "718-7", "Hemoglobin [Mass/volume] in Blood", 1, 1),
        ("haemoglobin", "718-7", "Hemoglobin [Mass/volume] in Blood", 1, 1),
        ("total leukocyte count", "6690-2", "Leukocytes [#/volume] in Blood", 1, 1),
        ("total leucocyte count", "6690-2", "Leukocytes [#/volume] in Blood", 1, 1),
        ("tlc", "6690-2", "Leukocytes [#/volume] in Blood", 1, 1),
        ("white blood cells", "6690-2", "Leukocytes [#/volume] in Blood", 1, 1),
        ("wbc", "6690-2", "Leukocytes [#/volume] in Blood", 1, 1),
        ("platelet count", "777-3", "Platelets [#/volume] in Blood", 1, 1),
        ("platelets", "777-3", "Platelets [#/volume] in Blood", 1, 1),
        ("plt", "777-3", "Platelets [#/volume] in Blood", 1, 1),
        ("red blood cells", "789-8", "Erythrocytes [#/volume] in Blood", 1, 1),
        ("rbc", "789-8", "Erythrocytes [#/volume] in Blood", 1, 1),
        ("erythrocytes", "789-8", "Erythrocytes [#/volume] in Blood", 1, 1),
        ("packed cell volume", "4544-3", "Hematocrit [Volume Fraction] of Blood", 1, 1),
        ("pcv", "4544-3", "Hematocrit [Volume Fraction] of Blood", 1, 1),
        ("hematocrit", "4544-3", "Hematocrit [Volume Fraction] of Blood", 1, 1),
        ("hct", "4544-3", "Hematocrit [Volume Fraction] of Blood", 1, 1),
        ("creatinine", "2160-0", "Creatinine [Mass/volume] in Serum or Plasma", 1, 1),
        ("serum creatinine", "2160-0", "Creatinine [Mass/volume] in Serum or Plasma", 1, 1),
        ("blood urea", "3094-0", "Urea nitrogen [Mass/volume] in Serum or Plasma", 1, 1),
        ("urea", "3094-0", "Urea nitrogen [Mass/volume] in Serum or Plasma", 1, 1),
        ("bun", "3094-0", "Urea nitrogen [Mass/volume] in Serum or Plasma", 1, 1),
        ("blood urea nitrogen", "3094-0", "Urea nitrogen [Mass/volume] in Serum or Plasma", 1, 1),
        ("blood glucose", "1558-6", "Glucose [Mass/volume] in Serum or Plasma", 1, 1),
        ("fasting blood sugar", "1558-6", "Glucose [Mass/volume] in Serum or Plasma", 1, 1),
        ("fbs", "1558-6", "Glucose [Mass/volume] in Serum or Plasma", 1, 1),
        ("random blood sugar", "1558-6", "Glucose [Mass/volume] in Serum or Plasma", 1, 1),
        ("rbs", "1558-6", "Glucose [Mass/volume] in Serum or Plasma", 1, 1),
        ("blood sugar", "1558-6", "Glucose [Mass/volume] in Serum or Plasma", 1, 1),
        ("glucose", "1558-6", "Glucose [Mass/volume] in Serum or Plasma", 1, 1),
        ("bilirubin total", "1975-2", "Bilirubin.total [Mass/volume] in Serum or Plasma", 1, 1),
        ("total bilirubin", "1975-2", "Bilirubin.total [Mass/volume] in Serum or Plasma", 1, 1),
        ("bilirubin direct", "1968-7", "Bilirubin.direct [Mass/volume] in Serum or Plasma", 1, 1),
        ("direct bilirubin", "1968-7", "Bilirubin.direct [Mass/volume] in Serum or Plasma", 1, 1),
        ("sgot", "1920-8", "Aspartate aminotransferase [Enzymatic activity/volume] in Serum or Plasma", 1, 1),
        ("ast", "1920-8", "Aspartate aminotransferase [Enzymatic activity/volume] in Serum or Plasma", 1, 1),
        ("sgpt", "1742-6", "Alanine aminotransferase [Enzymatic activity/volume] in Serum or Plasma", 1, 1),
        ("alt", "1742-6", "Alanine aminotransferase [Enzymatic activity/volume] in Serum or Plasma", 1, 1),
        ("hba1c", "4548-4", "Hemoglobin A1c/Hemoglobin.total in Blood", 1, 1),
        ("glycated hemoglobin", "4548-4", "Hemoglobin A1c/Hemoglobin.total in Blood", 1, 1),
        ("esr", "4537-7", "Erythrocyte sedimentation rate by Westergren method", 1, 1),
        ("vitamin d", "62292-8", "25-hydroxyvitamin D3 [Mass/volume] in Serum or Plasma", 1, 1),
        ("vitamin b12", "2132-9", "Cobalamin (Vitamin B12) [Mass/volume] in Serum or Plasma", 1, 1),
        ("serum calcium", "17861-6", "Calcium [Mass/volume] in Serum or Plasma", 1, 1),
        ("calcium", "17861-6", "Calcium [Mass/volume] in Serum or Plasma", 1, 1),
        ("uric acid", "3084-1", "Uric acid [Mass/volume] in Serum or Plasma", 1, 1),
        ("mcv", "787-2", "MCV [Entitic volume] by Automated count", 1, 1),
        ("mch", "785-6", "MCH [Entitic mass] by Automated count", 1, 1),
        ("mchc", "786-4", "MCHC [Mass/volume] by Automated count", 1, 1),
        ("rdw", "788-0", "Erythrocyte distribution width [Ratio] by Automated count", 1, 1),
        ("neutrophils", "770-8", "Neutrophils/100 leukocytes in Blood", 1, 1),
        ("lymphocytes", "736-9", "Lymphocytes/100 leukocytes in Blood", 1, 1),
        ("monocytes", "5905-5", "Monocytes/100 leukocytes in Blood", 1, 1),
        ("eosinophils", "713-8", "Eosinophils/100 leukocytes in Blood", 1, 1),
        ("basophils", "706-2", "Basophils/100 leukocytes in Blood", 1, 1),
        ("absolute neutrophil count", "751-8", "Neutrophils [#/volume] in Blood", 1, 1),
        ("anc", "751-8", "Neutrophils [#/volume] in Blood", 1, 1),
        ("absolute lymphocyte count", "731-0", "Lymphocytes [#/volume] in Blood", 1, 1),
        ("alc", "731-0", "Lymphocytes [#/volume] in Blood", 1, 1),
        ("absolute monocyte count", "742-7", "Monocytes [#/volume] in Blood", 1, 1),
        ("amc", "742-7", "Monocytes [#/volume] in Blood", 1, 1),
        ("absolute eosinophil count", "711-2", "Eosinophils [#/volume] in Blood", 1, 1),
        ("aec", "711-2", "Eosinophils [#/volume] in Blood", 1, 1),
        ("absolute basophil count", "704-7", "Basophils [#/volume] in Blood", 1, 1),
        ("abc", "704-7", "Basophils [#/volume] in Blood", 1, 1),
        # Bone Marrow / Cytology / Pathology
        ("site", "39111-0", "Body site", 1, 1),
        ("biopsy site", "39111-0", "Body site", 1, 1),
        ("cellularity", "74232-0", "Cellularity assessment in Bone marrow Narrative", 1, 1),
        ("m:e ratio", "11138-5", "Myeloid cells/Erythroid cells [# Ratio] in Bone marrow", 1, 1),
        ("me ratio", "11138-5", "Myeloid cells/Erythroid cells [# Ratio] in Bone marrow", 1, 1),
        ("m/e ratio", "11138-5", "Myeloid cells/Erythroid cells [# Ratio] in Bone marrow", 1, 1),
        ("myeloid/erythroid ratio", "11138-5", "Myeloid cells/Erythroid cells [# Ratio] in Bone marrow", 1, 1),
        ("erythropoiesis", "74231-2", "Erythropoiesis assessment in Bone marrow Narrative", 1, 1),
        ("myelopoiesis", "74228-8", "Myelopoiesis assessment in Bone marrow Narrative", 1, 1),
        ("granulopoiesis", "74228-8", "Myelopoiesis assessment in Bone marrow Narrative", 1, 1),
        ("blasts", "11150-0", "Blasts/cells in Bone marrow", 1, 1),
        ("blast cells", "11150-0", "Blasts/cells in Bone marrow", 1, 1),
        ("megakaryopoiesis", "74229-6", "Megakaryopoiesis assessment in Bone marrow Narrative", 1, 1),
        ("megakaryocytes", "40688-4", "Megakaryocytes [Presence] in Bone marrow by Microscopy", 1, 1),
        ("plasma cells", "74226-2", "Plasma cells assessment in Bone marrow Narrative", 1, 1),
        ("re cells", "44723-5", "Histiocytes [Presence] in Bone marrow by Light microscopy", 1, 1),
        ("reticuloendothelial cells", "44723-5", "Histiocytes [Presence] in Bone marrow by Light microscopy", 1, 1),
        ("histiocytes", "44723-5", "Histiocytes [Presence] in Bone marrow by Light microscopy", 1, 1),
        ("granulomas", "10355-6", "Microscopic observation [Identifier] in Bone marrow by Wright Giemsa stain", 1, 1),
        ("haemoparasites", "53609-4", "Parasite identified in Bone marrow by Light microscopy", 1, 1),
        ("hemoparasites", "53609-4", "Parasite identified in Bone marrow by Light microscopy", 1, 1),
        ("no granulomas / haemoparasites", "10355-6", "Microscopic observation [Identifier] in Bone marrow by Wright Giemsa stain", 1, 1),
        ("pearls stain", "101225-1", "Hemosiderin [Presence] in Blood or Marrow by Prussian blue stain", 1, 1),
        ("perls stain", "101225-1", "Hemosiderin [Presence] in Blood or Marrow by Prussian blue stain", 1, 1),
        ("prussian blue stain", "101225-1", "Hemosiderin [Presence] in Blood or Marrow by Prussian blue stain", 1, 1),
        ("iron stain", "101225-1", "Hemosiderin [Presence] in Blood or Marrow by Prussian blue stain", 1, 1),
        ("bone marrow aspiration study", "48807-2", "Bone marrow aspiration report", 1, 1),
        ("bone marrow aspiration", "48807-2", "Bone marrow aspiration report", 1, 1),
        ("bone marrow biopsy", "33721-2", "Bone marrow Pathology biopsy report", 1, 1),
        ("large biopsy", "52121-1", "Biopsy [Interpretation] in Specimen Narrative", 1, 1),
        ("biopsy report", "52121-1", "Biopsy [Interpretation] in Specimen Narrative", 1, 1),
        ("impression", "8251-1", "Service comment", 1, 1),
        ("lymphocytes/100 leukocytes in blood", "736-9", "Lymphocytes/100 leukocytes in Blood", 1, 1),
        ("lymphocytes/100 leukocytes", "736-9", "Lymphocytes/100 leukocytes in Blood", 1, 1),
        ("d. bilirubin", "1968-7", "Bilirubin.direct [Mass/volume] in Serum or Plasma", 1, 1),
        ("d bilirubin", "1968-7", "Bilirubin.direct [Mass/volume] in Serum or Plasma", 1, 1),
        ("t. bilirubin", "1975-2", "Bilirubin.total [Mass/volume] in Serum or Plasma", 1, 1),
        ("t bilirubin", "1975-2", "Bilirubin.total [Mass/volume] in Serum or Plasma", 1, 1),
        ("s. creatinine", "2160-0", "Creatinine [Mass/volume] in Serum or Plasma", 1, 1),
        ("s creatinine", "2160-0", "Creatinine [Mass/volume] in Serum or Plasma", 1, 1),
        ("hb", "718-7", "Hemoglobin [Mass/volume] in Blood", 1, 1),
        ("tlc", "6690-2", "Leukocytes [#/volume] in Blood", 1, 1),
        ("lymphocytes, plasma cells and re cells", "10355-6", "Microscopic observation [Identifier] in Bone marrow by Wright Giemsa stain", 1, 1),
        ("granulomas / haemoparasites", "10355-6", "Microscopic observation [Identifier] in Bone marrow by Wright Giemsa stain", 1, 1),
        ("granulomas / hemoparasites", "10355-6", "Microscopic observation [Identifier] in Bone marrow by Wright Giemsa stain", 1, 1),
        ("hiv", "88453-6", "HIV 1 RNA+Hepatitis C virus RNA+Hepatitis B virus DNA [Presence] in Serum, Plasma or Blood from Donor by NAA with probe detection", 1, 1),
        ("hbsag", "5195-3", "Hepatitis B virus surface Ag [Presence] in Serum", 1, 1),
        ("hcv", "16128-1", "Hepatitis C virus Ab [Presence] in Serum", 1, 1),
    ]
    for item in clinical_aliases:
        batch.append(item)

    c.executemany(
        "INSERT INTO loinc_terms (term, loinc_num, long_common_name, rank, is_lab) VALUES (?, ?, ?, ?, ?)",
        batch
    )
    c.execute("CREATE INDEX idx_term ON loinc_terms(term)")
    conn.commit()
    conn.close()

    temp_db.replace(db_path)
    log.info("LOINC 2.83 SQLite database built successfully (%d terms).", len(batch))


def _get_loinc_db_connection():
    """Returns a connection to the LOINC 2.83 SQLite database."""
    global _LOINC_DB_CONN
    if _LOINC_DB_CONN is not None:
        return _LOINC_DB_CONN

    import sqlite3
    data_dir = Path(__file__).resolve().parent.parent / "data"
    db_path = data_dir / "loinc_283.db"
    zip_path = data_dir / "Loinc_2.83.zip"

    if not db_path.exists() and zip_path.exists():
        try:
            _build_loinc_database_from_zip(zip_path, db_path)
        except Exception as exc:
            log.warning("Could not build loinc_283.db from zip: %s", exc)

    if db_path.exists():
        try:
            _LOINC_DB_CONN = sqlite3.connect(str(db_path), check_same_thread=False)
            return _LOINC_DB_CONN
        except Exception as exc:
            log.warning("Could not open loinc_283.db: %s", exc)

    return None


def _get_loinc_mapping() -> dict[str, tuple[str, str]]:
    """Loads LOINC number and display name mapping from local knowledge base."""
    global _LOINC_CACHE
    if _LOINC_CACHE is not None:
        return _LOINC_CACHE

    mapping: dict[str, tuple[str, str]] = {
        "blood group": ("883-9", "ABO and Rh group [Type] in Blood"),
        "rh factor": ("10331-7", "Rh [Type] in Blood"),
        "abo group": ("883-9", "ABO group [Type] in Blood"),
        "site": ("39111-0", "Body site"),
        "biopsy site": ("39111-0", "Body site"),
        "cellularity": ("74232-0", "Cellularity assessment in Bone marrow Narrative"),
        "m:e ratio": ("11138-5", "Myeloid cells/Erythroid cells [# Ratio] in Bone marrow"),
        "me ratio": ("11138-5", "Myeloid cells/Erythroid cells [# Ratio] in Bone marrow"),
        "m/e ratio": ("11138-5", "Myeloid cells/Erythroid cells [# Ratio] in Bone marrow"),
        "myeloid/erythroid ratio": ("11138-5", "Myeloid cells/Erythroid cells [# Ratio] in Bone marrow"),
        "erythropoiesis": ("74231-2", "Erythropoiesis assessment in Bone marrow Narrative"),
        "myelopoiesis": ("74228-8", "Myelopoiesis assessment in Bone marrow Narrative"),
        "granulopoiesis": ("74228-8", "Myelopoiesis assessment in Bone marrow Narrative"),
        "blasts": ("11150-0", "Blasts/cells in Bone marrow"),
        "blast cells": ("11150-0", "Blasts/cells in Bone marrow"),
        "megakaryopoiesis": ("74229-6", "Megakaryopoiesis assessment in Bone marrow Narrative"),
        "megakaryocytes": ("40688-4", "Megakaryocytes [Presence] in Bone marrow by Microscopy"),
        "plasma cells": ("74226-2", "Plasma cells assessment in Bone marrow Narrative"),
        "re cells": ("44723-5", "Histiocytes [Presence] in Bone marrow by Light microscopy"),
        "reticuloendothelial cells": ("44723-5", "Histiocytes [Presence] in Bone marrow by Light microscopy"),
        "histiocytes": ("44723-5", "Histiocytes [Presence] in Bone marrow by Light microscopy"),
        "granulomas": ("10355-6", "Microscopic observation [Identifier] in Bone marrow by Wright Giemsa stain"),
        "haemoparasites": ("53609-4", "Parasite identified in Bone marrow by Light microscopy"),
        "hemoparasites": ("53609-4", "Parasite identified in Bone marrow by Light microscopy"),
        "no granulomas / haemoparasites": ("10355-6", "Microscopic observation [Identifier] in Bone marrow by Wright Giemsa stain"),
        "pearls stain": ("101225-1", "Hemosiderin [Presence] in Blood or Marrow by Prussian blue stain"),
        "perls stain": ("101225-1", "Hemosiderin [Presence] in Blood or Marrow by Prussian blue stain"),
        "prussian blue stain": ("101225-1", "Hemosiderin [Presence] in Blood or Marrow by Prussian blue stain"),
        "iron stain": ("101225-1", "Hemosiderin [Presence] in Blood or Marrow by Prussian blue stain"),
        "bone marrow aspiration study": ("48807-2", "Bone marrow aspiration report"),
        "bone marrow aspiration": ("48807-2", "Bone marrow aspiration report"),
        "bone marrow biopsy": ("33721-2", "Bone marrow Pathology biopsy report"),
        "large biopsy": ("52121-1", "Biopsy [Interpretation] in Specimen Narrative"),
        "biopsy report": ("52121-1", "Biopsy [Interpretation] in Specimen Narrative"),
        "impression": ("8251-1", "Service comment"),
        "lymphocytes/100 leukocytes in blood": ("736-9", "Lymphocytes/100 leukocytes in Blood"),
        "lymphocytes/100 leukocytes": ("736-9", "Lymphocytes/100 leukocytes in Blood"),
        "d. bilirubin": ("1968-7", "Bilirubin.direct [Mass/volume] in Serum or Plasma"),
        "d bilirubin": ("1968-7", "Bilirubin.direct [Mass/volume] in Serum or Plasma"),
        "t. bilirubin": ("1975-2", "Bilirubin.total [Mass/volume] in Serum or Plasma"),
        "t bilirubin": ("1975-2", "Bilirubin.total [Mass/volume] in Serum or Plasma"),
        "s. creatinine": ("2160-0", "Creatinine [Mass/volume] in Serum or Plasma"),
        "s creatinine": ("2160-0", "Creatinine [Mass/volume] in Serum or Plasma"),
        "hb": ("718-7", "Hemoglobin [Mass/volume] in Blood"),
        "tlc": ("6690-2", "Leukocytes [#/volume] in Blood"),
        "lymphocytes, plasma cells and re cells": ("10355-6", "Microscopic observation [Identifier] in Bone marrow by Wright Giemsa stain"),
        "granulomas / haemoparasites": ("10355-6", "Microscopic observation [Identifier] in Bone marrow by Wright Giemsa stain"),
        "granulomas / hemoparasites": ("10355-6", "Microscopic observation [Identifier] in Bone marrow by Wright Giemsa stain"),
        "hiv": ("88453-6", "HIV 1 RNA+Hepatitis C virus RNA+Hepatitis B virus DNA [Presence] in Serum, Plasma or Blood from Donor by NAA with probe detection"),
        "hbsag": ("5195-3", "Hepatitis B virus surface Ag [Presence] in Serum"),
        "hcv": ("16128-1", "Hepatitis C virus Ab [Presence] in Serum"),
    }
    mapping_ranks: dict[str, int] = {k: 1 for k in mapping}

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
                    rank_str = (row.get("COMMON_TEST_RANK") or "").strip()
                    rank_val = int(rank_str) if rank_str.isdigit() else 99999
                    if code and core:
                        core_k = core.lower()
                        if core_k not in mapping or rank_val < mapping_ranks.get(core_k, 99999):
                            mapping[core_k] = (code, long_name)
                            mapping_ranks[core_k] = rank_val
                    syns_str = row.get("synonyms")
                    if syns_str:
                        try:
                            syns = ast.literal_eval(syns_str)
                            for s in syns:
                                s_k = str(s).strip().lower()
                                if s_k and (s_k not in mapping or rank_val < mapping_ranks.get(s_k, 99999)):
                                    mapping[s_k] = (code, long_name)
                                    mapping_ranks[s_k] = rank_val
                        except Exception:
                            pass
        except Exception as exc:
            log.warning("Could not load LOINC KB: %s", exc)

    _LOINC_CACHE = mapping
    return mapping


def _lookup_loinc(test_name: str) -> tuple[str, str] | None:
    """Finds best matching LOINC code and display name for a test analyte from LOINC 2.83."""
    if not test_name:
        return None
    t_lower = test_name.strip().lower()

    # Generate search variations: exact, stripped of parentheses, inside parentheses, colon-normalized
    variations = [t_lower]
    t_colon_norm = re.sub(r"\s*([:/])\s*", r"\1", t_lower).strip()
    if t_colon_norm and t_colon_norm not in variations:
        variations.append(t_colon_norm)
    t_clean = re.sub(r"\(.*?\)", "", t_lower).strip()
    if t_clean and t_clean != t_lower and t_clean not in variations:
        variations.append(t_clean)
    inside_terms = re.findall(r"\((.*?)\)", t_lower)
    for in_term in inside_terms:
        in_t = in_term.strip()
        if in_t and in_t not in variations:
            variations.append(in_t)
    t_norm = re.sub(r"[^a-z0-9\s]+", " ", t_lower).strip()
    if t_norm and t_norm not in variations:
        variations.append(t_norm)

    # 1. Fast indexed search in LOINC 2.83 SQLite database
    conn = _get_loinc_db_connection()
    if conn:
        try:
            cursor = conn.cursor()
            for v in variations:
                row = cursor.execute(
                    "SELECT loinc_num, long_common_name FROM loinc_terms WHERE term=? ORDER BY is_lab DESC, rank ASC LIMIT 1",
                    (v,)
                ).fetchone()
                if row:
                    return (row[0], row[1])
        except Exception as exc:
            log.debug("LOINC DB query error: %s", exc)

    # 2. Fall back to cached mapping from analyte_records_top_2000.csv / default map
    mapping = _get_loinc_mapping()
    for v in variations:
        if v in mapping:
            return mapping[v]
    candidates = []
    for k, v in mapping.items():
        if len(k) >= 4 and (k == t_lower or k in t_lower or t_lower in k):
            candidates.append((len(k), v))
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]
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


def _build_claim_resource(
    billing: Any,
    patient_ref: str,
    organization_ref: str,
) -> dict[str, Any]:
    """Constructs a FHIR Claim resource from extracted billing data."""
    b = billing.to_dict() if hasattr(billing, "to_dict") else (billing or {})
    claim_id = str(uuid.uuid4())

    items_list = b.get("items", []) or []
    fhir_items = []
    for seq, item in enumerate(items_list, 1):
        desc = item.get("description", "Charge")
        qty = item.get("quantity", 1) or 1
        unit_price = _safe_float(item.get("unit_price", 0))
        amount = _safe_float(item.get("amount", 0))
        category = item.get("category", "General")

        fhir_item: dict[str, Any] = {
            "sequence": seq,
            "productOrService": {
                "text": desc,
            },
            "quantity": {"value": qty},
            "unitPrice": {
                "value": unit_price,
                "currency": "INR",
            },
            "net": {
                "value": amount,
                "currency": "INR",
            },
            "category": {
                "text": category,
            },
        }
        fhir_items.append(fhir_item)

    total_amount = _safe_float(b.get("total_amount", 0))
    bill_number = b.get("bill_number", "") or ""
    bill_date = b.get("bill_date", "") or ""
    payment_mode = b.get("payment_mode", "") or ""
    src_doc = b.get("source_document", "") or ""

    claim: dict[str, Any] = {
        "resourceType": "Claim",
        "id": claim_id,
        "status": "active",
        "type": {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/claim-type",
                    "code": "institutional",
                    "display": "Institutional",
                }
            ]
        },
        "use": "claim",
        "patient": {"reference": patient_ref},
        "provider": {"reference": organization_ref},
        "priority": {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/processpriority",
                    "code": "normal",
                }
            ]
        },
        "total": {
            "value": total_amount,
            "currency": "INR",
        },
        "item": fhir_items,
    }

    # Add bill identifiers as extensions / notes
    notes = []
    if bill_number:
        claim["identifier"] = [{
            "system": "http://hospital.local/bill-number",
            "value": bill_number,
        }]
        notes.append(f"Bill Number: {bill_number}")
    if bill_date:
        notes.append(f"Bill Date: {bill_date}")
    if payment_mode:
        notes.append(f"Payment Mode: {payment_mode}")
    if src_doc:
        notes.append(f"Source document: {src_doc}")
    if notes:
        claim["supportingInfo"] = [{
            "sequence": 1,
            "category": {
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/claiminformationcategory",
                    "code": "info",
                    "display": "Information",
                }]
            },
            "valueString": " | ".join(notes),
        }]

    return claim


def _build_coverage_resource(
    policy: Any,
    patient_ref: str,
    organization_ref: str,
) -> dict[str, Any]:
    """Constructs a normative FHIR R4 Coverage resource from extracted insurance policy data."""
    cov_id = str(uuid.uuid4())
    scheme_name = getattr(policy, "scheme_or_insurer", "") or "Health Insurance Scheme"
    policy_num = getattr(policy, "policy_number", "") or getattr(policy, "health_card_number", "") or "N/A"
    cov_type = getattr(policy, "coverage_type", "") or "Cashless Health Insurance"
    sum_ins = _safe_float(getattr(policy, "annual_sum_insured", 0.0))
    copay = _safe_float(getattr(policy, "copayment_liability", 0.0))
    copay_pct = _safe_float(getattr(policy, "copayment_percentage", 0.0))
    covered_cats = getattr(policy, "covered_categories", []) or []
    terms = getattr(policy, "terms_and_rules", "") or ""

    is_public = any(k in scheme_name.lower() for k in ("gov", "pmjay", "aarogyasri", "trust", "yojana"))

    cov: dict[str, Any] = {
        "resourceType": "Coverage",
        "id": cov_id,
        "meta": {
            "profile": [ABDM_COVERAGE_PROFILE],
        },
        "status": "active",
        "type": {
            "coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                "code": "PUBLICPOL" if is_public else "HIP",
                "display": cov_type,
            }],
            "text": f"{scheme_name} ({cov_type})",
        },
        "beneficiary": {"reference": patient_ref},
        "payor": [{
            "reference": organization_ref,
            "display": scheme_name,
        }],
        "subscriberId": policy_num,
    }

    if policy_num and policy_num != "N/A":
        cov["identifier"] = [{
            "system": "https://healthid.ndhm.gov.in/policy",
            "value": policy_num,
        }]

    # Cost to beneficiary (Copayment / Deductible)
    cov["costToBeneficiary"] = [{
        "type": {
            "coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/coverage-copay-type",
                "code": "copay",
                "display": "Co-payment / Deductible",
            }]
        },
        "valueMoney": {
            "value": copay,
            "currency": "INR",
        }
    }]

    # Class (Plan details, sum insured, and policy metadata for reconstruction)
    classes = [{
        "type": {
            "coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/coverage-class",
                "code": "plan",
                "display": "Insurance Plan / Scheme",
            }]
        },
        "value": policy_num,
        "name": f"{scheme_name} ({cov_type})",
    }]
    if sum_ins > 0:
        classes.append({
            "type": {
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/coverage-class",
                    "code": "sum_insured",
                    "display": "Sum Insured Limit",
                }]
            },
            "value": str(sum_ins),
            "name": "Annual Family Floater Sum Insured",
        })
    if copay_pct > 0:
        classes.append({
            "type": {
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/coverage-class",
                    "code": "copay_percentage",
                    "display": "Co-Payment Percentage",
                }]
            },
            "value": str(copay_pct),
            "name": f"{copay_pct}% Beneficiary Co-Payment",
        })
    if covered_cats:
        classes.append({
            "type": {
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/coverage-class",
                    "code": "covered_categories",
                    "display": "Covered Treatment Categories",
                }]
            },
            "value": ", ".join(covered_cats),
            "name": "Covered Benefit Heads",
        })
    if terms:
        classes.append({
            "type": {
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/coverage-class",
                    "code": "terms_and_rules",
                    "display": "Policy Terms & Coverage Rules",
                }]
            },
            "value": terms,
            "name": "Insurance Policy Terms",
        })
    cov["class"] = classes

    return cov


def _adjudicate_bill_against_policy(
    bills: list[Any],
    policy: Any,
) -> dict[str, Any]:
    """Calculates insured amount vs patient out-of-pocket payable by cross-referencing bill and policy.

    Handles both insurance documents that specify exact rupee amounts AND
    documents that only describe coverage rules/categories without amounts.
    When exact amounts are missing, automatically calculates from the bill
    using copayment percentage and covered category matching.
    """
    # Prioritize demo bill if present, per requirement: "from the demo bill how much amount is insured"
    demo_bills = [
        b for b in bills 
        if "demo bill" in (getattr(b, "source_document", "") or "").lower()
        or "demo_bill" in (getattr(b, "source_document", "") or "").lower()
        or "hospital_bill" in (getattr(b, "source_document", "") or "").lower()
    ]
    target_bills = demo_bills if demo_bills else bills

    all_bill_items: list[Any] = []
    total_billed = 0.0
    bill_items_by_category: dict[str, float] = {}
    for b in target_bills:
        items = getattr(b, "items", []) or []
        all_bill_items.extend(items)
        amt = _safe_float(getattr(b, "total_amount", 0.0))
        items_sum = sum(_safe_float(getattr(it, "amount", 0.0)) for it in items)
        if items_sum > 0:
            total_billed += items_sum
        elif amt > 0:
            total_billed += amt
        for it in items:
            cat = (getattr(it, "category", "") or "General").strip()
            item_amt = _safe_float(getattr(it, "amount", 0.0))
            bill_items_by_category[cat] = bill_items_by_category.get(cat, 0.0) + item_amt

    pre_auth_appr = _safe_float(getattr(policy, "pre_auth_approved_amount", 0.0))
    policy_patient_oop = _safe_float(getattr(policy, "patient_out_of_pocket", 0.0))
    copay_liab = _safe_float(getattr(policy, "copayment_liability", 0.0))
    copay_pct = _safe_float(getattr(policy, "copayment_percentage", 0.0))
    sum_insured = _safe_float(getattr(policy, "annual_sum_insured", 0.0))
    cov_type = (getattr(policy, "coverage_type", "") or "").lower()
    terms_text = (getattr(policy, "terms_and_rules", "") or "").lower()
    scheme = getattr(policy, "scheme_or_insurer", "") or "Insurance Policy"
    policy_num = getattr(policy, "policy_number", "") or "N/A"
    preauth_num = getattr(policy, "claim_or_preauth_number", "") or ""
    covered_categories = getattr(policy, "covered_categories", []) or []
    excluded_categories = getattr(policy, "excluded_categories", []) or []
    deductible_amount = _safe_float(getattr(policy, "deductible_amount", 0.0))
    room_rent_sublimit = _safe_float(getattr(policy, "room_rent_sublimit", 0.0))

    # Also try to extract deductible from terms text if not set via field
    import re
    if deductible_amount == 0.0 and terms_text:
        ded_match = re.search(r'deductible[:\s]*(?:inr|rs\.?|₹)?\s*([\d,]+(?:\.\d+)?)', terms_text)
        if ded_match:
            deductible_amount = _safe_float(ded_match.group(1))

    # Detect ICU sub-limit from terms text if present
    icu_sublimit = 0.0
    if terms_text:
        # Look for explicit ICU daily rate patterns like "INR 2,000 per day for ICU"
        # or "ICU/ICCU ... INR 2000" but avoid picking up deductible amounts
        icu_patterns = [
            r'(?:inr|rs\.?|₹)\s*([\d,]+(?:\.\d+)?)\s*(?:per\s*day|/\s*day|daily)?\s*(?:for\s*)?(?:icu|iccu)',
            r'icu[/\s]*(?:iccu)?[^.]*?(?:inr|rs\.?|₹)\s*([\d,]+(?:\.\d+)?)\s*(?:per\s*day|/\s*day|daily)',
            r'(?:inr|rs\.?|₹)?\s*([\d,]+(?:\.\d+)?)\s*(?:per\s*day|/\s*day|daily)\s*(?:for\s*)?(?:icu|iccu)',
        ]
        for pat in icu_patterns:
            m = re.search(pat, terms_text)
            if m:
                candidate = _safe_float(m.group(1))
                # Sanity check: ICU sublimit is typically 1000-10000, not a sum insured or deductible
                if 100 <= candidate <= 50000:
                    icu_sublimit = candidate
                    break
    if icu_sublimit == 0.0 and room_rent_sublimit > 0:
        icu_sublimit = room_rent_sublimit * 4.0  # standard industry ratio (e.g. 500 ward -> 2000 ICU)


    # Normalize copay_liab: if it looks like a ratio/percentage (< 100) rather than
    # an actual rupee amount, treat it as 0 and rely on copay_pct instead
    if 0 < copay_liab < 100:
        copay_liab = 0.0

    # Detect coverage percentage from policy terms
    # Check for explicit percentage-based coverage rules (e.g. 80:20, 70:30, 90:10)
    coverage_split_pct = 0.0  # insurer's share percentage
    if copay_pct > 0:
        coverage_split_pct = 100.0 - copay_pct
    else:
        # Try to extract from terms/coverage type text
        for text in (terms_text, cov_type):
            m = re.search(r'(\d{2,3})\s*[:%]\s*(?:insurer|coverage|covered|cashless)', text)
            if m:
                coverage_split_pct = _safe_float(m.group(1))
                copay_pct = 100.0 - coverage_split_pct
                break
            m2 = re.search(r'(\d{2})\s*:\s*(\d{2})', text)
            if m2:
                coverage_split_pct = _safe_float(m2.group(1))
                copay_pct = _safe_float(m2.group(2))
                break

    has_copay_rule = coverage_split_pct > 0 and copay_pct > 0

    # ── Actionable Specific Exclusions: match targeted clinical exclusions from policy ──
    GENERIC_EXCLUSION_WORDS = {
        "general", "care", "treatment", "treatments", "charges", "hospitalization", 
        "medical", "disease", "diseases", "condition", "conditions", "illness", 
        "evaluation", "investigation", "investigations", "record", "admission", 
        "necessary", "supplements", "substances", "states", "related", "stay", 
        "standard", "code", "clause", "and", "the", "for", "with", "without", 
        "any", "all", "per", "prescribed", "unproven", "convalescence", "debility",
        "therapy", "therapies", "agents", "drugs", "cycle", "course", "person", "insured"
    }

    # Categories that represent drug/pharmacy costs (not procedure administration charges)
    PHARMACY_CATEGORIES = {
        "pharmacy", "medications", "medication", "drugs", "drug", "medicine",
        "pharmacy / medications", "pharmacy/medications", "consumables",
    }

    # Categories that represent procedure/surgical/administration charges
    PROCEDURE_CATEGORIES = {
        "surgical", "surgery", "procedure", "procedures", "surgical / procedures",
        "surgical/procedures", "administration", "day care", "operation",
    }

    # Determine if the exclusion clause is about drugs/pharmacy vs general clinical exclusion
    def _is_drug_exclusion(exclusion_text: str) -> bool:
        """Detect if an exclusion clause is specifically about drugs/medications."""
        drug_signals = {"drug", "drugs", "oral", "medication", "medications", "tablet",
                        "capsule", "biologic", "agent", "agents", "pharmaceutical"}
        words = set(re.split(r'[\s/&,()]+', exclusion_text.lower()))
        return bool(words & drug_signals)

    # Extract actionable specific clinical terms from exclusion clauses
    specific_exclusion_targets: list[tuple[str, list[str], bool]] = []
    for ec in excluded_categories:
        ec_clean = ec.strip().lower()
        is_drug_excl = _is_drug_exclusion(ec_clean)
        parts = re.split(r'[,;:]|\band\b|\bor\b|\bincluding\b', ec_clean)
        for part in parts:
            p = part.strip()
            meaningful_words = [w for w in re.split(r'[\s/&,()]+', p) if len(w) > 3 and w not in GENERIC_EXCLUSION_WORDS]
            if meaningful_words:
                target_phrase = " ".join(meaningful_words)
                specific_exclusion_targets.append((target_phrase, meaningful_words, is_drug_excl))

    # Track rejection reasons for the report
    rejection_reasons: list[dict[str, Any]] = []

    excluded_amount = 0.0
    if specific_exclusion_targets and all_bill_items:
        for it in all_bill_items:
            desc_lower = (getattr(it, "description", "") or "").lower()
            cat_lower = (getattr(it, "category", "") or "").lower()
            amt = float(getattr(it, "amount", 0.0) or 0.0)
            
            # Determine if this bill item is a pharmacy/drug item or a procedure item
            cat_words = set(w.lower() for w in re.split(r'[\s/&,]+', cat_lower) if w)
            is_pharmacy_item = bool(cat_words & PHARMACY_CATEGORIES)
            is_procedure_item = bool(cat_words & PROCEDURE_CATEGORIES)

            is_item_excluded = False
            exclusion_reason = ""
            for target_phrase, words, is_drug_excl in specific_exclusion_targets:
                # For drug exclusions: only match against pharmacy/medication items,
                # NOT against surgical procedure / administration charge items.
                # The drug cost (₹15,088 for Capecitabine) is excluded,
                # but the chemotherapy administration fee (₹23,594) is covered.
                if is_drug_excl and is_procedure_item and not is_pharmacy_item:
                    continue

                matched = False
                if len(target_phrase) > 4 and target_phrase in desc_lower:
                    matched = True
                else:
                    for w in words:
                        if len(w) >= 5 and w in desc_lower:
                            matched = True
                            break
                
                if matched:
                    is_item_excluded = True
                    exclusion_reason = (
                        f"Policy Exclusion Clause: '{target_phrase}' is specifically excluded from coverage in the insurance policy document. "
                        f"— Patient Liability: Patient must bear non-formulary and excluded medication/procedure costs out-of-pocket at hospital discharge."
                    )
                    break

            if is_item_excluded:
                excluded_amount += amt
                rejection_reasons.append({
                    "item": getattr(it, "description", ""),
                    "amount": amt,
                    "reason": exclusion_reason,
                    "type": "exclusion",
                })

    # ── Room rent sub-limit: calculate excess room charges from bill items ──
    room_rent_excess = 0.0
    if (room_rent_sublimit > 0 or icu_sublimit > 0) and all_bill_items:
        for it in all_bill_items:
            desc_lower = (getattr(it, "description", "") or "").lower()
            cat_lower = (getattr(it, "category", "") or "").lower()
            amt = float(getattr(it, "amount", 0.0) or 0.0)
            qty = int(getattr(it, "quantity", 1) or 1)
            if "icu" in desc_lower or "iccu" in desc_lower:
                if icu_sublimit > 0 and "monitoring" not in desc_lower:
                    max_allowed = icu_sublimit * qty
                    if amt > max_allowed:
                        excess = round(amt - max_allowed, 2)
                        room_rent_excess += excess
                        rejection_reasons.append({
                            "item": getattr(it, "description", ""),
                            "amount": excess,
                            "reason": (
                                f"Policy Room Sub-limit Clause: Policy document caps ICU accommodation tariff at INR {icu_sublimit:,.0f}/day "
                                f"(billed INR {amt:,.0f} for {qty} day(s), allowed INR {max_allowed:,.0f}). Excess INR {excess:,.0f} is rejected from insurance coverage. "
                                f"— Patient Liability: Room rent charged above the policy daily sub-limit is excluded from insurer liability and payable by the patient."
                            ),
                            "type": "room_sublimit",
                        })
            elif room_rent_sublimit > 0 and ("ward" in desc_lower or "room" in desc_lower or "bed" in desc_lower or "room & board" in cat_lower):
                max_allowed = room_rent_sublimit * qty
                if amt > max_allowed:
                    excess = round(amt - max_allowed, 2)
                    room_rent_excess += excess
                    rejection_reasons.append({
                        "item": getattr(it, "description", ""),
                        "amount": excess,
                        "reason": (
                            f"Policy Room Sub-limit Clause: Policy document caps General Ward room rent at INR {room_rent_sublimit:,.0f}/day "
                            f"(billed INR {amt:,.0f} for {qty} day(s), allowed INR {max_allowed:,.0f}). Excess INR {excess:,.0f} is rejected from insurance coverage. "
                            f"— Patient Liability: Room rent charged above the policy daily sub-limit is excluded from insurer liability and payable by the patient."
                        ),
                        "type": "room_sublimit",
                    })

    # ── Determine the eligible (covered) amount from the bill by matching categories ──
    eligible_amount = total_billed  # default: entire bill is eligible
    if bill_items_by_category and covered_categories:
        CATEGORY_SEMANTICS: dict[str, set[str]] = {
            "in-patient": {"room", "board", "ward", "bed", "nursing", "general", "professional", "fees", "consultation", "physician", "surgeon", "oncologist", "surgical", "surgery", "procedure", "administration", "monitoring", "investigation", "investigations", "test", "laboratory", "lab", "scan", "radiology", "ct", "pharmacy", "medication", "medications", "drug", "drugs", "fluid", "consumable"},
            "hospitalization": {"room", "board", "ward", "bed", "nursing", "general", "professional", "fees", "consultation", "physician", "surgeon", "oncologist", "surgical", "surgery", "procedure", "administration", "monitoring", "investigation", "investigations", "test", "laboratory", "lab", "scan", "radiology", "ct", "pharmacy", "medication", "medications", "drug", "drugs", "fluid", "consumable"},
            "icu": {"icu", "iccu", "critical", "intensive", "ventilation", "monitoring", "life support"},
            "critical care": {"icu", "iccu", "critical", "intensive", "ventilation", "monitoring"},
            "oncology": {"chemotherapy", "oncol", "cancer", "tumor", "radiation", "biopsy", "hpe", "molecular", "imatinib", "targeted", "capecitabine", "irinotecan"},
            "surgical": {"surgical", "surgery", "procedure", "operation", "aspiration", "resection", "cannulation", "biopsy", "colonoscopy"},
            "medical": {"medical", "medicine", "physician", "consultation", "monitoring", "hematological"},
            "day care": {"day care", "infusion", "hemodialysis", "minor", "chemotherapy", "procedure", "administration"},
            "pre-hospitalization": {"pre-hospitalization", "diagnostic", "investigation", "laboratory", "test", "scan", "x-ray", "ecg", "imaging", "pathology", "biopsy", "cbp", "cbc", "lft", "rft"},
            "post-hospitalization": {"post-hospitalization", "follow-up", "medication", "discharge"},
            "investigations": {"investigation", "test", "laboratory", "lab", "pathology", "radiology", "scan", "x-ray", "ecg", "biopsy", "cbc", "cbp", "lft", "rft", "molecular", "colonoscopy"},
            "pharmacy": {"pharmacy", "medication", "drug", "tablet", "injection", "iv fluid", "consumable", "anti-emetic", "supportive"},
            "room": {"room", "board", "ward", "bed", "nursing"},
            "professional": {"professional", "fees", "consultation", "surgeon", "physician", "oncologist"},
            "procedure": {"procedure", "surgical", "surgery", "aspiration", "administration", "monitoring", "management"},
        }

        covered_cats_lower = [c.lower().strip() for c in covered_categories]
        
        covered_keywords: set[str] = set()
        for cc in covered_cats_lower:
            for sem_key, keywords in CATEGORY_SEMANTICS.items():
                if sem_key in cc or any(w in cc for w in sem_key.split()):
                    covered_keywords.update(keywords)
            covered_keywords.update(w for w in cc.split() if len(w) > 2)
        
        normalized_keywords: set[str] = set()
        for kw in covered_keywords:
            normalized_keywords.add(kw)
            normalized_keywords.add(kw.rstrip('s'))
            normalized_keywords.add(kw + 's')

        matched_amount = 0.0
        unmatched_amount = 0.0
        for cat, cat_amt in bill_items_by_category.items():
            cat_lower = cat.lower().strip()
            cat_words = set(w for w in re.split(r'[\s/&,]+', cat_lower) if len(w) > 2)
            cat_words_stemmed = set()
            for w in cat_words:
                cat_words_stemmed.add(w)
                cat_words_stemmed.add(w.rstrip('s'))
            
            is_covered = False
            for cc in covered_cats_lower:
                if cat_lower in cc or cc in cat_lower:
                    is_covered = True
                    break
            if not is_covered and cat_words_stemmed & normalized_keywords:
                is_covered = True

            if is_covered:
                matched_amount += cat_amt
            else:
                unmatched_amount += cat_amt
        
        if matched_amount > 0:
            match_ratio = matched_amount / (matched_amount + unmatched_amount) if (matched_amount + unmatched_amount) > 0 else 1.0
            if match_ratio >= 0.5:
                eligible_amount = total_billed
            else:
                eligible_amount = matched_amount

    # Subtract excluded amounts from eligible amount
    if excluded_amount > 0:
        eligible_amount = round(max(0.0, eligible_amount - excluded_amount), 2)

    # Subtract room rent excess from eligible amount
    if room_rent_excess > 0:
        eligible_amount = round(max(0.0, eligible_amount - room_rent_excess), 2)

    # Apply deductible: reduce eligible amount by the per-claim deductible
    deductible_applied = 0.0
    if deductible_amount > 0:
        deductible_applied = min(deductible_amount, eligible_amount)
        eligible_amount = round(max(0.0, eligible_amount - deductible_applied), 2)
        if deductible_applied > 0:
            rejection_reasons.append({
                "item": "Mandatory Policy Deductible",
                "amount": deductible_applied,
                "reason": (
                    f"Policy Deductible Clause: Insurance policy document specifies a mandatory deductible of INR {deductible_amount:,.0f} per hospitalization. "
                    f"The first INR {deductible_applied:,.0f} of admissible expenses is deducted from claim payout. "
                    f"— Patient Liability: Mandatory policy deductible threshold must be paid out-of-pocket by the patient before insurance benefits apply."
                ),
                "type": "deductible",
            })

    # Cap eligible amount by sum insured
    if sum_insured > 0 and eligible_amount > sum_insured:
        sum_insured_excess = round(eligible_amount - sum_insured, 2)
        eligible_amount = sum_insured
        rejection_reasons.append({
            "item": "Annual Sum Insured Limit Exceeded",
            "amount": sum_insured_excess,
            "reason": (
                f"Policy Sum Insured Ceiling: Total admissible hospital claim exceeds the annual sum insured maximum cap of INR {sum_insured:,.2f} stated in the policy document. "
                f"Excess charges of INR {sum_insured_excess:,.2f} are rejected from insurance coverage. "
                f"— Patient Liability: Invoiced medical expenses beyond the policy annual limit are the responsibility of the patient."
            ),
            "type": "sum_insured_limit",
        })
    elif sum_insured > 0:
        eligible_amount = min(eligible_amount, sum_insured)

    # Adjudication calculation
    # At this point, eligible_amount already accounts for:
    # - Excluded categories (subtracted)
    # - Room rent sub-limit excess (subtracted)
    # - Deductible (subtracted)
    # - Sum insured cap
    # The gap (total_billed - eligible_amount) = patient's non-coverable portion
    non_coverable = round(max(0.0, total_billed - eligible_amount), 2)

    if has_copay_rule:
        # Percentage-based coverage rule (e.g. 80:20 risk-sharing)
        insurer_pct = coverage_split_pct / 100.0
        if pre_auth_appr > 0 and pre_auth_appr < eligible_amount:
            # Exact pre-auth amount overrides percentage calculation
            insured_amount = pre_auth_appr
        else:
            insured_amount = round(eligible_amount * insurer_pct, 2)
        # Determine patient payable
        if policy_patient_oop > 100:
            # Explicit rupee amount from the document (> 100 to filter out ratios)
            patient_payable = policy_patient_oop
        elif copay_liab > 100:
            # Explicit rupee co-payment liability amount
            patient_payable = copay_liab
        else:
            # Calculate from percentage: patient pays the copay share of eligible amount
            patient_payable = round(eligible_amount - insured_amount, 2)
            if patient_payable > 0:
                copay_pct_val = round(100.0 - coverage_split_pct, 1)
                rejection_reasons.append({
                    "item": f"Policy Co-Payment ({copay_pct_val:.0f}% Co-Pay Liability)",
                    "amount": patient_payable,
                    "reason": (
                        f"Policy Co-Payment Clause: Insurance policy document establishes a {coverage_split_pct:.0f}:{copay_pct_val:.0f} risk-sharing schedule under {scheme}. "
                        f"Insurer coverage is limited to {coverage_split_pct:.0f}% of eligible expenses (INR {insured_amount:,.2f}); remaining {copay_pct_val:.0f}% is excluded from insurer liability. "
                        f"— Patient Liability: Beneficiary is contractually required to pay {copay_pct_val:.0f}% cost-sharing liability (INR {patient_payable:,.2f}) at hospital discharge."
                    ),
                    "type": "copay",
                })
        # Add any non-coverable amount (excluded items, deductible, room excess) to patient payable
        patient_payable = round(patient_payable + non_coverable, 2)
    elif pre_auth_appr > 0:
        # Pre-authorized trust/insurer sanction exists in document (exact amount)
        insured_amount = min(eligible_amount, pre_auth_appr) if eligible_amount > 0 else pre_auth_appr
        patient_payable = policy_patient_oop if policy_patient_oop > 100 else 0.0
        if patient_payable == 0.0:
            patient_payable = round(total_billed - insured_amount, 2)
    elif copay_pct > 0:
        copay_amt = round(eligible_amount * (copay_pct / 100.0), 2)
        insured_amount = round(eligible_amount - copay_amt, 2)
        patient_payable = round(copay_amt + non_coverable, 2)
        if copay_amt > 0:
            rejection_reasons.append({
                "item": f"Policy Co-Payment ({copay_pct:.0f}% Co-Pay Liability)",
                "amount": copay_amt,
                "reason": (
                    f"Policy Co-Payment Clause: Insurance policy document mandates a {copay_pct:.0f}% beneficiary co-payment on admissible hospital charges. "
                    f"Insurer liability excludes INR {copay_amt:,.2f}. "
                    f"— Patient Liability: Beneficiary is contractually required to pay {copay_pct:.0f}% co-payment (INR {copay_amt:,.2f}) out-of-pocket."
                ),
                "type": "copay",
            })
    elif copay_liab > 100:
        patient_payable = min(eligible_amount, copay_liab) + non_coverable
        insured_amount = round(total_billed - patient_payable, 2)
    elif "cashless" in cov_type or (copay_liab == 0.0 and copay_pct == 0.0):
        # Full cashless scheme (e.g. Dr. YSR Aarogyasri / AB-PMJAY)
        # Use eligible_amount which already excludes deductible, exclusions, room excess
        max_limit = sum_insured if sum_insured > 0 else 2500000.0
        insured_amount = min(eligible_amount, max_limit)
        patient_payable = round(total_billed - insured_amount, 2)
    else:
        insured_amount = eligible_amount
        patient_payable = round(non_coverable, 2)

    coverage_pct = round((insured_amount / total_billed * 100.0), 1) if total_billed > 0 else 100.0

    if patient_payable == 0.0 and insured_amount >= total_billed:
        status = "100% Cashless Approved"
        notes = f"Hospital bill of INR {total_billed:,.2f} is 100% covered and approved under {scheme} (Policy #{policy_num}). Beneficiary out-of-pocket payment is INR 0.00 (NIL)."
    elif insured_amount > 0:
        status = f"Partially Covered ({coverage_pct}%)"
        notes = f"Hospital bill of INR {total_billed:,.2f} has approved coverage of INR {insured_amount:,.2f} ({coverage_pct}%) under {scheme} (Policy #{policy_num}). Patient payable co-payment liability is INR {patient_payable:,.2f}."
    else:
        status = "Not Covered"
        notes = f"Hospital bill of INR {total_billed:,.2f} is not covered under policy terms. Patient payable amount is INR {patient_payable:,.2f}."

    return {
        "total_billed": total_billed,
        "insured_amount": insured_amount,
        "patient_payable": patient_payable,
        "coverage_percentage": coverage_pct,
        "status": status,
        "notes": notes,
        "scheme_or_insurer": scheme,
        "policy_number": policy_num,
        "claim_or_preauth_number": preauth_num,
        "annual_sum_insured": sum_insured,
        "rejection_reasons": rejection_reasons,
    }



def _build_claim_response_resource(
    claim_ref: str,
    coverage_ref: str,
    patient_ref: str,
    organization_ref: str,
    adjudication: dict[str, Any],
) -> dict[str, Any]:
    """Constructs a normative FHIR R4 ClaimResponse resource representing adjudication results."""
    cr_id = str(uuid.uuid4())
    total_billed = _safe_float(adjudication.get("total_billed", 0.0))
    insured_amount = _safe_float(adjudication.get("insured_amount", 0.0))
    patient_payable = _safe_float(adjudication.get("patient_payable", 0.0))
    notes = adjudication.get("notes", "")

    claim_response: dict[str, Any] = {
        "resourceType": "ClaimResponse",
        "id": cr_id,
        "meta": {
            "profile": [ABDM_CLAIM_RESPONSE_PROFILE],
        },
        "status": "active",
        "type": {
            "coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/claim-type",
                "code": "institutional",
                "display": "Hospital In-Patient / Daycare Claim",
            }]
        },
        "use": "claim",
        "patient": {"reference": patient_ref},
        "insurer": {"reference": organization_ref},
        "request": {"reference": claim_ref},
        "outcome": "complete",
        "disposition": notes,
        "insurance": [{
            "sequence": 1,
            "focal": True,
            "coverage": {"reference": coverage_ref},
        }],
        "total": [
            {
                "category": {
                    "coding": [{
                        "system": "http://terminology.hl7.org/CodeSystem/adjudication",
                        "code": "submitted",
                        "display": "Submitted Amount (Total Billed)",
                    }]
                },
                "amount": {
                    "value": total_billed,
                    "currency": "INR",
                }
            },
            {
                "category": {
                    "coding": [{
                        "system": "http://terminology.hl7.org/CodeSystem/adjudication",
                        "code": "benefit",
                        "display": "Benefit Amount (Insured / Trust Approved)",
                    }]
                },
                "amount": {
                    "value": insured_amount,
                    "currency": "INR",
                }
            },
            {
                "category": {
                    "coding": [{
                        "system": "http://terminology.hl7.org/CodeSystem/adjudication",
                        "code": "patientoutoppocket",
                        "display": "Patient Responsibility (Patient to Pay)",
                    }]
                },
                "amount": {
                    "value": patient_payable,
                    "currency": "INR",
                }
            }
        ],
        "payment": {
            "type": {
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/ex-paymenttype",
                    "code": "complete",
                    "display": "Complete Cashless Payment",
                }]
            },
            "amount": {
                "value": insured_amount,
                "currency": "INR",
            }
        }
    }

    pre_auth_ref = adjudication.get("claim_or_preauth_number")
    if pre_auth_ref:
        claim_response["preAuthRef"] = pre_auth_ref

    # Add rejection reasons as processNote entries
    rejection_reasons = adjudication.get("rejection_reasons", []) or []
    if rejection_reasons:
        process_notes = []
        for idx, rr in enumerate(rejection_reasons, 1):
            note_text = f"{rr.get('item', '')}: INR {rr.get('amount', 0):,.2f} — {rr.get('reason', '')}"
            process_notes.append({
                "number": idx,
                "type": "display",
                "text": note_text,
            })
        claim_response["processNote"] = process_notes

    return claim_response


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

    # 7. Add Billing / Claims Section if available
    all_claims: list[dict[str, Any]] = []
    all_coverages: list[dict[str, Any]] = []
    all_claim_responses: list[dict[str, Any]] = []

    if gemini_extraction and getattr(gemini_extraction, "billing_data", None):
        claim_refs: list[dict[str, str]] = []
        raw_bills = gemini_extraction.billing_data
        demo_bills = [
            b for b in raw_bills
            if "demo bill" in (getattr(b, "source_document", "") or "").lower()
            or "demo_bill" in (getattr(b, "source_document", "") or "").lower()
            or "hospital_bill" in (getattr(b, "source_document", "") or "").lower()
        ]
        target_bills = demo_bills if demo_bills else raw_bills
        for bill in target_bills:
            claim_res = _build_claim_resource(bill, patient_ref, organization_ref)
            all_claims.append(claim_res)
            claim_refs.append({"reference": f"urn:uuid:{claim_res['id']}"})

        if claim_refs:
            sections.append({
                "title": "Billing & Financial Records",
                "code": {
                    "coding": [
                        {
                            "system": "http://loinc.org",
                            "code": "64297-5",
                            "display": "Death certificate",  # closest generic billing code
                        }
                    ],
                    "text": "Billing & Financial Records",
                },
                "entry": claim_refs,
            })

    # 8. Add Insurance Coverage & Claim Adjudication Section if available
    if gemini_extraction and getattr(gemini_extraction, "insurance_policy", None):
        policy_data = gemini_extraction.insurance_policy
        coverage_res = _build_coverage_resource(policy_data, patient_ref, organization_ref)
        all_coverages.append(coverage_res)

        cov_claim_refs: list[dict[str, str]] = [{"reference": f"urn:uuid:{coverage_res['id']}"}]

        # Adjudicate against hospital bill
        bills_to_adjudicate = getattr(gemini_extraction, "billing_data", []) or []
        adjudication = _adjudicate_bill_against_policy(bills_to_adjudicate, policy_data)

        if all_claims:
            matched_claim_id = all_claims[0]["id"]
            for cl in all_claims:
                s_info = cl.get("supportingInfo", [])
                val_str = s_info[0].get("valueString", "").lower() if s_info else ""
                if "demo bill" in val_str or "hospital_bill" in val_str:
                    matched_claim_id = cl["id"]
                    break

            claim_response_res = _build_claim_response_resource(
                claim_ref=f"urn:uuid:{matched_claim_id}",
                coverage_ref=f"urn:uuid:{coverage_res['id']}",
                patient_ref=patient_ref,
                organization_ref=organization_ref,
                adjudication=adjudication,
            )
            all_claim_responses.append(claim_response_res)
            cov_claim_refs.append({"reference": f"urn:uuid:{claim_response_res['id']}"})

        sections.append({
            "title": "Insurance Coverage & Claim Adjudication",
            "code": {
                "coding": [
                    {
                        "system": "http://loinc.org",
                        "code": "75282-4",
                        "display": "Insurance policy",
                    }
                ],
                "text": "Insurance Coverage & Claim Adjudication",
            },
            "text": {
                "status": "additional",
                "div": (
                    f"<div xmlns='http://www.w3.org/1999/xhtml'>"
                    f"<p><strong>Policy Number:</strong> {policy_data.policy_number} | <strong>Scheme:</strong> {policy_data.scheme_or_insurer}</p>"
                    f"<p><strong>Status:</strong> {adjudication['status']} | <strong>Coverage:</strong> {adjudication['coverage_percentage']}%</p>"
                    f"<p><strong>Total Billed Amount:</strong> INR {adjudication['total_billed']:,.2f}</p>"
                    f"<p><strong>Insured / Covered Amount:</strong> INR {adjudication['insured_amount']:,.2f}</p>"
                    f"<p><strong>Patient Out-of-Pocket Liability:</strong> INR {adjudication['patient_payable']:,.2f}</p>"
                    f"<p><strong>Adjudication Notes:</strong> {adjudication['notes']}</p>"
                    f"</div>"
                ),
            },
            "entry": cov_claim_refs,
        })

    # 8. Add Archive Document Manifest Section if available
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

        # Ensure observation has canonical LOINC code from LOINC 2.83
        code_obj = obs.get("code") or {}
        codings = code_obj.get("coding") or []
        has_loinc = any(c.get("system") == "http://loinc.org" and c.get("code") for c in codings if isinstance(c, dict))
        if not has_loinc:
            tname = code_obj.get("text") or (codings[0].get("display") if codings else "")
            if tname:
                loinc_match = _lookup_loinc(tname)
                if loinc_match:
                    new_codings = [c for c in codings if isinstance(c, dict) and c.get("system") != "http://loinc.org"]
                    new_codings.insert(0, {
                        "system": "http://loinc.org",
                        "code": loinc_match[0],
                        "display": loinc_match[1],
                    })
                    code_obj["coding"] = new_codings
                    obs["code"] = code_obj

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

    for claim in all_claims:
        bundle_entries.append({
            "fullUrl": f"urn:uuid:{claim['id']}",
            "resource": claim,
        })

    for cov in all_coverages:
        bundle_entries.append({
            "fullUrl": f"urn:uuid:{cov['id']}",
            "resource": cov,
        })

    for cr in all_claim_responses:
        bundle_entries.append({
            "fullUrl": f"urn:uuid:{cr['id']}",
            "resource": cr,
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
