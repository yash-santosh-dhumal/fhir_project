"""Lightweight FHIR R4 resource storage using SQLite.

Stores complete FHIR Document Bundles alongside indexed Patient and
Observation resources for fast querying.  Designed for local/demo use;
swap SQLite for PostgreSQL in production hospital deployments.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any


_DB_PATH = Path(__file__).resolve().parent / "fhir_data.db"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS patients (
    id             TEXT PRIMARY KEY,
    name           TEXT,
    gender         TEXT,
    birth_date     TEXT,
    abha_id        TEXT,
    patient_json   TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS bundles (
    id               TEXT PRIMARY KEY,
    bundle_json      TEXT NOT NULL,
    source_filename  TEXT,
    document_type    TEXT,
    patient_id       TEXT,
    page_count       INTEGER DEFAULT 0,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (patient_id) REFERENCES patients(id)
);

CREATE TABLE IF NOT EXISTS observations (
    id                TEXT PRIMARY KEY,
    bundle_id         TEXT NOT NULL,
    patient_id        TEXT,
    code_text         TEXT,
    loinc_code        TEXT,
    value             REAL,
    unit              TEXT,
    reference_range   TEXT,
    status            TEXT,
    observation_json  TEXT NOT NULL,
    FOREIGN KEY (bundle_id) REFERENCES bundles(id),
    FOREIGN KEY (patient_id) REFERENCES patients(id)
);

CREATE TABLE IF NOT EXISTS processing_log (
    id             TEXT PRIMARY KEY,
    batch_id       TEXT,
    filename       TEXT,
    status         TEXT NOT NULL DEFAULT 'pending',
    error_message  TEXT,
    duration_ms    REAL,
    bundle_id      TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (bundle_id) REFERENCES bundles(id)
);

CREATE INDEX IF NOT EXISTS idx_bundles_patient ON bundles(patient_id);
CREATE INDEX IF NOT EXISTS idx_observations_bundle ON observations(bundle_id);
CREATE INDEX IF NOT EXISTS idx_observations_patient ON observations(patient_id);
CREATE INDEX IF NOT EXISTS idx_processing_log_batch ON processing_log(batch_id);
"""


class FhirStore:
    """Thread-safe SQLite store for FHIR resources."""

    def __init__(self, db_path: str | Path | None = None):
        self._db_path = str(db_path or _DB_PATH)
        self._local = threading.local()
        self._ensure_schema()

    # ── Connection management ──

    def _get_conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def _ensure_schema(self) -> None:
        conn = self._get_conn()
        conn.executescript(_SCHEMA_SQL)
        conn.commit()

    # ── Patient operations ──

    def upsert_patient(self, patient_resource: dict[str, Any]) -> str:
        """Insert or find an existing patient.  Deduplicates by name+gender+birthDate."""
        names = patient_resource.get("name", []) or []
        first = names[0] if names else {}
        name = first.get("text") or " ".join(
            [*first.get("given", []), first.get("family", "")]
        ).strip()
        gender = patient_resource.get("gender", "")
        birth_date = patient_resource.get("birthDate", "")

        conn = self._get_conn()
        row = conn.execute(
            "SELECT id FROM patients WHERE name=? AND gender=? AND birth_date=?",
            (name, gender, birth_date),
        ).fetchone()
        if row:
            return row["id"]

        patient_id = patient_resource.get("id") or str(uuid.uuid4())
        conn.execute(
            "INSERT INTO patients (id, name, gender, birth_date, patient_json) VALUES (?,?,?,?,?)",
            (patient_id, name, gender, birth_date, json.dumps(patient_resource)),
        )
        conn.commit()
        return patient_id

    def get_all_patients(self) -> list[dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT p.*, COUNT(b.id) AS bundle_count "
            "FROM patients p LEFT JOIN bundles b ON b.patient_id = p.id "
            "GROUP BY p.id ORDER BY p.created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_patient(self, patient_id: str) -> dict[str, Any] | None:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM patients WHERE id=?", (patient_id,)).fetchone()
        return dict(row) if row else None

    def delete_patient(self, patient_id: str) -> bool:
        """Deletes a patient, all their associated bundles, and observations."""
        conn = self._get_conn()
        row = conn.execute("SELECT id FROM patients WHERE id=?", (patient_id,)).fetchone()
        if not row:
            return False

        # Find all bundles for this patient
        bundle_rows = conn.execute("SELECT id FROM bundles WHERE patient_id=?", (patient_id,)).fetchall()
        for b in bundle_rows:
            bid = b["id"]
            conn.execute("DELETE FROM observations WHERE bundle_id=?", (bid,))
            conn.execute("UPDATE processing_log SET bundle_id=NULL WHERE bundle_id=?", (bid,))

        conn.execute("DELETE FROM observations WHERE patient_id=?", (patient_id,))
        conn.execute("DELETE FROM bundles WHERE patient_id=?", (patient_id,))
        conn.execute("DELETE FROM patients WHERE id=?", (patient_id,))
        conn.commit()
        return True

    # ── Bundle operations ──

    def store_bundle(
        self,
        bundle: dict[str, Any],
        source_filename: str = "",
        document_type: str = "",
        page_count: int = 0,
    ) -> str:
        """Store a FHIR Bundle and index its Patient + Observations."""
        bundle_id = bundle.get("id") or str(uuid.uuid4())
        entries = bundle.get("entry", []) or []
        resources = [e.get("resource", {}) for e in entries if e.get("resource")]

        # Find and upsert patient
        patient_res = next(
            (r for r in resources if r.get("resourceType") == "Patient"), None
        )
        patient_id = self.upsert_patient(patient_res) if patient_res else None

        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO bundles (id, bundle_json, source_filename, document_type, patient_id, page_count) "
            "VALUES (?,?,?,?,?,?)",
            (bundle_id, json.dumps(bundle), source_filename, document_type, patient_id, page_count),
        )

        # Index observations
        for r in resources:
            if r.get("resourceType") != "Observation":
                continue
            obs_id = r.get("id") or str(uuid.uuid4())
            code = r.get("code", {}) or {}
            codings = code.get("coding", []) or []
            loinc = next(
                (c for c in codings if c.get("system") == "http://loinc.org"), None
            )
            vq = r.get("valueQuantity") or {}
            ref_ranges = r.get("referenceRange", []) or []
            ref_text = ref_ranges[0].get("text", "") if ref_ranges else ""

            conn.execute(
                "INSERT OR REPLACE INTO observations "
                "(id, bundle_id, patient_id, code_text, loinc_code, value, unit, reference_range, status, observation_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    obs_id,
                    bundle_id,
                    patient_id,
                    code.get("text", ""),
                    loinc.get("code") if loinc else None,
                    vq.get("value"),
                    vq.get("unit") or vq.get("code", ""),
                    ref_text,
                    r.get("status", ""),
                    json.dumps(r),
                ),
            )

        conn.commit()
        return bundle_id

    def get_all_bundles(self, limit: int = 100) -> list[dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT b.id, b.source_filename, b.document_type, b.patient_id, "
            "b.page_count, b.created_at, p.name AS patient_name "
            "FROM bundles b LEFT JOIN patients p ON p.id = b.patient_id "
            "ORDER BY b.created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_bundle(self, bundle_id: str) -> dict[str, Any] | None:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM bundles WHERE id=?", (bundle_id,)).fetchone()
        return dict(row) if row else None

    def get_bundles_for_patient(self, patient_id: str) -> list[dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, bundle_json, source_filename, document_type, page_count, created_at "
            "FROM bundles WHERE patient_id=? ORDER BY created_at DESC",
            (patient_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_bundle(self, bundle_id: str) -> bool:
        """Deletes a bundle and its observations from the store."""
        conn = self._get_conn()
        row = conn.execute("SELECT patient_id FROM bundles WHERE id=?", (bundle_id,)).fetchone()
        if not row:
            return False
        patient_id = row["patient_id"]

        conn.execute("DELETE FROM observations WHERE bundle_id=?", (bundle_id,))
        conn.execute("UPDATE processing_log SET bundle_id=NULL WHERE bundle_id=?", (bundle_id,))
        conn.execute("DELETE FROM bundles WHERE id=?", (bundle_id,))

        # If patient has no remaining bundles, clean up the patient record as well
        if patient_id:
            remaining = conn.execute("SELECT COUNT(*) AS c FROM bundles WHERE patient_id=?", (patient_id,)).fetchone()
            if remaining and remaining["c"] == 0:
                conn.execute("DELETE FROM patients WHERE id=?", (patient_id,))

        conn.commit()
        return True

    def get_observations_for_patient(self, patient_id: str) -> list[dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT o.*, b.source_filename "
            "FROM observations o JOIN bundles b ON b.id = o.bundle_id "
            "WHERE o.patient_id=? ORDER BY b.created_at DESC",
            (patient_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Processing log ──

    def log_processing(
        self,
        batch_id: str,
        filename: str,
        status: str = "pending",
        error_message: str = "",
        duration_ms: float = 0,
        bundle_id: str = "",
    ) -> str:
        log_id = str(uuid.uuid4())
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO processing_log (id, batch_id, filename, status, error_message, duration_ms, bundle_id) "
            "VALUES (?,?,?,?,?,?,?)",
            (log_id, batch_id, filename, status, error_message, duration_ms, bundle_id or None),
        )
        conn.commit()
        return log_id

    def update_processing_log(
        self, log_id: str, status: str, error_message: str = "", duration_ms: float = 0, bundle_id: str = ""
    ) -> None:
        conn = self._get_conn()
        conn.execute(
            "UPDATE processing_log SET status=?, error_message=?, duration_ms=?, bundle_id=? WHERE id=?",
            (status, error_message, duration_ms, bundle_id or None, log_id),
        )
        conn.commit()

    def get_batch_status(self, batch_id: str) -> list[dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM processing_log WHERE batch_id=? ORDER BY created_at",
            (batch_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Stats ──

    def get_stats(self) -> dict[str, Any]:
        conn = self._get_conn()
        patients = conn.execute("SELECT COUNT(*) AS c FROM patients").fetchone()["c"]
        bundles = conn.execute("SELECT COUNT(*) AS c FROM bundles").fetchone()["c"]
        observations = conn.execute("SELECT COUNT(*) AS c FROM observations").fetchone()["c"]
        return {
            "patients": patients,
            "bundles": bundles,
            "observations": observations,
        }
