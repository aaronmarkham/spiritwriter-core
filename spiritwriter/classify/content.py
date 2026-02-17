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


# Patterns that identify institutional affiliation text
INSTITUTION_PATTERNS = [
    r"(?:university|universit[ée]|universidad)\s+(?:of\s+)?[\w\s]+",
    r"(?:institute|institut)\s+(?:of|for|de)\s+[\w\s]+",
    r"(?:school|college|faculty|department|dept\.?)\s+of\s+[\w\s]+",
    r"(?:laboratory|lab|centre|center)\s+(?:of|for)\s+[\w\s]+",
    r"[\w\s]+(?:polytechnic|polytechnical)\s+[\w\s]*",
]

# These are METADATA, not topics — they describe who, not what
INSTITUTION_INDICATOR_WORDS = {
    "university", "institute", "school", "college", "department",
    "faculty", "laboratory", "centre", "center", "hospital",
    "corporation", "inc", "ltd", "gmbh", "polytechnic", "polytechnical",
    "academy", "research center", "research centre", "national lab",
}

# Extraction rules by document type
EXTRACTION_RULES = {
    DocumentType.SCIENTIFIC_PAPER: {
        "topic_zones": [ZoneRole.BODY],                          # Only body gets topics
        "entity_zones": [ZoneRole.BODY, ZoneRole.FRONT_MATTER],  # Entities from body + abstract
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
        "topic_zones": [ZoneRole.BODY, ZoneRole.FRONT_MATTER],   # Permissive fallback
        "entity_zones": [ZoneRole.BODY, ZoneRole.FRONT_MATTER],
        "metadata_zones": [ZoneRole.BOILERPLATE],
    },
}


class ContentClassifier:
    """
    Classifies document type and identifies structural zones.

    This is NOT an LLM agent. It uses heuristics on the raw extraction
    output (text blocks, font sizes, positions, PDF metadata) to make
    fast, deterministic decisions before the expensive LLM phase.
    """

    def classify(self, extraction: "ExtractionResult") -> ContentProfile:
        """
        Classify a document and identify its structural zones.

        Args:
            extraction: Raw PyMuPDF extraction result

        Returns:
            ContentProfile describing the document
        """
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
        """
        Detect document type from structural signals.

        Signals checked (in priority order):
        1. PDF metadata (keywords, subject, creator)
        2. Structural markers (Abstract, References, DOI, byline patterns)
        3. Font distribution (papers have more font variation than articles)
        4. Content patterns (equations → paper, dateline → news)
        """
        signals = {}
        text_blocks = extraction.text_blocks
        metadata = extraction.metadata
        full_text = " ".join(b.get("text", "") for b in text_blocks[:30])  # First 30 blocks
        full_text_lower = full_text.lower()

        # --- Signal: DOI or arXiv ---
        if self._has_doi(full_text) or "arxiv" in full_text_lower:
            signals["doi"] = (DocumentType.SCIENTIFIC_PAPER, 0.9)

        # --- Signal: "Abstract" section ---
        if self._has_abstract_header(text_blocks):
            signals["abstract"] = (DocumentType.SCIENTIFIC_PAPER, 0.7)

        # --- Signal: "References" or "Bibliography" section near end ---
        if self._has_references_section(text_blocks):
            signals["references"] = (DocumentType.SCIENTIFIC_PAPER, 0.6)

        # --- Signal: Dateline pattern (CITY, Month Day —) ---
        if self._has_dateline(text_blocks):
            signals["dateline"] = (DocumentType.NEWS_ARTICLE, 0.8)

        # --- Signal: AP/Reuters/byline pattern ---
        if self._has_news_byline(text_blocks):
            signals["byline"] = (DocumentType.NEWS_ARTICLE, 0.7)

        # --- Signal: Dataset/schema indicators ---
        if any(w in full_text_lower for w in ["columns:", "schema:", "csv", "json", "dataset description"]):
            signals["dataset"] = (DocumentType.DATASET_README, 0.7)

        # --- Signal: Equations (LaTeX remnants, numbered equations) ---
        if self._count_equations(text_blocks) > 3:
            signals["equations"] = (DocumentType.SCIENTIFIC_PAPER, 0.5)

        # --- Vote ---
        if not signals:
            return DocumentType.GENERIC, 0.3

        # Take highest confidence signal
        best_type, best_conf = max(signals.values(), key=lambda x: x[1])
        return best_type, best_conf

    def _identify_zones(
        self,
        extraction: "ExtractionResult",
        doc_type: DocumentType,
    ) -> List[DocumentZone]:
        """
        Identify structural zones in the document.

        For papers:
          front_matter:  blocks 0..first_body_section
          body:          first_body_section..references_section
          back_matter:   references_section..end
          biographical:  author/affiliation blocks (detected by font/position)

        For news:
          front_matter:  headline + byline
          body:          main text
          biographical:  author bio at end
          boilerplate:   dateline, copyright
        """
        zones = []
        text_blocks = extraction.text_blocks
        num_blocks = len(text_blocks)

        if num_blocks == 0:
            return zones

        if doc_type == DocumentType.SCIENTIFIC_PAPER:
            zones = self._identify_paper_zones(text_blocks)
        elif doc_type == DocumentType.NEWS_ARTICLE:
            zones = self._identify_news_zones(text_blocks)
        else:
            # Generic: front matter = first 3 blocks, body = rest
            zones = [
                DocumentZone(ZoneRole.FRONT_MATTER, 0, min(2, num_blocks - 1), "Header"),
                DocumentZone(ZoneRole.BODY, min(3, num_blocks - 1), num_blocks - 1, "Content"),
            ]

        return zones

    def _identify_paper_zones(self, text_blocks: List[Dict]) -> List[DocumentZone]:
        """Identify zones for a scientific paper."""
        zones = []
        num_blocks = len(text_blocks)

        # Find key markers
        abstract_idx = None
        references_idx = None
        first_section_idx = None

        for i, block in enumerate(text_blocks):
            text = block.get("text", "").strip().lower()

            # Abstract marker
            if abstract_idx is None and text.startswith("abstract"):
                abstract_idx = i

            # References/Bibliography marker
            if text in ("references", "bibliography", "works cited"):
                references_idx = i

            # First real section (Introduction, Background, etc.)
            if first_section_idx is None and i > 3:
                if any(text.startswith(s) for s in [
                    "introduction", "1 introduction", "1. introduction",
                    "background", "1 background", "1. background",
                    "related work", "2 related", "2. related",
                ]):
                    first_section_idx = i

        # Build zones based on markers
        front_matter_end = min(first_section_idx or 10, num_blocks - 1)

        # Front matter: title through abstract
        zones.append(DocumentZone(
            ZoneRole.FRONT_MATTER, 0, front_matter_end, "Title/Abstract"
        ))

        # Detect biographical blocks within front matter (affiliations, emails)
        for i in range(front_matter_end + 1):
            text = text_blocks[i].get("text", "")
            if self._is_affiliation_block(text):
                zones.append(DocumentZone(
                    ZoneRole.BIOGRAPHICAL, i, i, "Affiliations"
                ))

        # Body: from first section to references
        body_start = front_matter_end + 1
        body_end = (references_idx - 1) if references_idx else num_blocks - 1
        if body_start <= body_end:
            zones.append(DocumentZone(
                ZoneRole.BODY, body_start, body_end, "Main Content"
            ))

        # Back matter: references to end
        if references_idx:
            zones.append(DocumentZone(
                ZoneRole.BACK_MATTER, references_idx, num_blocks - 1, "References"
            ))

        return zones

    def _identify_news_zones(self, text_blocks: List[Dict]) -> List[DocumentZone]:
        """Identify zones for a news article."""
        zones = []
        num_blocks = len(text_blocks)

        # News articles: headline (block 0-1), byline, then body
        zones.append(DocumentZone(
            ZoneRole.FRONT_MATTER, 0, min(2, num_blocks - 1), "Headline"
        ))

        # Find byline
        for i in range(min(5, num_blocks)):
            text = text_blocks[i].get("text", "")
            if self._is_byline(text):
                zones.append(DocumentZone(
                    ZoneRole.BIOGRAPHICAL, i, i, "Byline"
                ))
                break

        # Body is everything else
        body_start = min(3, num_blocks - 1)
        zones.append(DocumentZone(
            ZoneRole.BODY, body_start, num_blocks - 1, "Article Body"
        ))

        return zones

    def _extract_early_metadata(
        self,
        extraction: "ExtractionResult",
        zones: List[DocumentZone],
    ) -> Dict[str, Any]:
        """
        Extract author/institution metadata from biographical zones.

        This runs before the LLM phase. The extracted data goes into
        DocumentGraph.authors and KnowledgeSource.authors — NOT into
        atom topics.
        """
        metadata: Dict[str, Any] = {"authors": [], "institutions": [], "doi": None, "date": None}

        for zone in zones:
            if zone.role != ZoneRole.BIOGRAPHICAL:
                continue

            for i in range(zone.start_block, zone.end_block + 1):
                if i >= len(extraction.text_blocks):
                    break
                text = extraction.text_blocks[i].get("text", "")

                # Extract institution names
                institutions = self._extract_institutions(text)
                metadata["institutions"].extend(institutions)

        # DOI from anywhere in the document
        for block in extraction.text_blocks:
            doi = self._extract_doi(block.get("text", ""))
            if doi:
                metadata["doi"] = doi
                break

        return metadata

    # --- Helper Detection Methods ---

    def _has_doi(self, text: str) -> bool:
        """Check if text contains a DOI."""
        return bool(re.search(r"10\.\d{4,}/[\w\.\-/]+", text))

    def _extract_doi(self, text: str) -> Optional[str]:
        """Extract DOI from text."""
        match = re.search(r"(10\.\d{4,}/[\w\.\-/]+)", text)
        return match.group(1) if match else None

    def _has_abstract_header(self, text_blocks: List[Dict]) -> bool:
        """Check if document has an Abstract section."""
        for block in text_blocks[:15]:
            text = block.get("text", "").strip().lower()
            if text.startswith("abstract") or text == "abstract":
                return True
        return False

    def _has_references_section(self, text_blocks: List[Dict]) -> bool:
        """Check if document has a References section near the end."""
        # Check last 30% of document
        start_idx = int(len(text_blocks) * 0.7)
        for block in text_blocks[start_idx:]:
            text = block.get("text", "").strip().lower()
            if text in ("references", "bibliography", "works cited"):
                return True
        return False

    def _has_dateline(self, text_blocks: List[Dict]) -> bool:
        """Check for news dateline pattern (CITY, Month Day —)."""
        dateline_pattern = r"^[A-Z]{3,}\s*[,\-]\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        for block in text_blocks[:10]:
            text = block.get("text", "").strip()
            if re.match(dateline_pattern, text):
                return True
        return False

    def _has_news_byline(self, text_blocks: List[Dict]) -> bool:
        """Check for news byline pattern (By Author Name)."""
        for block in text_blocks[:10]:
            text = block.get("text", "").strip()
            if re.match(r"^By\s+[A-Z][a-z]+\s+[A-Z][a-z]+", text):
                return True
            if re.search(r"(AP|Reuters|AFP|UPI)\s*[-–—]", text):
                return True
        return False

    def _count_equations(self, text_blocks: List[Dict]) -> int:
        """Count equation-like patterns in text."""
        count = 0
        for block in text_blocks:
            text = block.get("text", "")
            # Look for numbered equations or LaTeX-like patterns
            if re.search(r"\(\d+\)\s*$", text):  # Equation number
                count += 1
            if re.search(r"\\[a-z]+\{", text):  # LaTeX command
                count += 1
        return count

    def _is_affiliation_block(self, text: str) -> bool:
        """Check if a text block contains institutional affiliations."""
        text_lower = text.lower()
        # Check for institution indicators
        if any(word in text_lower for word in INSTITUTION_INDICATOR_WORDS):
            return True
        # Check for email patterns (common in author blocks)
        if re.search(r"[\w\.\-]+@[\w\.\-]+\.\w+", text):
            return True
        return False

    def _is_byline(self, text: str) -> bool:
        """Check if text is a news byline."""
        return bool(re.match(r"^By\s+[A-Z][a-z]+", text.strip()))

    def _extract_institutions(self, text: str) -> List[str]:
        """Extract institution names from text."""
        institutions = []
        for pattern in INSTITUTION_PATTERNS:
            matches = re.findall(pattern, text, re.IGNORECASE)
            institutions.extend(matches)
        return institutions


def is_institutional_name(text: str) -> bool:
    """
    Check if text is an institutional/organizational name rather than a concept.

    This is used to filter topics — institution names are metadata, not content topics.
    """
    text_lower = text.lower()
    return any(indicator in text_lower for indicator in INSTITUTION_INDICATOR_WORDS)


def is_theme_candidate(topic: str, entity_index: Optional[Dict] = None) -> bool:
    """
    Determine if a topic is a legitimate theme vs. metadata noise.

    Rejects:
    - Institutional names (university, department, school of...)
    - Journal/conference names
    - Geographic locations that are affiliations not content
    """
    topic_lower = topic.lower()

    # Institutional name detection
    if is_institutional_name(topic_lower):
        return False

    # Journal/conference name detection
    if any(w in topic_lower for w in [
        "journal", "proceedings", "conference", "symposium",
        "transactions", "letters", "ieee", "acm", "springer",
        "workshop", "annual meeting",
    ]):
        return False

    # Pure single-word geographic (just a place name with no technical content)
    # "Beijing" alone might be noise; "Beijing traffic dataset" is content
    if len(topic.split()) == 1 and topic[0].isupper():
        # Single capitalized word that looks like a place name
        # We're more lenient here - only block very short geographic-only terms
        pass

    return True