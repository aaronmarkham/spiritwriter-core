"""Multi-format document loading — basic text ingestion across formats.

Turn documents (markdown, text, PDF, …) into a ``{source_ref: text}``
mapping that downstream extraction/KB layers can consume.

This is the lightweight, format-dispatching text path. For rich single-PDF
structural analysis (zones, figures, reading-order graph) use
``DocumentIngestor`` instead — that path is intentionally PDF-shaped.

Text formats need no extra dependencies. PDF uses PyMuPDF (the optional
``[ingest]``/``fitz`` dependency); without it, PDFs raise a clear error
rather than failing obscurely.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Union

# Plain-text formats read directly; PDFs go through PyMuPDF. New formats
# (e.g. .docx, .html) slot in by adding a suffix + an extractor branch.
TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".text", ".rst"}
PDF_SUFFIXES = {".pdf"}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | PDF_SUFFIXES


class UnsupportedDocument(ValueError):
    """Raised for a file whose format has no loader."""


def extract_pdf_text(path: Path) -> str:
    """Extract plain text from a PDF (text only — no figure rendering)."""
    try:
        import fitz  # PyMuPDF
    except ImportError as e:  # pragma: no cover - depends on optional extra
        raise UnsupportedDocument(
            f"{path.name}: PDF support requires PyMuPDF (pip install pymupdf)"
        ) from e
    doc = fitz.open(str(path))
    try:
        return "\n\n".join(page.get_text("text") for page in doc).strip()
    finally:
        doc.close()


def extract_document_text(path: Union[str, Path]) -> str:
    """Extract plain text from a single document, dispatched by extension."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in PDF_SUFFIXES:
        return extract_pdf_text(path)
    if suffix in TEXT_SUFFIXES:
        return path.read_text(encoding="utf-8")
    raise UnsupportedDocument(f"{path.name}: unsupported document format {suffix!r}")


def load_documents(source: Union[str, Path, Dict[str, str]]) -> Dict[str, str]:
    """Load one or many documents into a ``{source_ref: text}`` mapping.

    - ``dict``: returned as-is (already-loaded text passes straight through).
    - directory: every supported file at the top level, sorted by name;
      unsupported files are skipped silently.
    - file: that single document (unsupported format raises).
    """
    if isinstance(source, dict):
        return source
    path = Path(source)
    if path.is_dir():
        out: Dict[str, str] = {}
        for f in sorted(path.iterdir()):
            if f.is_file() and f.suffix.lower() in SUPPORTED_SUFFIXES:
                try:
                    out[f"doc:{f.name}"] = extract_document_text(f)
                except UnsupportedDocument:
                    continue
        return out
    if path.is_file():
        return {f"doc:{path.name}": extract_document_text(path)}
    raise FileNotFoundError(f"sources path not found: {source}")
