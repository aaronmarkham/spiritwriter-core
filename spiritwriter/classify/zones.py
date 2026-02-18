"""Zone identification functions for document structural analysis."""

from typing import Dict, List

from spiritwriter.models.document import DocumentType, ZoneRole, DocumentZone
from spiritwriter.classify.signals import is_affiliation_block, is_byline


def identify_zones(
    text_blocks: List[Dict],
    doc_type: DocumentType,
) -> List[DocumentZone]:
    """Identify structural zones in the document."""
    num_blocks = len(text_blocks)
    if num_blocks == 0:
        return []

    if doc_type == DocumentType.SCIENTIFIC_PAPER:
        return identify_paper_zones(text_blocks)
    elif doc_type == DocumentType.NEWS_ARTICLE:
        return identify_news_zones(text_blocks)
    else:
        return [
            DocumentZone(ZoneRole.FRONT_MATTER, 0, min(2, num_blocks - 1), "Header"),
            DocumentZone(ZoneRole.BODY, min(3, num_blocks - 1), num_blocks - 1, "Content"),
        ]


def identify_paper_zones(text_blocks: List[Dict]) -> List[DocumentZone]:
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
        if is_affiliation_block(text):
            zones.append(DocumentZone(ZoneRole.BIOGRAPHICAL, i, i, "Affiliations"))

    body_start = front_matter_end + 1
    body_end = (references_idx - 1) if references_idx else num_blocks - 1
    if body_start <= body_end:
        zones.append(DocumentZone(ZoneRole.BODY, body_start, body_end, "Main Content"))

    if references_idx:
        zones.append(DocumentZone(ZoneRole.BACK_MATTER, references_idx, num_blocks - 1, "References"))

    return zones


def identify_news_zones(text_blocks: List[Dict]) -> List[DocumentZone]:
    """Identify zones for a news article."""
    zones = []
    num_blocks = len(text_blocks)

    zones.append(DocumentZone(ZoneRole.FRONT_MATTER, 0, min(2, num_blocks - 1), "Headline"))
    for i in range(min(5, num_blocks)):
        text = text_blocks[i].get("text", "")
        if is_byline(text):
            zones.append(DocumentZone(ZoneRole.BIOGRAPHICAL, i, i, "Byline"))
            break
    zones.append(DocumentZone(ZoneRole.BODY, min(3, num_blocks - 1), num_blocks - 1, "Article Body"))
    return zones
