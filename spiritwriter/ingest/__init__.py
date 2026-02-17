"""Document ingestion pipeline."""

from .document import (
    ExtractionResult,
    DocumentIngestor,
    DocumentIngestorAgent,  # Backward compatibility alias
)

__all__ = [
    "ExtractionResult",
    "DocumentIngestor", 
    "DocumentIngestorAgent",
]