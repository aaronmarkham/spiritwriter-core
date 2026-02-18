"""Document Ingestor - Extracts knowledge atoms from documents using PyMuPDF + LLM"""

import base64
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple

from spiritwriter.llm import LLMProvider
from spiritwriter.models.document import AtomType, DocumentAtom, DocumentGraph, ContentProfile
from spiritwriter.classify import ContentClassifier, is_theme_candidate


@dataclass
class ExtractionResult:
    """Result of document extraction before LLM analysis"""
    text_blocks: List[Dict[str, Any]]   # Raw text blocks with page/position
    images: List[Dict[str, Any]]        # Extracted images with metadata
    page_count: int
    metadata: Dict[str, Any]            # PDF metadata (title, author, etc.)


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
        extraction = self._extract_with_pymupdf(path)

        # Phase 1.5: Content-aware classification (before LLM)
        # This identifies document type and zones to guide extraction
        profile = self.classifier.classify(extraction)

        # Phase 2: LLM analysis for structure and semantics
        if self.mock_mode or not self.llm_provider:
            graph = self._mock_analyze(doc_id, source_path, extraction, profile)
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

    def _extract_with_pymupdf(self, path: Path, use_rendered_figures: bool = True) -> ExtractionResult:
        """
        Phase 1: Use PyMuPDF (fitz) to extract raw text blocks and images.

        Returns structured extraction with positional information.

        Args:
            use_rendered_figures: If True, extract figures by rendering pages and
                detecting figure regions (better for academic PDFs). If False, use
                raw embedded image extraction (faster but may produce fragments).
        """
        import fitz  # PyMuPDF

        doc = fitz.open(str(path))
        page_count = len(doc)

        text_blocks: List[Dict[str, Any]] = []
        images: List[Dict[str, Any]] = []
        seen_xrefs_global: set = set()  # Deduplicate images across pages
        metadata = {
            "title": doc.metadata.get("title", ""),
            "author": doc.metadata.get("author", ""),
            "subject": doc.metadata.get("subject", ""),
            "keywords": doc.metadata.get("keywords", ""),
            "creator": doc.metadata.get("creator", ""),
            "creation_date": doc.metadata.get("creationDate", ""),
        }

        for page_num in range(len(doc)):
            page = doc[page_num]

            # Extract text blocks with position
            blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
            for block in blocks:
                if block["type"] == 0:  # Text block
                    # Combine lines within the block
                    text = ""
                    for line in block.get("lines", []):
                        line_text = ""
                        for span in line.get("spans", []):
                            line_text += span.get("text", "")
                        text += line_text + "\n"
                    text = text.strip()
                    if text:
                        # Detect font size for structure hints
                        font_sizes = []
                        is_bold = False
                        for line in block.get("lines", []):
                            for span in line.get("spans", []):
                                font_sizes.append(span.get("size", 12))
                                if "bold" in span.get("font", "").lower():
                                    is_bold = True

                        avg_font_size = sum(font_sizes) / len(font_sizes) if font_sizes else 12

                        text_blocks.append({
                            "text": text,
                            "page": page_num,
                            "bbox": (block["bbox"][0], block["bbox"][1],
                                     block["bbox"][2], block["bbox"][3]),
                            "font_size": avg_font_size,
                            "is_bold": is_bold,
                        })

        # Close doc temporarily, we'll reopen if needed for figure extraction
        doc.close()

        # Extract figures using rendered page approach (better for academic PDFs)
        if use_rendered_figures:
            images = self._extract_rendered_figures(path, text_blocks)
        else:
            # Fallback: Extract embedded images (may produce fragments)
            images = self._extract_embedded_images(path)

        return ExtractionResult(
            text_blocks=text_blocks,
            images=images,
            page_count=page_count,
            metadata=metadata,
        )

    def _extract_rendered_figures(
        self, path: Path, text_blocks: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Extract figures by rendering pages and detecting figure regions.

        This method finds figure captions in the text and extracts the image
        region above each caption. Works much better for academic PDFs where
        embedded images are often fragmented.
        """
        import fitz
        import re
        import io

        doc = fitz.open(str(path))
        images = []

        # Find figure captions grouped by page
        caption_pattern = re.compile(
            r"(FIGURE|Figure|Fig\.?|TABLE|Table)\s*(\d+)",
            re.IGNORECASE
        )

        captions_by_page: Dict[int, List[Dict]] = {}
        for block in text_blocks:
            text = block["text"].strip()
            match = caption_pattern.match(text)
            if match:
                page_num = block["page"]
                if page_num not in captions_by_page:
                    captions_by_page[page_num] = []
                captions_by_page[page_num].append({
                    "text": text,
                    "bbox": block["bbox"],
                    "figure_num": match.group(2),
                    "figure_type": match.group(1).upper().replace(".", ""),
                })

        # For each page with captions, render and extract figure regions
        seen_figures = set()  # Track figure numbers to avoid duplicates

        for page_num in sorted(captions_by_page.keys()):
            page = doc[page_num]
            page_rect = page.rect
            page_width = page_rect.width
            page_height = page_rect.height

            # Render page at 2x resolution for quality
            zoom = 2.0
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)

            for cap in captions_by_page[page_num]:
                fig_key = f"{cap['figure_type']}_{cap['figure_num']}"
                if fig_key in seen_figures:
                    continue
                seen_figures.add(fig_key)

                # Caption bbox (in page coordinates)
                cap_x0, cap_y0, cap_x1, cap_y1 = cap["bbox"]

                # Figure region is above the caption
                # Use full page width, from top of page (or previous caption) to caption top
                margin = 10  # Small margin
                fig_x0 = margin
                fig_x1 = page_width - margin
                fig_y0 = max(0, cap_y0 - 400)  # Up to 400pt above caption
                fig_y1 = cap_y0 - 5  # Just above caption

                # Skip if region is too small
                if fig_y1 - fig_y0 < 50:
                    continue

                # Convert to pixel coordinates (account for zoom)
                clip_rect = fitz.Rect(
                    fig_x0 * zoom,
                    fig_y0 * zoom,
                    fig_x1 * zoom,
                    fig_y1 * zoom
                )

                # Clip the pixmap to extract just the figure region
                try:
                    # Re-render with clip for this specific region
                    fig_pix = page.get_pixmap(matrix=mat, clip=fitz.Rect(fig_x0, fig_y0, fig_x1, fig_y1))
                    img_bytes = fig_pix.tobytes("png")

                    if len(img_bytes) < 5000:  # Skip if too small
                        continue

                    images.append({
                        "page": page_num,
                        "bbox": (fig_x0, fig_y0, fig_x1, fig_y1),
                        "image_bytes": img_bytes,
                        "ext": "png",
                        "width": fig_pix.width,
                        "height": fig_pix.height,
                        "figure_number": cap["figure_num"],
                        "caption": cap["text"],
                    })
                except Exception as e:
                    continue

        doc.close()
        return images

    def _extract_embedded_images(self, path: Path) -> List[Dict[str, Any]]:
        """
        Extract raw embedded images from PDF.

        This is the fallback method - faster but may produce image fragments
        for academic PDFs where figures are composed of multiple sub-images.
        """
        import fitz

        doc = fitz.open(str(path))
        images = []
        seen_xrefs: set = set()

        for page_num, page in enumerate(doc):
            for img_ref in page.get_images(full=True):
                xref = img_ref[0]
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)

                try:
                    img_data = doc.extract_image(xref)
                    if not img_data:
                        continue

                    width = img_data.get("width", 0)
                    height = img_data.get("height", 0)
                    img_bytes = img_data["image"]

                    # Skip tiny images (icons, bullets, decorations)
                    if width < 150 or height < 150 or len(img_bytes) < 5000:
                        continue

                    # Skip corrupt images (impossible dimensions)
                    if width > 10000 or height > 10000:
                        continue

                    # Get bounding box on the page
                    bbox = (0.0, 0.0, float(width), float(height))
                    try:
                        rects = page.get_image_rects(xref)
                        if rects:
                            r = rects[0]
                            bbox = (r.x0, r.y0, r.x1, r.y1)
                    except Exception:
                        pass

                    images.append({
                        "page": page_num,
                        "bbox": bbox,
                        "image_bytes": img_bytes,
                        "ext": img_data["ext"],
                        "width": width,
                        "height": height,
                        "xref": xref,
                    })
                except Exception:
                    continue

        doc.close()
        return images

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
        # A 100-block document would need ~15k output tokens in one shot,
        # hitting limits and producing truncated JSON. Chunking to ~30 blocks
        # keeps each response well under 8k tokens.
        all_classified_blocks = []
        title = extraction.metadata.get("title", "")
        authors = []

        num_blocks = len(extraction.text_blocks)
        chunk_size = 30

        for chunk_start in range(0, num_blocks, chunk_size):
            chunk_end = min(chunk_start + chunk_size, num_blocks)
            chunk_blocks = extraction.text_blocks[chunk_start:chunk_end]
            chunk_indices = list(range(chunk_start, chunk_end))

            text_context = self._build_text_context_for_chunk(chunk_blocks, chunk_indices)
            is_first_chunk = chunk_start == 0

            structure_prompt = self._build_structure_prompt(
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

        # Map LLM type names to our AtomType enum
        type_mapping = {
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

        for i, block_info in enumerate(classified_blocks):
            atom_type_str = block_info.get("type", "paragraph")
            # Apply type mapping
            atom_type_str = type_mapping.get(atom_type_str, atom_type_str)
            try:
                atom_type = AtomType(atom_type_str)
            except ValueError:
                atom_type = AtomType.PARAGRAPH

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
            caption = self._find_caption(img_info, extraction.text_blocks)
            if caption:
                atom.caption = caption
                # Parse figure number from caption
                fig_match = re.match(r"(Figure|Fig\.?|Table)\s*(\d+)", caption, re.IGNORECASE)
                if fig_match:
                    atom.figure_number = f"{fig_match.group(1)} {fig_match.group(2)}"

        # Step 4: Generate summaries (use abbreviated context — just title, abstract, conclusions)
        summary_context = self._build_summary_context(extraction)
        summary_prompt = self._build_summary_prompt(summary_context)
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

    def _find_caption(
        self, img_info: Dict[str, Any], text_blocks: List[Dict[str, Any]]
    ) -> Optional[str]:
        """Find the caption text near a figure (below, above, or anywhere on page)"""
        img_page = img_info["page"]
        img_bbox = img_info["bbox"]
        img_top = img_bbox[1]     # y0 coordinate (top of image)
        img_bottom = img_bbox[3]  # y1 coordinate (bottom of image)

        candidates = []
        caption_pattern = re.compile(r"(FIGURE|Figure|Fig\.?|TABLE|Table|Chart)\s*\d", re.IGNORECASE)

        for block in text_blocks:
            if block["page"] != img_page:
                continue

            text = block["text"].strip()
            if not caption_pattern.match(text):
                continue

            block_top = block["bbox"][1]
            block_bottom = block["bbox"][3]

            # Check if caption is below the image (most common)
            if 0 < (block_top - img_bottom) < 100:
                distance = block_top - img_bottom
                candidates.append((distance, text, "below"))

            # Check if caption is above the image (some papers do this)
            elif 0 < (img_top - block_bottom) < 100:
                distance = img_top - block_bottom
                candidates.append((distance + 200, text, "above"))  # Prefer below

        # If no proximity match found, search entire page for figure captions
        if not candidates:
            page_captions = []
            for block in text_blocks:
                if block["page"] != img_page:
                    continue
                text = block["text"].strip()
                if caption_pattern.match(text):
                    # Use vertical distance from image center as tiebreaker
                    img_center_y = (img_top + img_bottom) / 2
                    block_center_y = (block["bbox"][1] + block["bbox"][3]) / 2
                    distance = abs(block_center_y - img_center_y)
                    page_captions.append((distance, text, "page"))

            if page_captions:
                page_captions.sort(key=lambda x: x[0])
                return page_captions[0][1]

        if candidates:
            candidates.sort(key=lambda x: x[0])
            return candidates[0][1]

        return None

    def _build_text_context_for_chunk(
        self,
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

    def _build_summary_context(self, extraction: ExtractionResult, max_chars: int = 15000) -> str:
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

    def _build_structure_prompt(
        self, text_context: str, metadata: Dict[str, Any],
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

    def _build_summary_prompt(self, text_context: str) -> str:
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

    def _extract_json(self, response: str) -> Dict[str, Any]:
        """Extract JSON from LLM response"""
        import json
        
        # Try to find JSON in response
        response = response.strip()
        
        # Look for JSON code blocks
        if "```json" in response:
            start = response.find("```json") + 7
            end = response.find("```", start)
            if end != -1:
                json_str = response[start:end].strip()
            else:
                json_str = response[start:].strip()
        elif "```" in response:
            start = response.find("```") + 3
            end = response.find("```", start)
            if end != -1:
                json_str = response[start:end].strip()
            else:
                json_str = response[start:].strip()
        else:
            json_str = response
            
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            # Try to find just the JSON object
            start = json_str.find("{")
            if start != -1:
                try:
                    return json.loads(json_str[start:])
                except json.JSONDecodeError:
                    pass
            return {}

    def _mock_analyze(
        self, doc_id: str, source_path: str, extraction: ExtractionResult,
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
            raw_topics = self._extract_mock_topics(text)
            filtered_topics = [t for t in raw_topics if is_theme_candidate(t)]

            # If block is in a metadata zone, don't extract topics
            if profile.is_metadata_block(i):
                filtered_topics = []

            entities = self._extract_mock_entities(text)
            relationships = self._extract_mock_relationships(text, entities)

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
            caption = self._find_caption(img_info, extraction.text_blocks)

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

    # Common words to exclude from topics and entities
        # Common English + academic stopwords for topic extraction filtering.
    # Combines THEME_STOPWORDS with additional function words and generic terms.
    from spiritwriter.stopwords import THEME_STOPWORDS as _THEME_SW
    _STOPWORDS = _THEME_SW | frozenset({
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

    def _extract_mock_topics(self, text: str) -> List[str]:
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
            if any(w not in self._STOPWORDS for w in words):
                topics.append(phrase)

        # Single significant capitalized words (not at sentence start, min 6 chars)
        for match in re.finditer(r'(?<=[a-z]\s)([A-Z][a-z]{5,})\b', text[:500]):
            word = match.group(1).lower()
            if word not in self._STOPWORDS:
                topics.append(word)

        # Deduplicate while preserving order
        seen = set()
        unique_topics = []
        for t in topics:
            if t not in seen:
                seen.add(t)
                unique_topics.append(t)

        return unique_topics[:3]

    def _extract_mock_entities(self, text: str) -> List[str]:
        """Extract entities from text using heuristics (mock mode).

        Finds: acronyms (LLM, KG, NLP), capitalized phrases (Knowledge Graph),
        and named entities that aren't common academic boilerplate.
        """
        entities = set()

        # Acronyms: 3-5 uppercase letters, optionally with digits
        # These are almost always meaningful (LLM, KG, NLP, GPT, DNN, etc.)
        for match in re.finditer(r'\b([A-Z][A-Z0-9]{2,4})\b', text):
            acronym = match.group(1)
            if acronym.lower() not in self._STOPWORDS:
                entities.add(acronym)

        # Capitalized multi-word phrases — must not be at sentence start
        # Look for "... word Word Word ..." patterns (mid-sentence capitalization)
        for match in re.finditer(r'(?<=[a-z.,;:]\s)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)', text):
            phrase = match.group(1)
            words = phrase.lower().split()
            # Skip if all words are stopwords
            if not all(w in self._STOPWORDS for w in words) and len(phrase) > 5:
                entities.add(phrase)

        return list(entities)[:5]

    def _extract_mock_relationships(self, text: str, entities: List[str]) -> List[str]:
        """Extract simple relationships between entities found in the same text block."""
        if len(entities) < 2:
            return []

        relationships = []
        # Co-occurrence: entities in the same block are related
        for i in range(min(len(entities) - 1, 3)):
            relationships.append(f"{entities[i]} <-> {entities[i+1]}")

        return relationships[:3]


# Backward compatibility alias
DocumentIngestorAgent = DocumentIngestor