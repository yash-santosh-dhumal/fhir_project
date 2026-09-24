"""Production-grade ZIP archive analyzer for hospital MIS medical documents.

Recursively extracts and analyzes nested folders and sub-folders within ZIP archives:
- Full Zip-Slip / path traversal protection (CWE-22)
- Decompression bomb / zip bomb mitigation (CWE-409)
- Enforces strict safety limits on file sizes, total size, and file counts
- Filters out OS junk / metadata (__MACOSX, .DS_Store, Thumbs.db, etc.)
- Traverses arbitrary directory depths (Ward/Patient/Date/document.pdf)
- Detects medical documents (PDFs, JPEGs, PNGs, WebP, BMP, TIFF, DOCX)
- Recursively extracts nested ZIP archives
- Normalizes TIFF images to JPEG for model / OCR pipeline compatibility
"""

from __future__ import annotations

import io
import logging
import os
import posixpath
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import PIL.Image

log = logging.getLogger(__name__)

# ── Safety Quotas & Defaults ──
DEFAULT_MAX_FILES = 200
DEFAULT_MAX_SINGLE_FILE_BYTES = 50 * 1024 * 1024  # 50 MB
DEFAULT_MAX_TOTAL_UNCOMPRESSED_BYTES = 250 * 1024 * 1024  # 250 MB
DEFAULT_MAX_COMPRESSION_RATIO = 100  # uncompressed / compressed

# ── Supported Document Formats ──
SUPPORTED_DOCUMENT_EXTENSIONS: dict[str, str] = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

# Extensions that are nested archives to be recursively extracted
NESTED_ARCHIVE_EXTENSIONS = {".zip"}

# ── Ignored OS & System Files ──
IGNORED_PATTERNS = (
    "__macosx/",
    ".ds_store",
    "thumbs.db",
    "desktop.ini",
    ".git/",
    ".gitignore",
    ".svn/",
    "ehthumbs.db",
)

# ── Non-Text Visual Evidence Photo Identifiers ──
# Folders and filenames containing camera photos of wards, selfies, beds, wound photos,
# emblems, and QR codes that do not contain readable medical text or lab tables.
NON_TEXT_PHOTO_FOLDERS = (
    "anm selfie",
    "ward photos",
    "on bed visit",
    "after discharge photo",
    "after surgery",
    "intra op photos",
)

NON_TEXT_PHOTO_FILENAMES = (
    "ap_emblem",
    "qr_code",
    "qr_test",
    "patient_photo",
    "patient_portrait",
    "emblem_tp",
    "discharge photo",
)


class ArchiveSecurityError(Exception):
    """Raised when an archive violates security limits (Zip-Slip, Zip Bomb, etc.)."""


class ArchiveFormatError(Exception):
    """Raised when an archive is corrupt, empty, or not a valid ZIP."""


@dataclass
class ArchiveDocument:
    """Represents a valid medical document extracted from an archive."""

    filename: str  # Base filename, e.g., "CBC_Report.pdf"
    relative_path: str  # Normalized relative path, e.g., "Ward_A/Patient_101/CBC_Report.pdf"
    folder_path: str  # Containing folder, e.g., "Ward_A/Patient_101"
    file_bytes: bytes  # Document content
    mime_type: str  # e.g., "application/pdf", "image/jpeg"
    size_bytes: int  # Uncompressed byte size
    is_clinical_text_document: bool = True  # True if document contains readable clinical/financial text

    def to_dict(self, include_bytes: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "filename": self.filename,
            "relative_path": self.relative_path,
            "folder_path": self.folder_path,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "is_clinical_text_document": self.is_clinical_text_document,
        }
        if include_bytes:
            data["file_bytes"] = self.file_bytes
        return data


@dataclass
class SkippedFile:
    """Record of an ignored or unsupported file in the archive."""

    path: str
    reason: str  # "system_metadata", "unsupported_extension", "empty_file"
    size_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "reason": self.reason,
            "size_bytes": self.size_bytes,
        }


@dataclass
class ArchiveAnalysisResult:
    """Complete summary of the recursive archive analysis."""

    archive_name: str
    total_entries_scanned: int
    total_uncompressed_bytes: int
    documents: list[ArchiveDocument] = field(default_factory=list)
    skipped_files: list[SkippedFile] = field(default_factory=list)
    folder_tree: dict[str, Any] = field(default_factory=dict)

    @property
    def document_count(self) -> int:
        return len(self.documents)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped_files)

    def to_dict(self, include_bytes: bool = False) -> dict[str, Any]:
        return {
            "archive_name": self.archive_name,
            "document_count": self.document_count,
            "skipped_count": self.skipped_count,
            "total_entries_scanned": self.total_entries_scanned,
            "total_uncompressed_bytes": self.total_uncompressed_bytes,
            "documents": [d.to_dict(include_bytes=include_bytes) for d in self.documents],
            "skipped_files": [s.to_dict() for s in self.skipped_files],
            "folder_tree": self.folder_tree,
        }


def _is_zip_slip(raw_path: str) -> bool:
    """Detects path traversal attacks (e.g., '../../etc/passwd' or absolute paths)."""
    norm = posixpath.normpath(raw_path.replace("\\", "/"))
    return (
        norm.startswith("../")
        or norm == ".."
        or norm.startswith("/")
        or (len(norm) >= 2 and norm[1] == ":")  # Windows drive letter C:
    )


def _is_system_or_junk(norm_path: str) -> bool:
    """Determines if a file is an OS metadata or temporary file."""
    lower = norm_path.lower()
    base = posixpath.basename(lower)
    if base.startswith("._") or base.startswith(".~"):
        return True
    for pat in IGNORED_PATTERNS:
        if pat in lower or lower.startswith(pat):
            return True
    return False


def _normalize_image_if_tiff(file_bytes: bytes, mime_type: str) -> tuple[bytes, str]:
    """Converts TIFF/TIF files to JPEG for OCR and model compatibility."""
    if mime_type not in ("image/tiff", "image/tif"):
        return file_bytes, mime_type
    try:
        with PIL.Image.open(io.BytesIO(file_bytes)) as img:
            if img.mode != "RGB":
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90, optimize=True)
            return buf.getvalue(), "image/jpeg"
    except Exception:
        return file_bytes, mime_type


def _build_folder_tree(doc_paths: list[str]) -> dict[str, Any]:
    """Builds a hierarchical tree dictionary representing folders and files."""
    tree: dict[str, Any] = {}
    for p in doc_paths:
        parts = p.split("/")
        curr = tree
        for part in parts[:-1]:
            if part not in curr or not isinstance(curr[part], dict):
                curr[part] = {}
            curr = curr[part]
        filename = parts[-1]
        if "_files" not in curr:
            curr["_files"] = []
        curr["_files"].append(filename)
    return tree


def _extract_files_from_zip(
    zf: zipfile.ZipFile,
    prefix: str,
    documents: list[ArchiveDocument],
    skipped_files: list[SkippedFile],
    discovered_paths: list[str],
    max_single_file_bytes: int,
    max_compression_ratio: int,
    total_uncompressed: int,
    max_total_uncompressed_bytes: int,
    depth: int = 0,
) -> int:
    """Recursively extract files from a ZipFile, including nested ZIPs.

    Returns updated total_uncompressed byte count.
    """
    MAX_NESTING_DEPTH = 3
    infolist = zf.infolist()

    for info in infolist:
        if info.is_dir():
            continue

        raw_name = info.filename
        norm_name = posixpath.normpath(raw_name.replace("\\", "/"))
        full_path = f"{prefix}/{norm_name}" if prefix else norm_name

        # 1. Zip-Slip Path Traversal Protection
        if _is_zip_slip(raw_name):
            raise ArchiveSecurityError(
                f"Security alert: Malicious path traversal detected in archive entry '{raw_name}'."
            )

        # 2. Decompression Bomb Checks
        file_uncompressed_size = info.file_size
        file_compressed_size = max(info.compress_size, 1)

        if file_uncompressed_size > max_single_file_bytes:
            log.warning("Skipping '%s': exceeds max file size (%.1f MB)",
                        full_path, file_uncompressed_size / (1024 * 1024))
            skipped_files.append(
                SkippedFile(path=full_path, reason="exceeds_size_limit", size_bytes=file_uncompressed_size)
            )
            continue

        ratio = file_uncompressed_size / file_compressed_size
        if ratio > max_compression_ratio and file_uncompressed_size > 1024 * 1024:
            log.warning("Skipping '%s': compression ratio too high (%.1f:1)",
                        full_path, ratio)
            skipped_files.append(
                SkippedFile(path=full_path, reason=f"high_compression_ratio_{ratio:.0f}", size_bytes=file_uncompressed_size)
            )
            continue

        total_uncompressed += file_uncompressed_size
        if total_uncompressed > max_total_uncompressed_bytes:
            log.warning("Total uncompressed size limit reached at '%s'", full_path)
            skipped_files.append(
                SkippedFile(path=full_path, reason="total_size_limit_reached", size_bytes=file_uncompressed_size)
            )
            continue

        # 3. Filter System and Metadata Files
        if _is_system_or_junk(norm_name):
            log.debug("Skipping system/junk file: %s", full_path)
            skipped_files.append(
                SkippedFile(path=full_path, reason="system_metadata", size_bytes=file_uncompressed_size)
            )
            continue

        # 4. Check extension
        ext = Path(norm_name).suffix.lower()

        # 4a. Handle nested ZIP archives
        if ext in NESTED_ARCHIVE_EXTENSIONS and depth < MAX_NESTING_DEPTH:
            log.info("Found nested ZIP: %s (depth=%d), extracting recursively...", full_path, depth)
            try:
                nested_bytes = zf.read(info)
                if nested_bytes and zipfile.is_zipfile(io.BytesIO(nested_bytes)):
                    nested_zf = zipfile.ZipFile(io.BytesIO(nested_bytes))
                    nested_prefix = full_path.rsplit(".", 1)[0]  # strip .zip extension from prefix
                    total_uncompressed = _extract_files_from_zip(
                        nested_zf, nested_prefix, documents, skipped_files,
                        discovered_paths, max_single_file_bytes,
                        max_compression_ratio, total_uncompressed,
                        max_total_uncompressed_bytes, depth + 1,
                    )
                    continue
            except Exception as exc:
                log.warning("Failed to extract nested ZIP '%s': %s", full_path, exc)
                skipped_files.append(
                    SkippedFile(path=full_path, reason=f"nested_zip_error: {exc}", size_bytes=file_uncompressed_size)
                )
                continue

        # 4b. Check if file type is supported
        if ext not in SUPPORTED_DOCUMENT_EXTENSIONS:
            log.debug("Skipping unsupported file type: %s (ext=%s)", full_path, ext)
            skipped_files.append(
                SkippedFile(path=full_path, reason=f"unsupported_type_{ext or 'none'}", size_bytes=file_uncompressed_size)
            )
            continue

        # 5. Extract Content
        try:
            content = zf.read(info)
        except Exception as exc:
            log.warning("Read error for '%s': %s", full_path, exc)
            skipped_files.append(
                SkippedFile(path=full_path, reason=f"read_error: {exc}", size_bytes=file_uncompressed_size)
            )
            continue

        if not content:
            log.debug("Empty file: %s", full_path)
            skipped_files.append(
                SkippedFile(path=full_path, reason="empty_file", size_bytes=0)
            )
            continue

        mime_type = SUPPORTED_DOCUMENT_EXTENSIONS[ext]
        # Normalize TIFF if needed
        content, mime_type = _normalize_image_if_tiff(content, mime_type)

        base_filename = posixpath.basename(full_path)
        folder_path = posixpath.dirname(full_path)

        # Detect non-text visual evidence photos vs clinical/financial text documents
        is_clinical = True
        folder_lower = folder_path.lower()
        base_stem_lower = Path(base_filename).stem.lower()

        if any(f in folder_lower for f in NON_TEXT_PHOTO_FOLDERS):
            is_clinical = False
        elif any(f in base_stem_lower for f in NON_TEXT_PHOTO_FILENAMES):
            is_clinical = False

        doc = ArchiveDocument(
            filename=base_filename,
            relative_path=full_path,
            folder_path=folder_path,
            file_bytes=content,
            mime_type=mime_type,
            size_bytes=len(content),
            is_clinical_text_document=is_clinical,
        )
        documents.append(doc)
        discovered_paths.append(full_path)
        log.info("  ✓ Extracted: %s (%s, %d bytes, clinical_text=%s)",
                 full_path, mime_type, len(content), is_clinical)

    return total_uncompressed


def extract_and_analyze_archive(
    file_bytes: bytes,
    archive_name: str = "archive.zip",
    max_files: int = DEFAULT_MAX_FILES,
    max_single_file_bytes: int = DEFAULT_MAX_SINGLE_FILE_BYTES,
    max_total_uncompressed_bytes: int = DEFAULT_MAX_TOTAL_UNCOMPRESSED_BYTES,
    max_compression_ratio: int = DEFAULT_MAX_COMPRESSION_RATIO,
) -> ArchiveAnalysisResult:
    """Securely inspects and extracts medical documents from a ZIP archive.

    Recursively traverses all sub-folders and nested ZIP archives to find every
    document (PDF, image, Word) at any depth level.

    Args:
        file_bytes: Raw bytes of the ZIP archive.
        archive_name: Filename of the uploaded archive.
        max_files: Maximum allowed files in archive before error.
        max_single_file_bytes: Maximum allowed size for an uncompressed file.
        max_total_uncompressed_bytes: Maximum total uncompressed size allowed.
        max_compression_ratio: Max compression ratio threshold to avoid zip bombs.

    Returns:
        ArchiveAnalysisResult containing valid documents and scan statistics.

    Raises:
        ArchiveFormatError: If the archive is corrupt or invalid.
        ArchiveSecurityError: If Zip-Slip or Zip-Bomb thresholds are breached.
    """
    import re

    log.info("═" * 60)
    log.info("Archive analysis starting: '%s' (%d bytes)", archive_name, len(file_bytes))
    log.info("═" * 60)

    if not file_bytes:
        raise ArchiveFormatError("Uploaded archive is empty (0 bytes).")

    if not zipfile.is_zipfile(io.BytesIO(file_bytes)):
        raise ArchiveFormatError(f"'{archive_name}' is not a valid ZIP file.")

    try:
        zf = zipfile.ZipFile(io.BytesIO(file_bytes))
    except Exception as exc:
        raise ArchiveFormatError(f"Could not open ZIP file: {exc}") from exc

    infolist = zf.infolist()
    if not infolist:
        raise ArchiveFormatError(f"ZIP archive '{archive_name}' contains no entries.")

    if len(infolist) > max_files:
        raise ArchiveSecurityError(
            f"Archive entry count ({len(infolist)}) exceeds maximum allowable entries ({max_files})."
        )

    total_size = sum(i.file_size for i in infolist)
    if total_size > max_total_uncompressed_bytes:
        raise ArchiveSecurityError(
            f"Total uncompressed size ({total_size} bytes) exceeds maximum safety limit ({max_total_uncompressed_bytes} bytes)."
        )

    log.info("ZIP contains %d entries (%d dirs, %d files)",
             len(infolist),
             sum(1 for i in infolist if i.is_dir()),
             sum(1 for i in infolist if not i.is_dir()))

    # Log all entries for debugging
    for info in infolist:
        if not info.is_dir():
            ext = Path(info.filename).suffix.lower()
            log.info("  Entry: %s (ext=%s, size=%d)", info.filename, ext, info.file_size)

    documents: list[ArchiveDocument] = []
    skipped_files: list[SkippedFile] = []
    discovered_paths: list[str] = []

    total_uncompressed = _extract_files_from_zip(
        zf, "", documents, skipped_files, discovered_paths,
        max_single_file_bytes, max_compression_ratio, 0,
        max_total_uncompressed_bytes,
    )

    # If the archive contains an insurance policy PDF, mark redundant pre-rendered PNG page images as non-clinical
    has_policy_pdf = any(
        d.filename.lower().endswith(".pdf") and any(k in d.filename.lower() for k in ("policy", "insurance", "claim"))
        for d in documents
    )
    if has_policy_pdf:
        for doc in documents:
            stem = Path(doc.filename).stem.lower()
            if re.match(r"^(policy_)?page_\d+$", stem) and doc.mime_type.startswith("image/"):
                doc.is_clinical_text_document = False
                log.info("  Marked redundant policy image render as non-clinical: %s", doc.relative_path)

    tree = _build_folder_tree(discovered_paths)

    log.info("═" * 60)
    log.info("Archive analysis complete: %d documents extracted (%d clinical text, %d visual photos), %d skipped",
             len(documents),
             sum(1 for d in documents if d.is_clinical_text_document),
             sum(1 for d in documents if not d.is_clinical_text_document),
             len(skipped_files))
    for s in skipped_files:
        log.info("  Skipped: %s → %s", s.path, s.reason)
    log.info("═" * 60)

    return ArchiveAnalysisResult(
        archive_name=archive_name,
        total_entries_scanned=len(infolist),
        total_uncompressed_bytes=total_uncompressed,
        documents=documents,
        skipped_files=skipped_files,
        folder_tree=tree,
    )
