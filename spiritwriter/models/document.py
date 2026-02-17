"""Document ingestion models for document-to-video pipeline"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any, Tuple


class AtomType(Enum):
    """Types of knowledge atoms extracted from documents"""
    # Text atoms
    TITLE = "title"
    ABSTRACT = "abstract"
    SECTION_HEADER = "section_header"
    PARAGRAPH = "paragraph"
    QUOTE = "quote"
    CITATION = "citation"

    # Visual atoms
    FIGURE = "figure"
    CHART = "chart"
    TABLE = "table"
    EQUATION = "equation"
    DIAGRAM = "diagram"

    # Meta atoms
    AUTHOR = "author"
    DATE = "date"
    KEYWORD = "keyword"


class DocumentType(str, Enum):
    """What kind of document this is."""
    SCIENTIFIC_PAPER = "scientific_paper"
    NEWS_ARTICLE = "news_article"
    BLOG_POST = "blog_post"
    TECHNICAL_REPORT = "technical_report"
    DATASET_README = "dataset_readme"
    GOVERNMENT_DOCUMENT = "government_document"
    GENERIC = "generic"  # Fallback


class ZoneRole(str, Enum):
    """
    What role a document zone plays.

    Zones are contiguous regions of the document. A zone's role
    determines how its atoms are treated during extraction.
    """
    FRONT_MATTER = "front_matter"       # Title, authors, affiliations, abstract
    BODY = "body"                       # Main content — full topic extraction
    BACK_MATTER = "back_matter"         # References, acknowledgments, appendix
    BIOGRAPHICAL = "biographical"       # Author bios, institutional info
    BOILERPLATE = "boilerplate"         # Headers, footers, page numbers, copyright


@dataclass
class DocumentZone:
    """A contiguous region of the document with a known role."""
    role: ZoneRole
    start_block: int                    # First block index (inclusive)
    end_block: int                      # Last block index (inclusive)
    label: str = ""                     # e.g., "Author Affiliations", "Methods", "References"


@dataclass
class ContentProfile:
    """
    What the classifier learned about this document.

    Produced by ContentClassifier, consumed by DocumentIngestorAgent
    to guide LLM analysis.
    """
    document_type: DocumentType
    confidence: float                   # 0-1, how sure the classifier is

    zones: List[DocumentZone] = field(default_factory=list)

    # Detected metadata (extracted early, before LLM)
    detected_authors: List[str] = field(default_factory=list)
    detected_institutions: List[str] = field(default_factory=list)
    detected_doi: Optional[str] = None
    detected_date: Optional[str] = None

    # Extraction rules derived from document type
    # These tell the ingestor what to do with each zone
    topic_extraction_zones: List[ZoneRole] = field(default_factory=list)  # Only extract topics from these
    entity_extraction_zones: List[ZoneRole] = field(default_factory=list)  # Extract entities from these
    metadata_zones: List[ZoneRole] = field(default_factory=list)           # Store as metadata, not content

    def is_metadata_block(self, block_index: int) -> bool:
        """Check if a block is in a metadata zone."""
        for zone in self.zones:
            if zone.start_block <= block_index <= zone.end_block:
                return zone.role in self.metadata_zones
        return False

    def get_zone_for_block(self, block_index: int) -> Optional[DocumentZone]:
        """Get the zone a block belongs to."""
        for zone in self.zones:
            if zone.start_block <= block_index <= zone.end_block:
                return zone
        return None


@dataclass
class DocumentAtom:
    """Smallest unit of extracted knowledge from a document"""
    atom_id: str
    atom_type: AtomType

    # Content
    content: str                            # Text content or description
    raw_data: Optional[bytes] = None        # For figures/tables: the actual image data

    # Location in source
    source_page: Optional[int] = None
    source_location: Optional[Tuple[float, float, float, float]] = None  # Bounding box (x0, y0, x1, y1)

    # Semantic metadata (populated by LLM)
    topics: List[str] = field(default_factory=list)
    entities: List[str] = field(default_factory=list)
    relationships: List[str] = field(default_factory=list)  # atom_ids this relates to
    importance_score: float = 0.5           # 0-1, how central to the document

    # For figures/tables
    caption: Optional[str] = None
    figure_number: Optional[str] = None     # "Figure 3", "Table 2"
    data_summary: Optional[str] = None      # LLM-generated description of what it shows

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict (excluding raw_data bytes)"""
        return {
            "atom_id": self.atom_id,
            "atom_type": self.atom_type.value,
            "content": self.content,
            "has_raw_data": self.raw_data is not None,
            "source_page": self.source_page,
            "source_location": list(self.source_location) if self.source_location else None,
            "topics": self.topics,
            "entities": self.entities,
            "relationships": self.relationships,
            "importance_score": self.importance_score,
            "caption": self.caption,
            "figure_number": self.figure_number,
            "data_summary": self.data_summary,
        }


@dataclass
class DocumentGraph:
    """Knowledge graph of document atoms with structure and summaries"""
    document_id: str
    source_path: str

    atoms: Dict[str, DocumentAtom] = field(default_factory=dict)

    # Graph structure
    hierarchy: Dict[str, List[str]] = field(default_factory=dict)       # Parent atom_id -> child atom_ids
    references: Dict[str, List[str]] = field(default_factory=dict)      # Atom -> atoms it references
    flow: List[str] = field(default_factory=list)                       # Reading order (atom_ids)

    # Summaries at different levels (populated by LLM)
    one_sentence: str = ""
    one_paragraph: str = ""
    full_summary: str = ""

    # Convenience lists of atom_ids by type
    figures: List[str] = field(default_factory=list)
    tables: List[str] = field(default_factory=list)
    key_quotes: List[str] = field(default_factory=list)

    # Document metadata
    title: str = ""
    authors: List[str] = field(default_factory=list)
    page_count: int = 0

    def get_atom(self, atom_id: str) -> Optional[DocumentAtom]:
        """Get an atom by ID"""
        return self.atoms.get(atom_id)

    def get_atoms_by_type(self, atom_type: AtomType) -> List[DocumentAtom]:
        """Get all atoms of a given type"""
        return [a for a in self.atoms.values() if a.atom_type == atom_type]

    def get_figures(self) -> List[DocumentAtom]:
        """Get all figure atoms"""
        return [self.atoms[aid] for aid in self.figures if aid in self.atoms]

    def get_tables(self) -> List[DocumentAtom]:
        """Get all table atoms"""
        return [self.atoms[aid] for aid in self.tables if aid in self.atoms]

    def get_section(self, section_name: str) -> List[DocumentAtom]:
        """Get atoms belonging to a named section"""
        # Find the section header atom
        for atom in self.atoms.values():
            if atom.atom_type == AtomType.SECTION_HEADER and section_name.lower() in atom.content.lower():
                # Return its children from the hierarchy
                child_ids = self.hierarchy.get(atom.atom_id, [])
                return [self.atoms[cid] for cid in child_ids if cid in self.atoms]
        return []

    def get_children(self, atom_id: str) -> List[DocumentAtom]:
        """Get child atoms of a given atom"""
        child_ids = self.hierarchy.get(atom_id, [])
        return [self.atoms[cid] for cid in child_ids if cid in self.atoms]

    @property
    def atom_count(self) -> int:
        """Total number of atoms"""
        return len(self.atoms)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for storage"""
        return {
            "document_id": self.document_id,
            "source_path": self.source_path,
            "atoms": {aid: atom.to_dict() for aid, atom in self.atoms.items()},
            "hierarchy": self.hierarchy,
            "references": self.references,
            "flow": self.flow,
            "one_sentence": self.one_sentence,
            "one_paragraph": self.one_paragraph,
            "full_summary": self.full_summary,
            "figures": self.figures,
            "tables": self.tables,
            "key_quotes": self.key_quotes,
            "title": self.title,
            "authors": self.authors,
            "page_count": self.page_count,
        }