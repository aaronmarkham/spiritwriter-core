"""Document ingestion pipeline.

Two front-ends, by need:
- ``load_documents`` / ``extract_document_text`` — basic multi-format text
  ingestion (markdown, text, PDF). Feeds ``fabric.familiarize`` to align
  concepts from many sources into one KB.
- ``DocumentIngestor`` — rich single-PDF structural analysis (zones,
  figures, reading-order graph).
"""

from spiritwriter.ingest.extraction import ExtractionResult
from spiritwriter.ingest.loaders import (
    extract_document_text,
    load_documents,
    UnsupportedDocument,
    SUPPORTED_SUFFIXES,
)
from spiritwriter.ingest.document import (
    DocumentIngestor,
    DocumentIngestorAgent,  # Backward compatibility alias
)

__all__ = [
    "ExtractionResult",
    "extract_document_text",
    "load_documents",
    "UnsupportedDocument",
    "SUPPORTED_SUFFIXES",
    "DocumentIngestor",
    "DocumentIngestorAgent",
]
