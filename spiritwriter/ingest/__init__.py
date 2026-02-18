"""Document ingestion pipeline."""

from spiritwriter.ingest.extraction import ExtractionResult
from spiritwriter.ingest.document import (
    DocumentIngestor,
    DocumentIngestorAgent,  # Backward compatibility alias
)

__all__ = [
    "ExtractionResult",
    "DocumentIngestor",
    "DocumentIngestorAgent",
]