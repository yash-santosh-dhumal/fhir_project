"""Unit tests for production-grade archive_analyzer module."""

from __future__ import annotations

import io
import unittest
import zipfile

from demo.archive_analyzer import (
    ArchiveAnalysisResult,
    ArchiveFormatError,
    ArchiveSecurityError,
    extract_and_analyze_archive,
)


class TestArchiveAnalyzer(unittest.TestCase):

    def _create_zip(self, file_map: dict[str, bytes]) -> bytes:
        """Helper to construct in-memory ZIP archives."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path, data in file_map.items():
                zf.writestr(path, data)
        return buf.getvalue()

    def test_flat_zip_extraction(self):
        sample_files = {
            "report1.pdf": b"%PDF-1.4 test document 1",
            "report2.png": b"\x89PNG\r\n\x1a\n test png",
            "notes.txt": b"doctor notes - should be skipped",
        }
        zip_bytes = self._create_zip(sample_files)
        result = extract_and_analyze_archive(zip_bytes, "batch.zip")

        self.assertEqual(result.document_count, 2)
        self.assertEqual(result.skipped_count, 1)
        doc_names = [d.filename for d in result.documents]
        self.assertIn("report1.pdf", doc_names)
        self.assertIn("report2.png", doc_names)

        # Check MIME types
        pdf_doc = next(d for d in result.documents if d.filename == "report1.pdf")
        self.assertEqual(pdf_doc.mime_type, "application/pdf")
        self.assertEqual(pdf_doc.file_bytes, sample_files["report1.pdf"])

    def test_recursive_nested_subfolder_traversal(self):
        sample_files = {
            "Hospital_Records/ICU/Patient_A/2026-01-01_CBC.pdf": b"%PDF-1.4 patient A cbc",
            "Hospital_Records/ICU/Patient_A/Scans/chest_xray.jpg": b"\xff\xd8\xff test jpeg",
            "Hospital_Records/Outpatient/Dr_Smith/Patient_B/lipid.png": b"\x89PNG patient B",
            "Hospital_Records/README.txt": b"Text doc to skip",
        }
        zip_bytes = self._create_zip(sample_files)
        result = extract_and_analyze_archive(zip_bytes, "Hospital_Batch.zip")

        self.assertEqual(result.document_count, 3)
        self.assertEqual(result.skipped_count, 1)

        # Verify relative paths and folder preservation
        rel_paths = [d.relative_path for d in result.documents]
        self.assertIn("Hospital_Records/ICU/Patient_A/2026-01-01_CBC.pdf", rel_paths)
        self.assertIn("Hospital_Records/ICU/Patient_A/Scans/chest_xray.jpg", rel_paths)
        self.assertIn("Hospital_Records/Outpatient/Dr_Smith/Patient_B/lipid.png", rel_paths)

        # Check folder paths
        doc_a = next(d for d in result.documents if d.filename == "2026-01-01_CBC.pdf")
        self.assertEqual(doc_a.folder_path, "Hospital_Records/ICU/Patient_A")

        # Verify folder tree structure
        tree = result.folder_tree
        self.assertIn("Hospital_Records", tree)
        self.assertIn("ICU", tree["Hospital_Records"])
        self.assertIn("Patient_A", tree["Hospital_Records"]["ICU"])

    def test_system_and_junk_file_filtering(self):
        sample_files = {
            "__MACOSX/._report.pdf": b"junk metadata",
            "Patient_1/.DS_Store": b"mac os junk",
            "Patient_1/Thumbs.db": b"windows junk",
            "Patient_1/report.pdf": b"%PDF-1.4 real report",
        }
        zip_bytes = self._create_zip(sample_files)
        result = extract_and_analyze_archive(zip_bytes, "mac_archive.zip")

        self.assertEqual(result.document_count, 1)
        self.assertEqual(result.documents[0].filename, "report.pdf")
        self.assertEqual(result.skipped_count, 3)
        reasons = [s.reason for s in result.skipped_files]
        self.assertTrue(all(r == "system_metadata" for r in reasons))

    def test_zip_slip_security_prevention(self):
        malicious_files = {
            "../../etc/passwd": b"root:x:0:0:root",
            "Patient/report.pdf": b"%PDF-1.4 normal file",
        }
        zip_bytes = self._create_zip(malicious_files)
        with self.assertRaises(ArchiveSecurityError) as ctx:
            extract_and_analyze_archive(zip_bytes, "exploit.zip")
        self.assertIn("path traversal", str(ctx.exception).lower())

    def test_empty_and_corrupt_archive(self):
        with self.assertRaises(ArchiveFormatError):
            extract_and_analyze_archive(b"", "empty.zip")

        with self.assertRaises(ArchiveFormatError):
            extract_and_analyze_archive(b"NOT A REAL ZIP FILE", "bad.zip")

    def test_max_files_limit_enforcement(self):
        files = {f"file_{i}.pdf": b"%PDF-1.4 test" for i in range(15)}
        zip_bytes = self._create_zip(files)
        with self.assertRaises(ArchiveSecurityError) as ctx:
            extract_and_analyze_archive(zip_bytes, "too_many.zip", max_files=10)
        self.assertIn("maximum allowable entries", str(ctx.exception).lower())

    def test_max_total_size_limit_enforcement(self):
        files = {"large_report.pdf": b"X" * 2000}
        zip_bytes = self._create_zip(files)
        with self.assertRaises(ArchiveSecurityError) as ctx:
            extract_and_analyze_archive(zip_bytes, "oversized.zip", max_total_uncompressed_bytes=1000)
        self.assertIn("exceeds maximum safety limit", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
