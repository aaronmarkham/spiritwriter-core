"""Atom type mapping — normalizes LLM type labels to AtomType enum values."""

from spiritwriter.models.document import AtomType

# Map LLM type names to our AtomType enum
TYPE_MAPPING = {
    "reference": "citation",
    "references": "citation",
    "bibliography": "citation",
    "subsection_header": "section_header",
    "heading": "section_header",
    "subheading": "section_header",
    "body_text": "paragraph",
    "method_description": "paragraph",
    "experiment_description": "paragraph",
    "results_description": "paragraph",
    "evaluation_description": "paragraph",
    "discussion": "paragraph",
    "introduction": "paragraph",
    "conclusion": "paragraph",
    "table_data": "table",
    "table_header": "table",
    "table_caption": "paragraph",
    "figure_caption": "paragraph",
    "page_footer": "citation",
    "page_header": "citation",
    "metadata": "citation",
    "authors": "author",
    "affiliations": "author",  # Affiliations are author metadata, not content
    "contact": "author",
    "author_bio": "author",
    "date": "date",
}


def normalize_atom_type(type_str: str) -> AtomType:
    """Normalize an LLM type label to an AtomType enum value.

    Applies TYPE_MAPPING first, then attempts enum lookup, falling back
    to PARAGRAPH for unrecognised labels.
    """
    type_str = TYPE_MAPPING.get(type_str, type_str)
    try:
        return AtomType(type_str)
    except ValueError:
        return AtomType.PARAGRAPH
