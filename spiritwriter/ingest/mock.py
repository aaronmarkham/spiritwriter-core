"""Mock / heuristic analysis — used when no LLM provider is available."""

import re
from typing import List, Dict, Any

from spiritwriter.models.document import AtomType, DocumentAtom, DocumentGraph, ContentProfile
from spiritwriter.classify import is_theme_candidate
from spiritwriter.stopwords import THEME_STOPWORDS
from spiritwriter.ingest.extraction import ExtractionResult, find_caption

# Common English + academic stopwords for topic extraction filtering.
# Combines THEME_STOPWORDS with additional function words and generic terms.
_STOPWORDS = THEME_STOPWORDS | frozenset({
    # Function words not in THEME_STOPWORDS
    "the", "and", "for", "are", "was", "has", "but", "not", "all",
    "can", "will", "had", "were", "when", "where", "what", "how",
    "who", "then", "did", "its", "our", "may", "one", "two",
    "therefore", "thus", "hence", "about", "above", "below", "under",
    "through", "after", "before", "while", "during", "since",
    "further", "here", "there", "still", "use",
    # Generic academic terms not already in THEME_STOPWORDS
    "proposed", "present", "presented", "show", "shown", "shows",
    "result", "approach", "method", "paper", "work", "study",
    "model", "models", "data", "set", "first", "second", "new",
    "different", "between", "methods", "figure", "table", "section",
    "algorithms", "techniques", "framework", "feature", "features",
    "representation",
})


def mock_analyze(
    doc_id: str, source_path: str, extraction: ExtractionResult,
    profile: ContentProfile
) -> DocumentGraph:
    """
    Mock analysis for testing without LLM calls.
    Classifies blocks using heuristics (font size, position, bold).
    Uses ContentProfile for zone-aware topic filtering.
    """
    atoms: Dict[str, DocumentAtom] = {}
    hierarchy: Dict[str, List[str]] = {}
    flow: List[str] = []
    current_section_id = None

    # Heuristic classification based on font size and position
    max_font_size = max(
        (b["font_size"] for b in extraction.text_blocks), default=12
    )

    for i, block in enumerate(extraction.text_blocks):
        atom_id = f"{doc_id}_atom_{i:03d}"
        text = block["text"]

        # Classify by heuristics
        if i == 0 and block["font_size"] >= max_font_size * 0.9:
            atom_type = AtomType.TITLE
            importance = 1.0
        elif block["is_bold"] and block["font_size"] > 13 and len(text) < 100:
            atom_type = AtomType.SECTION_HEADER
            importance = 0.8
        elif text.lower().startswith("abstract"):
            atom_type = AtomType.ABSTRACT
            importance = 0.9
        elif re.match(r"^\[\d+\]", text) or re.match(r"^\d+\.\s+\w+.*\d{4}", text):
            atom_type = AtomType.CITATION
            importance = 0.3
        elif text.startswith('"') or text.startswith('\u201c'):
            atom_type = AtomType.QUOTE
            importance = 0.6
        else:
            atom_type = AtomType.PARAGRAPH
            importance = 0.5

        # Extract and filter topics using content-aware rules
        raw_topics = extract_mock_topics(text)
        filtered_topics = [t for t in raw_topics if is_theme_candidate(t)]

        # If block is in a metadata zone, don't extract topics
        if profile.is_metadata_block(i):
            filtered_topics = []

        entities = extract_mock_entities(text)
        relationships = extract_mock_relationships(text, entities)

        atom = DocumentAtom(
            atom_id=atom_id,
            atom_type=atom_type,
            content=text,
            source_page=block["page"],
            source_location=block["bbox"],
            importance_score=importance,
            topics=filtered_topics,
            entities=entities,
            relationships=relationships,
        )

        atoms[atom_id] = atom
        flow.append(atom_id)

        # Build hierarchy
        if atom_type == AtomType.SECTION_HEADER:
            current_section_id = atom_id
            hierarchy[atom_id] = []
        elif current_section_id and atom_type in (AtomType.PARAGRAPH, AtomType.QUOTE):
            hierarchy[current_section_id].append(atom_id)

    # Process images
    for i, img_info in enumerate(extraction.images):
        atom_id = f"{doc_id}_fig_{i:03d}"
        caption = find_caption(img_info, extraction.text_blocks)

        atom = DocumentAtom(
            atom_id=atom_id,
            atom_type=AtomType.FIGURE,
            content=caption or f"Figure on page {img_info['page'] + 1}",
            raw_data=img_info["image_bytes"],
            source_page=img_info["page"],
            source_location=img_info["bbox"],
            caption=caption,
            data_summary=caption or "Figure extracted from document",
            importance_score=0.7,
        )
        atoms[atom_id] = atom

    # Generate mock summaries from first few text blocks
    all_text = " ".join(b["text"] for b in extraction.text_blocks[:5])
    title = extraction.metadata.get("title", "")
    if not title and extraction.text_blocks:
        title = extraction.text_blocks[0]["text"][:100]

    graph = DocumentGraph(
        document_id=doc_id,
        source_path=source_path,
        atoms=atoms,
        hierarchy=hierarchy,
        references={},
        flow=flow,
        one_sentence=f"Document about: {title}" if title else "Document analysis pending.",
        one_paragraph=all_text[:300] if all_text else "",
        full_summary=all_text[:600] if all_text else "",
        figures=[aid for aid, a in atoms.items() if a.atom_type == AtomType.FIGURE],
        tables=[aid for aid, a in atoms.items() if a.atom_type == AtomType.TABLE],
        key_quotes=[aid for aid, a in atoms.items() if a.atom_type == AtomType.QUOTE],
        title=title,
        authors=([extraction.metadata["author"]] if extraction.metadata.get("author") else []),
        page_count=extraction.page_count,
    )

    return graph


def extract_mock_topics(text: str) -> List[str]:
    """Extract meaningful topics from text using heuristics (mock mode).

    Finds multi-word capitalized phrases and significant technical terms,
    filtering out common academic boilerplate.
    """
    topics = []

    # Multi-word capitalized phrases (e.g., "Knowledge Graph", "Large Language Model")
    for match in re.finditer(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b', text[:500]):
        phrase = match.group(1).lower()
        words = phrase.split()
        # Keep if at least one word is NOT a stopword (meaningful compound term)
        if any(w not in _STOPWORDS for w in words):
            topics.append(phrase)

    # Single significant capitalized words (not at sentence start, min 6 chars)
    for match in re.finditer(r'(?<=[a-z]\s)([A-Z][a-z]{5,})\b', text[:500]):
        word = match.group(1).lower()
        if word not in _STOPWORDS:
            topics.append(word)

    # Deduplicate while preserving order
    seen = set()
    unique_topics = []
    for t in topics:
        if t not in seen:
            seen.add(t)
            unique_topics.append(t)

    return unique_topics[:3]


def extract_mock_entities(text: str) -> List[str]:
    """Extract entities from text using heuristics (mock mode).

    Finds: acronyms (LLM, KG, NLP), capitalized phrases (Knowledge Graph),
    and named entities that aren't common academic boilerplate.
    """
    entities = set()

    # Acronyms: 3-5 uppercase letters, optionally with digits
    # These are almost always meaningful (LLM, KG, NLP, GPT, DNN, etc.)
    for match in re.finditer(r'\b([A-Z][A-Z0-9]{2,4})\b', text):
        acronym = match.group(1)
        if acronym.lower() not in _STOPWORDS:
            entities.add(acronym)

    # Capitalized multi-word phrases — must not be at sentence start
    # Look for "... word Word Word ..." patterns (mid-sentence capitalization)
    for match in re.finditer(r'(?<=[a-z.,;:]\s)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)', text):
        phrase = match.group(1)
        words = phrase.lower().split()
        # Skip if all words are stopwords
        if not all(w in _STOPWORDS for w in words) and len(phrase) > 5:
            entities.add(phrase)

    return list(entities)[:5]


def extract_mock_relationships(text: str, entities: List[str]) -> List[str]:
    """Extract simple relationships between entities found in the same text block."""
    if len(entities) < 2:
        return []

    relationships = []
    # Co-occurrence: entities in the same block are related
    for i in range(min(len(entities) - 1, 3)):
        relationships.append(f"{entities[i]} <-> {entities[i+1]}")

    return relationships[:3]
