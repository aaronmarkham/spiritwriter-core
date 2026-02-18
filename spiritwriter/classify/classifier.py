"""Content-Aware Document Classifier.

Classifies document type and identifies structural zones BEFORE LLM analysis.
Uses heuristics on raw PyMuPDF extraction (text blocks, font sizes, positions)
to make fast, deterministic decisions.

This enables the DocumentIngestorAgent to:
1. Use type-specific LLM prompts (papers vs news vs blog posts)
2. Filter topics from metadata zones (affiliations, author bios)
3. Auto-set SourceType for KnowledgeSource
"""

from typing import Dict, List, Tuple, Any

from spiritwriter.models.document import (
    DocumentType,
    ZoneRole,
    ContentProfile,
    DocumentZone,
)
from spiritwriter.classify.rules import EXTRACTION_RULES
from spiritwriter.classify.signals import (
    has_doi,
    has_abstract_header,
    has_references_section,
    has_dateline,
    has_news_byline,
    count_equations,
    extract_doi,
    extract_institutions,
)
from spiritwriter.classify.zones import identify_zones


class ContentClassifier:
    """Classifies document type and identifies structural zones.

    Uses heuristics on raw extraction output (text blocks, font sizes,
    positions, PDF metadata) — NOT an LLM agent.
    """

    def classify(self, extraction: "ExtractionResult") -> ContentProfile:
        """Classify a document and identify its structural zones."""
        doc_type, confidence = self._detect_document_type(extraction)
        zones = identify_zones(extraction.text_blocks, doc_type)
        metadata = self._extract_early_metadata(extraction, zones)
        rules = EXTRACTION_RULES.get(doc_type, EXTRACTION_RULES[DocumentType.GENERIC])

        return ContentProfile(
            document_type=doc_type,
            confidence=confidence,
            zones=zones,
            detected_authors=metadata.get("authors", []),
            detected_institutions=metadata.get("institutions", []),
            detected_doi=metadata.get("doi"),
            detected_date=metadata.get("date"),
            topic_extraction_zones=rules["topic_zones"],
            entity_extraction_zones=rules["entity_zones"],
            metadata_zones=rules["metadata_zones"],
        )

    def _detect_document_type(
        self, extraction: "ExtractionResult"
    ) -> Tuple[DocumentType, float]:
        """Detect document type from structural signals."""
        signals = {}
        text_blocks = extraction.text_blocks
        full_text = " ".join(b.get("text", "") for b in text_blocks[:30])
        full_text_lower = full_text.lower()

        if has_doi(full_text) or "arxiv" in full_text_lower:
            signals["doi"] = (DocumentType.SCIENTIFIC_PAPER, 0.9)
        if has_abstract_header(text_blocks):
            signals["abstract"] = (DocumentType.SCIENTIFIC_PAPER, 0.7)
        if has_references_section(text_blocks):
            signals["references"] = (DocumentType.SCIENTIFIC_PAPER, 0.6)
        if has_dateline(text_blocks):
            signals["dateline"] = (DocumentType.NEWS_ARTICLE, 0.8)
        if has_news_byline(text_blocks):
            signals["byline"] = (DocumentType.NEWS_ARTICLE, 0.7)
        if any(w in full_text_lower for w in ["columns:", "schema:", "csv", "json", "dataset description"]):
            signals["dataset"] = (DocumentType.DATASET_README, 0.7)
        if count_equations(text_blocks) > 3:
            signals["equations"] = (DocumentType.SCIENTIFIC_PAPER, 0.5)

        if not signals:
            return DocumentType.GENERIC, 0.3

        best_type, best_conf = max(signals.values(), key=lambda x: x[1])
        return best_type, best_conf

    def _extract_early_metadata(
        self,
        extraction: "ExtractionResult",
        zones: List[DocumentZone],
    ) -> Dict[str, Any]:
        """Extract author/institution metadata from biographical zones."""
        metadata: Dict[str, Any] = {"authors": [], "institutions": [], "doi": None, "date": None}

        for zone in zones:
            if zone.role != ZoneRole.BIOGRAPHICAL:
                continue
            for i in range(zone.start_block, zone.end_block + 1):
                if i >= len(extraction.text_blocks):
                    break
                text = extraction.text_blocks[i].get("text", "")
                institutions = extract_institutions(text)
                metadata["institutions"].extend(institutions)

        for block in extraction.text_blocks:
            doi = extract_doi(block.get("text", ""))
            if doi:
                metadata["doi"] = doi
                break

        return metadata
