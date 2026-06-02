"""Document Ingestor — orchestrator that delegates to extraction, prompts, mappings, and mock modules."""

import hashlib
import re
from pathlib import Path
from typing import List, Optional, Dict, Any

from spiritwriter.llm import LLMProvider
from spiritwriter.llm.anthropic import JSONExtractor
from spiritwriter.models.document import AtomType, DocumentAtom, DocumentGraph, ContentProfile
from spiritwriter.classify import ContentClassifier, is_theme_candidate

from spiritwriter.ingest.extraction import (
    ExtractionResult,
    extract_with_pymupdf,
    find_caption,
)
from spiritwriter.ingest.prompts import (
    build_text_context_for_chunk,
    build_summary_context,
    build_structure_prompt,
    build_summary_prompt,
)
from spiritwriter.ingest.mappings import normalize_atom_type
from spiritwriter.ingest.mock import mock_analyze


class DocumentIngestor:
    """
    Extracts knowledge atoms from documents (PDFs) using PyMuPDF for structure
    extraction and Claude LLM for semantic analysis.

    Two-phase approach:
    1. PyMuPDF: Extract raw text blocks, images, and structure
    2. LLM: Analyze structure, classify atoms, extract metadata, generate summaries
    """

    def __init__(
        self,
        llm_provider: Optional[LLMProvider] = None,
        mock_mode: bool = False,
    ):
        self.llm_provider = llm_provider
        self.mock_mode = mock_mode
        self.classifier = ContentClassifier()

    async def ingest(self, source_path: str) -> DocumentGraph:
        """
        Ingest a document and produce a DocumentGraph.

        Args:
            source_path: Path to the PDF file

        Returns:
            DocumentGraph with extracted and analyzed atoms
        """
        path = Path(source_path)
        if not path.exists():
            raise FileNotFoundError(f"Document not found: {source_path}")

        if path.suffix.lower() != ".pdf":
            raise ValueError(f"Unsupported format: {path.suffix}. Currently only PDF is supported.")

        # Generate document ID from file hash
        doc_id = self._generate_doc_id(path)

        # Phase 1: Extract raw content with PyMuPDF
        extraction = extract_with_pymupdf(path)

        # Phase 1.5: Content-aware classification (before LLM)
        # This identifies document type and zones to guide extraction
        profile = self.classifier.classify(extraction)

        # Phase 2: LLM analysis for structure and semantics
        if self.mock_mode or not self.llm_provider:
            graph = mock_analyze(doc_id, source_path, extraction, profile)
        else:
            graph = await self._llm_analyze(doc_id, source_path, extraction, profile)

        return graph

    def _generate_doc_id(self, path: Path) -> str:
        """Generate a stable document ID from file content hash"""
        hasher = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return f"doc_{hasher.hexdigest()[:12]}"

    async def _llm_analyze(
        self, doc_id: str, source_path: str, extraction: ExtractionResult,
        profile: ContentProfile
    ) -> DocumentGraph:
        """
        Phase 2: Use LLM to analyze extracted content, classify atoms,
        build hierarchy, and generate summaries.

        Uses chunked classification to avoid output token truncation:
        - Sends blocks in batches of ~30 to the LLM
        - Each chunk gets a complete JSON response
        - Chunks are merged into the final classified block list

        Uses ContentProfile from pre-LLM classification to:
        - Guide document-type-aware prompts
        - Filter topics from metadata zones (affiliations, biographical)
        """
        # Step 1: Classify blocks in chunks to avoid output truncation
        all_classified_blocks = []
        title = extraction.metadata.get("title", "")
        authors = []

        num_blocks = len(extraction.text_blocks)
        chunk_size = 30

        for chunk_start in range(0, num_blocks, chunk_size):
            chunk_end = min(chunk_start + chunk_size, num_blocks)
            chunk_blocks = extraction.text_blocks[chunk_start:chunk_end]
            chunk_indices = list(range(chunk_start, chunk_end))

            text_context = build_text_context_for_chunk(chunk_blocks, chunk_indices)
            is_first_chunk = chunk_start == 0

            structure_prompt = build_structure_prompt(
                text_context, extraction.metadata, profile,
                chunk_start=chunk_start, chunk_end=chunk_end, total_blocks=num_blocks,
                include_title_authors=is_first_chunk,
            )
            structure_response = await self.llm_provider.query(structure_prompt)
            structure = self._extract_json(structure_response)

            # Grab title/authors from first chunk only
            if is_first_chunk:
                title = structure.get("title", title)
                authors = structure.get("authors", [])

            all_classified_blocks.extend(structure.get("blocks", []))

        # Step 2: Build atoms from classified blocks
        atoms: Dict[str, DocumentAtom] = {}
        hierarchy: Dict[str, List[str]] = {}
        flow: List[str] = []

        classified_blocks = all_classified_blocks
        current_section_id = None

        for i, block_info in enumerate(classified_blocks):
            atom_type_str = block_info.get("type", "paragraph")
            atom_type = normalize_atom_type(atom_type_str)

            # Get the original text block
            block_idx = block_info.get("block_index", i)
            if block_idx < len(extraction.text_blocks):
                text_block = extraction.text_blocks[block_idx]
            else:
                continue

            atom_id = f"{doc_id}_atom_{i:03d}"
            # Filter topics: remove institutional names and metadata noise
            raw_topics = block_info.get("topics", [])
            filtered_topics = [t for t in raw_topics if is_theme_candidate(t)]

            # If block is in a metadata zone, don't extract topics at all
            if profile.is_metadata_block(block_idx):
                filtered_topics = []

            atom = DocumentAtom(
                atom_id=atom_id,
                atom_type=atom_type,
                content=text_block["text"],
                source_page=text_block["page"],
                source_location=text_block["bbox"],
                topics=filtered_topics,
                entities=block_info.get("entities", []),
                importance_score=block_info.get("importance", 0.5),
            )

            atoms[atom_id] = atom
            flow.append(atom_id)

            # Build hierarchy
            if atom_type == AtomType.SECTION_HEADER:
                current_section_id = atom_id
                hierarchy[atom_id] = []
            elif current_section_id and atom_type in (AtomType.PARAGRAPH, AtomType.QUOTE):
                hierarchy[current_section_id].append(atom_id)

        # Step 3: Process images as figure atoms
        for i, img_info in enumerate(extraction.images):
            atom_id = f"{doc_id}_fig_{i:03d}"

            # Use LLM to describe the image if not in mock mode
            description = await self._describe_image(img_info)

            atom = DocumentAtom(
                atom_id=atom_id,
                atom_type=AtomType.FIGURE,
                content=description,
                raw_data=img_info["image_bytes"],
                source_page=img_info["page"],
                source_location=img_info["bbox"],
                data_summary=description,
                importance_score=0.7,  # Figures are generally important
            )
            atoms[atom_id] = atom

            # Find nearby caption
            caption = find_caption(img_info, extraction.text_blocks)
            if caption:
                atom.caption = caption
                # Parse figure number from caption
                fig_match = re.match(r"(Figure|Fig\.?|Table)\s*(\d+)", caption, re.IGNORECASE)
                if fig_match:
                    atom.figure_number = f"{fig_match.group(1)} {fig_match.group(2)}"

        # Step 4: Generate summaries (use abbreviated context — just title, abstract, conclusions)
        summary_context = build_summary_context(extraction)
        summary_prompt = build_summary_prompt(summary_context)
        summary_response = await self.llm_provider.query(summary_prompt)
        summaries = self._extract_json(summary_response)

        # Identify key quotes
        key_quote_ids = []
        for atom_id, atom in atoms.items():
            if atom.atom_type == AtomType.QUOTE:
                key_quote_ids.append(atom_id)

        # Build the graph
        graph = DocumentGraph(
            document_id=doc_id,
            source_path=source_path,
            atoms=atoms,
            hierarchy=hierarchy,
            references={},  # TODO: cross-reference extraction in future
            flow=flow,
            one_sentence=summaries.get("one_sentence", ""),
            one_paragraph=summaries.get("one_paragraph", ""),
            full_summary=summaries.get("full_summary", ""),
            figures=[aid for aid, a in atoms.items() if a.atom_type == AtomType.FIGURE],
            tables=[aid for aid, a in atoms.items() if a.atom_type == AtomType.TABLE],
            key_quotes=key_quote_ids,
            title=title or extraction.metadata.get("title", ""),
            authors=authors,
            page_count=extraction.page_count,
        )

        return graph

    async def _describe_image(self, img_info: Dict[str, Any]) -> str:
        """Use LLM vision to describe an extracted image"""
        import tempfile
        import os

        img_bytes = img_info["image_bytes"]
        ext = img_info.get("ext", "png")

        # Save image bytes to temp file for LLM provider
        with tempfile.NamedTemporaryFile(mode='wb', suffix=f'.{ext}', delete=False) as tmp:
            tmp.write(img_bytes)
            tmp_path = tmp.name

        try:
            prompt = (
                "Describe this figure/image from an academic paper. "
                "What does it show? Include key data points, axes labels, trends, or diagram elements. "
                "Be concise but specific (2-3 sentences)."
            )

            # Use vision-capable query with file path
            response = await self.llm_provider.query_with_image(
                prompt=prompt,
                image_data=img_bytes,
            )
            return response.strip()
        finally:
            # Clean up temp file
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def _extract_json(self, response: str) -> Dict[str, Any]:
        """Extract JSON from an LLM response.

        Delegates to the shared, more robust ``JSONExtractor`` (handles
        code fences, truncated output, stray prose), preserving this
        method's graceful ``{}`` fallback on failure.
        """
        try:
            return JSONExtractor.extract(response)
        except ValueError:
            return {}


# Backward compatibility alias
DocumentIngestorAgent = DocumentIngestor
