"""Prompt builders for LLM-based document analysis — pure string returns, no state."""

from typing import List, Optional, Dict, Any

from spiritwriter.models.document import ContentProfile
from spiritwriter.ingest.extraction import ExtractionResult


def build_text_context_for_chunk(
    blocks: List[Dict[str, Any]],
    block_indices: List[int],
) -> str:
    """Build text context for a specific chunk of blocks.

    Args:
        blocks: The text blocks in this chunk
        block_indices: The original indices of these blocks in the full document
    """
    lines = []
    for block, idx in zip(blocks, block_indices):
        page = block["page"] + 1  # 1-indexed
        font_hint = f" [size={block['font_size']:.0f}]" if block["font_size"] > 14 else ""
        bold_hint = " [BOLD]" if block["is_bold"] else ""
        lines.append(f"[Block {idx}, Page {page}{font_hint}{bold_hint}]\n{block['text']}\n")
    return "\n".join(lines)


def build_summary_context(extraction: ExtractionResult, max_chars: int = 15000) -> str:
    """Build abbreviated context for summary generation.

    Includes title/abstract area (first ~15 blocks) and conclusion area (last ~10 blocks).
    Summaries don't need every block — just the key sections.
    """
    blocks = extraction.text_blocks
    num_blocks = len(blocks)

    if num_blocks <= 30:
        # Small doc — use everything
        indices = list(range(num_blocks))
    else:
        # First 15 (title, abstract, intro) + last 10 (conclusion, summary)
        indices = list(range(min(15, num_blocks))) + list(range(max(0, num_blocks - 10), num_blocks))
        # Deduplicate if overlap
        indices = sorted(set(indices))

    lines = []
    total_chars = 0
    for i in indices:
        block = blocks[i]
        page = block["page"] + 1
        text = f"[Page {page}]\n{block['text']}\n"
        if total_chars + len(text) > max_chars:
            break
        lines.append(text)
        total_chars += len(text)

    return "\n".join(lines)


def build_structure_prompt(
    text_context: str, metadata: Dict[str, Any],
    profile: Optional[ContentProfile] = None,
    chunk_start: int = 0, chunk_end: int = 0, total_blocks: int = 0,
    include_title_authors: bool = True,
) -> str:
    """Build the prompt for LLM structure analysis.

    Uses ContentProfile to provide document-type-specific guidance.
    Supports chunked classification — each chunk gets its own prompt.

    Args:
        text_context: Formatted text blocks for this chunk
        metadata: PDF metadata
        profile: Content classification profile
        chunk_start: First block index in this chunk
        chunk_end: Last block index (exclusive) in this chunk
        total_blocks: Total blocks in the full document
        include_title_authors: Whether to ask for title/authors (first chunk only)
    """
    # Document type context for LLM
    doc_type_context = ""
    if profile:
        doc_type = profile.document_type.value.replace("_", " ")
        doc_type_context = f"""
Document type detected: {doc_type} (confidence: {profile.confidence:.0%})

Document-type-specific guidance:
"""
        if profile.document_type.value == "scientific_paper":
            doc_type_context += """- This is a SCIENTIFIC PAPER. Focus on methodology, findings, and technical contributions.
- Front matter (title, authors, affiliations, abstract) is metadata, NOT content topics.
- Author affiliations (universities, institutes, labs) are NOT topics - they are metadata.
- Extract topics ONLY from the body content (introduction through conclusion).
- Good topics: technical concepts, methods, algorithms, problem domains.
- BAD topics: "Harvard University", "Department of Computer Science", author names.
"""
        elif profile.document_type.value == "news_article":
            doc_type_context += """- This is a NEWS ARTICLE. Focus on events, quotes, and key facts.
- Bylines and datelines are metadata, not topics.
- Extract topics from the article body, not headers/footers.
"""
        elif profile.document_type.value == "dataset_readme":
            doc_type_context += """- This is a DATASET README. Focus on what data is included, format, and intended use.
- Extract topics about data types, domains, and applications.
"""

    # Chunk context
    chunk_note = ""
    if total_blocks > 0:
        chunk_note = f"\nNOTE: This is blocks {chunk_start}-{chunk_end - 1} of {total_blocks} total. Classify ONLY these blocks.\n"

    # Title/authors line — only for first chunk
    if include_title_authors:
        title_authors_schema = """  "title": "The document title",
  "authors": ["Author Name", ...],
"""
    else:
        title_authors_schema = ""

    return f"""Classify each text block by its role and extract metadata.

Document metadata:
- Title: {metadata.get('title', 'Unknown')}
- Author: {metadata.get('author', 'Unknown')}
{doc_type_context}{chunk_note}
Text blocks:
---
{text_context}
---

Respond with JSON:
{{
{title_authors_schema}  "blocks": [
    {{
      "block_index": 0,
      "type": "title|abstract|section_header|paragraph|quote|citation|equation|author|date|keyword",
      "topics": ["topic1", "topic2"],
      "entities": ["entity1", "entity2"],
      "importance": 0.8
    }}
  ]
}}

Classification types:
- title: Main document title (largest font, first page)
- abstract: Summary paragraph (often labeled "Abstract")
- section_header: Section/subsection headings (bold, larger font)
- paragraph: Regular body text
- quote: Quoted text or block quotes
- citation: References, bibliography entries
- equation: Mathematical equations
- author: Author names/affiliations (METADATA, not content)
- date: Publication dates

Topics (IMPORTANT):
- Extract 1-3 CONCEPTUAL topics per block (what it's about, not what it is)
- Good: "Kalman filter", "UAV positioning", "sensor fusion"
- BAD — never extract these as topics:
  * Institutions: "Stanford University", "Department of Physics"
  * Block types: "figure caption", "abstract", "section"
  * Author/person names, journal names
- For author/citation/date/affiliation blocks: topics = []

Entities: Named algorithms, systems, datasets (e.g., "IKF-PF", "ROS")

Importance: 1.0 = title/findings, 0.8 = abstract/headers, 0.5 = paragraphs, 0.3 = citations/metadata
"""


def build_summary_prompt(text_context: str) -> str:
    """Build the prompt for summary generation"""
    return f"""Generate summaries of this document at three levels of detail.

Document text:
---
{text_context[:8000]}
---

Respond with JSON:
{{
  "one_sentence": "A single sentence summarizing the key point of the document.",
  "one_paragraph": "A paragraph (3-5 sentences) covering the main findings and contributions.",
  "full_summary": "A comprehensive summary (1-2 paragraphs) covering methodology, findings, and implications."
}}
"""
