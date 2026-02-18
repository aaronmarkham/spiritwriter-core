"""
Content-Aware Document Classifier

Classifies document type and identifies structural zones BEFORE LLM analysis.
Uses heuristics on raw PyMuPDF extraction (text blocks, font sizes, positions)
to make fast, deterministic decisions.

This enables the DocumentIngestorAgent to:
1. Use type-specific LLM prompts (papers vs news vs blog posts)
2. Filter topics from metadata zones (affiliations, author bios)
3. Auto-set SourceType for KnowledgeSource
"""

import re
from typing import Dict, List, Tuple, Any, Optional

from spiritwriter.models.document import (
    DocumentType,
    ZoneRole,
    DocumentZone,
    ContentProfile,
)
from spiritwriter.stopwords import (
    INSTITUTION_PATTERNS,
    INSTITUTION_INDICATOR_WORDS,
    is_institutional_name,
    is_venue_name,
    is_theme_candidate,
)


# Extraction rules by document type
EXTRACTION_RULES = {
    DocumentType.SCIENTIFIC_PAPER: {
        "topic_zones": [ZoneRole.BODY],
        "entity_zones": [ZoneRole.BODY, ZoneRole.FRONT_MATTER],
        "metadata_zones": [ZoneRole.BIOGRAPHICAL, ZoneRole.BOILERPLATE, ZoneRole.BACK_MATTER],
    },
    DocumentType.NEWS_ARTICLE: {
        "topic_zones": [ZoneRole.BODY],
        "entity_zones": [ZoneRole.BODY, ZoneRole.FRONT_MATTER],
        "metadata_zones": [ZoneRole.BIOGRAPHICAL, ZoneRole.BOILERPLATE],
    },
    DocumentType.BLOG_POST: {
        "topic_zones": [ZoneRole.BODY],
        "entity_zones": [ZoneRole.BODY],
        "metadata_zones": [ZoneRole.BIOGRAPHICAL, ZoneRole.BOILERPLATE],
    },
    DocumentType.DATASET_README: {
        "topic_zones": [ZoneRole.BODY],
        "entity_zones": [ZoneRole.BODY],
        "metadata_zones": [ZoneRole.BIOGRAPHICAL, ZoneRole.BOILERPLATE, ZoneRole.BACK_MATTER],
    },
    DocumentType.GENERIC: {
        "topic_zones": [ZoneRole.BODY, ZoneRole.FRONT_MATTER],
        "entity_zones": [ZoneRole.BODY, ZoneRole.FRONT_MATTER],
        "metadata_zones": [ZoneRole.BOILERPLATE],
    },
}


class ContentClassifier:
    """Classifies document type and identifies structural zones.

    Uses heuristics on raw extraction output (text blocks, font sizes,
    positions, PDF metadata) — NOT an LLM agent.
    """

    def classify(self, extraction: "ExtractionResult") -> ContentProfile:
        """Classify a document and identify its structural zones."""
        doc_type, confidence = self._detect_document_type(extraction)
        zones = self._identify_zones(extraction, doc_type)
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

        if self._has_doi(full_text) or "arxiv" in full_text_lower:
            signals["doi"] = (DocumentType.SCIENTIFIC_PAPER, 0.9)
        if self._has_abstract_header(text_blocks):
            signals["abstract"] = (DocumentType.SCIENTIFIC_PAPER, 0.7)
        if self._has_references_section(text_blocks):
            signals["references"] = (DocumentType.SCIENTIFIC_PAPER, 0.6)
        if self._has_dateline(text_blocks):
            signals["dateline"] = (DocumentType.NEWS_ARTICLE, 0.8)
        if self._has_news_byline(text_blocks):
            signals["byline"] = (DocumentType.NEWS_ARTICLE, 0.7)
        if any(w in full_text_lower for w in ["columns:", "schema:", "csv", "json", "dataset description"]):
            signals["dataset"] = (DocumentType.DATASET_README, 0.7)
        if self._count_equations(text_blocks) > 3:
            signals["equations"] = (DocumentType.SCIENTIFIC_PAPER, 0.5)

        if not signals:
            return DocumentType.GENERIC, 0.3

        best_type, best_conf = max(signals.values(), key=lambda x: x[1])
        return best_type, best_conf

    def _identify_zones(
        self,
        extraction: "ExtractionResult",
        doc_type: DocumentType,
    ) -> List[DocumentZone]:
        """Identify structural zones in the document."""
        text_blocks = extraction.text_blocks
        num_blocks = len(text_blocks)
        if num_blocks == 0:
            return []

        if doc_type == DocumentType.SCIENTIFIC_PAPER:
            return self._identify_paper_zones(text_blocks)
        elif doc_type == DocumentType.NEWS_ARTICLE:
            return self._identify_news_zones(text_blocks)
        else:
            return [
                DocumentZone(ZoneRole.FRONT_MATTER, 0, min(2, num_blocks - 1), "Header"),
                DocumentZone(ZoneRole.BODY, min(3, num_blocks - 1), num_blocks - 1, "Content"),
            ]

    def _identify_paper_zones(self, text_blocks: List[Dict]) -> List[DocumentZone]:
        """Identify zones for a scientific paper."""
        zones = []
        num_blocks = len(text_blocks)

        abstract_idx = None
        references_idx = None
        first_section_idx = None

        for i, block in enumerate(text_blocks):
            text = block.get("text", "").strip().lower()
            if abstract_idx is None and text.startswith("abstract"):
                abstract_idx = i
            if text in ("references", "bibliography", "works cited"):
                references_idx = i
            if first_section_idx is None and i > 3:
                if any(text.startswith(s) for s in [
                    "introduction", "1 introduction", "1. introduction",
                    "background", "1 background", "1. background",
                    "related work", "2 related", "2. related",
                ]):
                    first_section_idx = i

        front_matter_end = min(first_section_idx or 10, num_blocks - 1)
        zones.append(DocumentZone(ZoneRole.FRONT_MATTER, 0, front_matter_end, "Title/Abstract"))

        for i in range(front_matter_end + 1):
            text = text_blocks[i].get("text", "")
            if self._is_affiliation_block(text):
                zones.append(DocumentZone(ZoneRole.BIOGRAPHICAL, i, i, "Affiliations"))

        body_start = front_matter_end + 1
        body_end = (references_idx - 1) if references_idx else num_blocks - 1
        if body_start <= body_end:
            zones.append(DocumentZone(ZoneRole.BODY, body_start, body_end, "Main Content"))

        if references_idx:
            zones.append(DocumentZone(ZoneRole.BACK_MATTER, references_idx, num_blocks - 1, "References"))

        return zones

    def _identify_news_zones(self, text_blocks: List[Dict]) -> List[DocumentZone]:
        """Identify zones for a news article."""
        zones = []
        num_blocks = len(text_blocks)

        zones.append(DocumentZone(ZoneRole.FRONT_MATTER, 0, min(2, num_blocks - 1), "Headline"))
        for i in range(min(5, num_blocks)):
            text = text_blocks[i].get("text", "")
            if self._is_byline(text):
                zones.append(DocumentZone(ZoneRole.BIOGRAPHICAL, i, i, "Byline"))
                break
        zones.append(DocumentZone(ZoneRole.BODY, min(3, num_blocks - 1), num_blocks - 1, "Article Body"))
        return zones

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
                institutions = self._extract_institutions(text)
                metadata["institutions"].extend(institutions)

        for block in extraction.text_blocks:
            doi = self._extract_doi(block.get("text", ""))
            if doi:
                metadata["doi"] = doi
                break

        return metadata

    # --- Helper methods ---

    def _has_doi(self, text: str) -> bool:
        return bool(re.search(r"10\.\d{4,}/[\w\.\-/]+", text))

    def _extract_doi(self, text: str) -> Optional[str]:
        match = re.search(r"(10\.\d{4,}/[\w\.\-/]+)", text)
        return match.group(1) if match else None

    def _has_abstract_header(self, text_blocks: List[Dict]) -> bool:
        for block in text_blocks[:15]:
            text = block.get("text", "").strip().lower()
            if text.startswith("abstract") or text == "abstract":
                return True
        return False

    def _has_references_section(self, text_blocks: List[Dict]) -> bool:
        start_idx = int(len(text_blocks) * 0.7)
        for block in text_blocks[start_idx:]:
            text = block.get("text", "").strip().lower()
            if text in ("references", "bibliography", "works cited"):
                return True
        return False

    def _has_dateline(self, text_blocks: List[Dict]) -> bool:
        pattern = r"^[A-Z]{3,}\s*[,\-]\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        for block in text_blocks[:10]:
            if re.match(pattern, block.get("text", "").strip()):
                return True
        return False

    def _has_news_byline(self, text_blocks: List[Dict]) -> bool:
        for block in text_blocks[:10]:
            text = block.get("text", "").strip()
            if re.match(r"^By\s+[A-Z][a-z]+\s+[A-Z][a-z]+", text):
                return True
            if re.search(r"(AP|Reuters|AFP|UPI)\s*[-–—]", text):
                return True
        return False

    def _count_equations(self, text_blocks: List[Dict]) -> int:
        count = 0
        for block in text_blocks:
            text = block.get("text", "")
            if re.search(r"\(\d+\)\s*$", text):
                count += 1
            if re.search(r"\\[a-z]+\{", text):
                count += 1
        return count

    def _is_affiliation_block(self, text: str) -> bool:
        text_lower = text.lower()
        if any(word in text_lower for word in INSTITUTION_INDICATOR_WORDS):
            return True
        if re.search(r"[\w\.\-]+@[\w\.\-]+\.\w+", text):
            return True
        return False

    def _is_byline(self, text: str) -> bool:
        return bool(re.match(r"^By\s+[A-Z][a-z]+", text.strip()))

    def _extract_institutions(self, text: str) -> List[str]:
        institutions = []
        for pattern in INSTITUTION_PATTERNS:
            matches = pattern.findall(text)
            institutions.extend(matches)
        return institutions
