"""Tests for the ingest flow — multi-format loaders + DocumentIngestor.

The PDF-dependent tests need PyMuPDF (``fitz``); they skip cleanly when
it isn't installed. The DocumentIngestor tests are the first coverage of
that CSP-derived pipeline, and exercise the real PyMuPDF extraction plus
the shared JSONExtractor path (``_extract_json`` DRY).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from spiritwriter.models.document import AtomType
from spiritwriter.ingest import (
    extract_document_text,
    load_documents,
    UnsupportedDocument,
    DocumentIngestor,
)
from spiritwriter.ingest.mappings import normalize_atom_type
from spiritwriter.llm import MockLLMProvider


@pytest.fixture
def pdf_file(tmp_path):
    """A tiny single-page PDF with known text. Skips without PyMuPDF."""
    fitz = pytest.importorskip("fitz")
    path = tmp_path / "doc.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "Alpha Bravo Charlie. Knowledge Graphs are useful for retrieval.",
        fontsize=14,
    )
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def figure_pdf(tmp_path):
    """A single-page PDF with an embedded image and a 'Figure 1.' caption.

    Synthetic (no copyrighted content) but shaped like a real paper so it
    exercises the figure-extraction paths: rendered-figure detection,
    embedded-image extraction, and caption matching. The pixmap is noisy
    so the rendered/embedded bytes clear the 5KB/150px size gates.
    """
    fitz = pytest.importorskip("fitz")
    path = tmp_path / "figure.pdf"
    doc = fitz.open()
    page = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 260, 200))
    for y in range(200):
        for x in range(0, 260, 2):
            pix.set_pixel(x, y, ((x * 7) % 256, (y * 5) % 256, ((x + y) * 3) % 256))
    page.insert_image(fitz.Rect(72, 90, 332, 290), stream=pix.tobytes("png"))
    page.insert_text((72, 310), "Figure 1. Overview of the synthetic test figure.",
                     fontsize=11)
    page.insert_text((72, 340), "Body paragraph discussing the figure in detail here.",
                     fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


# ── Loaders ─────────────────────────────────────────────────────────


class TestLoaders:
    def test_extract_pdf_text_reads_content(self, pdf_file):
        text = extract_document_text(pdf_file)
        assert "Alpha Bravo Charlie" in text
        assert "Knowledge Graphs" in text

    def test_load_documents_mixed_pdf_and_markdown(self, pdf_file, tmp_path):
        (tmp_path / "notes.md").write_text("markdown body", encoding="utf-8")
        docs = load_documents(tmp_path)
        assert set(docs) == {"doc:doc.pdf", "doc:notes.md"}
        assert "Alpha Bravo Charlie" in docs["doc:doc.pdf"]
        assert docs["doc:notes.md"] == "markdown body"

    def test_missing_path_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_documents(tmp_path / "does-not-exist")

    def test_non_utf8_file_skipped_in_dir(self, tmp_path):
        """A single non-UTF-8 .txt must not abort the whole directory load —
        it's skipped, the rest still load."""
        (tmp_path / "good.md").write_text("clean text", encoding="utf-8")
        (tmp_path / "bad.txt").write_bytes(b"\xff\xfe bad bytes \x80\x81")
        docs = load_documents(tmp_path)
        assert set(docs) == {"doc:good.md"}
        assert docs["doc:good.md"] == "clean text"

    def test_unsupported_single_file_raises(self, tmp_path):
        f = tmp_path / "data.bin"
        f.write_bytes(b"\x00\x01")
        with pytest.raises(UnsupportedDocument):
            extract_document_text(f)


# ── mappings ────────────────────────────────────────────────────────


class TestNormalizeAtomType:
    def test_known_mappings(self):
        assert normalize_atom_type("references") == AtomType.CITATION
        assert normalize_atom_type("body_text") == AtomType.PARAGRAPH
        assert normalize_atom_type("affiliations") == AtomType.AUTHOR

    def test_direct_enum_value(self):
        assert normalize_atom_type("title") == AtomType.TITLE

    def test_unknown_falls_back_to_paragraph(self):
        assert normalize_atom_type("totally_unknown_type") == AtomType.PARAGRAPH


# ── DocumentIngestor (PDF → DocumentGraph) ──────────────────────────


class TestDocumentIngestor:
    def test_rejects_non_pdf(self, tmp_path):
        txt = tmp_path / "notes.txt"
        txt.write_text("hello", encoding="utf-8")
        with pytest.raises(ValueError):
            asyncio.run(DocumentIngestor(mock_mode=True).ingest(str(txt)))

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            asyncio.run(DocumentIngestor(mock_mode=True).ingest("nope.pdf"))

    def test_mock_mode_builds_graph(self, pdf_file):
        """mock_mode runs real PyMuPDF extraction + heuristic analysis —
        no LLM. Smoke-covers extraction.py + mock.py + document.py."""
        graph = asyncio.run(DocumentIngestor(mock_mode=True).ingest(str(pdf_file)))
        assert graph.atom_count >= 1
        assert graph.page_count == 1
        assert graph.document_id.startswith("doc_")
        # Some atom should carry the inserted text.
        assert any("Alpha Bravo Charlie" in a.content for a in graph.atoms.values())

    def test_llm_mode_uses_structure_and_summary(self, pdf_file):
        """Drives _llm_analyze with a scripted provider — exercises
        prompts.py, mappings.py, and the JSONExtractor-backed _extract_json."""
        def respond(prompt: str) -> str:
            if "Classify each text block" in prompt:
                return json.dumps({
                    "title": "Test Paper",
                    "authors": ["A. Researcher"],
                    "blocks": [{"block_index": 0, "type": "body_text",
                                "topics": ["knowledge graphs"], "entities": ["KG"],
                                "importance": 0.7}],
                })
            if "Generate summaries" in prompt:
                return json.dumps({"one_sentence": "A sentence.",
                                   "one_paragraph": "A paragraph.",
                                   "full_summary": "The full summary."})
            return "{}"

        graph = asyncio.run(
            DocumentIngestor(llm_provider=MockLLMProvider(respond)).ingest(str(pdf_file)))
        assert graph.title == "Test Paper"
        assert graph.authors == ["A. Researcher"]
        assert graph.one_sentence == "A sentence."
        # 'body_text' normalizes to PARAGRAPH via mappings.
        assert any(a.atom_type == AtomType.PARAGRAPH for a in graph.atoms.values())

    def test_unparseable_llm_response_degrades_gracefully(self, pdf_file):
        """Garbage LLM output → _extract_json returns {} (the DRY'd
        JSONExtractor fallback) → graph still builds, no crash."""
        graph = asyncio.run(
            DocumentIngestor(llm_provider=MockLLMProvider("not json at all")).ingest(
                str(pdf_file)))
        assert graph.document_id.startswith("doc_")
        assert graph.one_sentence == ""   # summaries {} → default empty


# ── Figure extraction (image-bearing PDFs) ──────────────────────────


class TestFigureExtraction:
    def test_rendered_figure_detected_with_caption(self, figure_pdf):
        from spiritwriter.ingest.extraction import extract_with_pymupdf
        ext = extract_with_pymupdf(figure_pdf)
        assert len(ext.images) >= 1
        assert any("Figure 1" in (im.get("caption") or "") for im in ext.images)

    def test_embedded_image_extraction(self, figure_pdf):
        from spiritwriter.ingest.extraction import extract_with_pymupdf
        ext = extract_with_pymupdf(figure_pdf, use_rendered_figures=False)
        assert len(ext.images) >= 1
        assert all("image_bytes" in im for im in ext.images)

    def test_mock_mode_builds_figure_atoms(self, figure_pdf):
        """mock_analyze turns extracted images into FIGURE atoms with
        captions (covers mock.py's image branch)."""
        from spiritwriter.models.document import AtomType
        graph = asyncio.run(DocumentIngestor(mock_mode=True).ingest(str(figure_pdf)))
        figs = [a for a in graph.atoms.values() if a.atom_type == AtomType.FIGURE]
        assert figs, "expected at least one FIGURE atom"
        assert graph.figures  # graph-level figure index populated

    def test_llm_mode_describes_figures_via_vision(self, figure_pdf):
        """_llm_analyze routes each image through query_with_image —
        covers _describe_image and the figure-atom loop."""
        from spiritwriter.models.document import AtomType

        description = "A scatter plot of synthetic results."

        def respond(prompt: str) -> str:
            if "Describe this figure" in prompt:
                return description
            if "Classify each text block" in prompt:
                return json.dumps({"title": "T", "authors": [],
                                   "blocks": [{"block_index": 0, "type": "paragraph",
                                               "topics": [], "entities": [],
                                               "importance": 0.5}]})
            if "Generate summaries" in prompt:
                return json.dumps({"one_sentence": "s", "one_paragraph": "p",
                                   "full_summary": "f"})
            return "{}"

        graph = asyncio.run(
            DocumentIngestor(llm_provider=MockLLMProvider(respond)).ingest(str(figure_pdf)))
        figs = [a for a in graph.atoms.values() if a.atom_type == AtomType.FIGURE]
        assert figs, "expected a FIGURE atom"
        assert any(a.content == description for a in figs)


# ── Extraction internals (carried from CSP's test_document_ingestor) ──


@pytest.fixture
def bold_pdf(tmp_path):
    """A 1-page PDF with a bold heading + body, for bold/metadata extraction."""
    fitz = pytest.importorskip("fitz")
    path = tmp_path / "paper.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 80), "Machine Learning for Climate Analysis",
                     fontsize=20, fontname="helv")
    page.insert_text((72, 120), "1. Introduction", fontsize=14, fontname="hebo")  # bold
    page.insert_text((72, 150),
                     "Climate change poses challenges. Machine learning offers tools.",
                     fontsize=11, fontname="helv")
    doc.save(str(path))
    doc.close()
    return path


class TestExtractionInternals:
    """Free-function extraction internals — moved here when CSP's ingestor
    consolidated onto spiritwriter (port step 5)."""

    def test_blocks_carry_metadata(self, bold_pdf):
        from spiritwriter.ingest.extraction import extract_with_pymupdf
        ext = extract_with_pymupdf(bold_pdf)
        assert ext.page_count == 1
        assert ext.text_blocks
        for block in ext.text_blocks:
            assert {"text", "page", "bbox", "font_size", "is_bold"} <= block.keys()
            assert isinstance(block["page"], int)
            assert len(block["bbox"]) == 4

    def test_detects_bold(self, bold_pdf):
        from spiritwriter.ingest.extraction import extract_with_pymupdf
        ext = extract_with_pymupdf(bold_pdf)
        assert any(b["is_bold"] for b in ext.text_blocks)

    def test_metadata_keys_present(self, bold_pdf):
        from spiritwriter.ingest.extraction import extract_with_pymupdf
        ext = extract_with_pymupdf(bold_pdf)
        assert "title" in ext.metadata and "author" in ext.metadata

    def test_find_caption_hit(self):
        from spiritwriter.ingest.extraction import find_caption
        img_info = {"page": 0, "bbox": (100, 100, 400, 300)}
        text_blocks = [
            {"page": 0, "text": "Some paragraph text", "bbox": (100, 50, 400, 90)},
            {"page": 0, "text": "Figure 1: Temperature over time", "bbox": (100, 310, 400, 330)},
            {"page": 0, "text": "Another paragraph", "bbox": (100, 400, 400, 450)},
        ]
        assert find_caption(img_info, text_blocks) == "Figure 1: Temperature over time"

    def test_find_caption_no_match(self):
        from spiritwriter.ingest.extraction import find_caption
        img_info = {"page": 0, "bbox": (100, 100, 400, 300)}
        text_blocks = [{"page": 0, "text": "Regular text", "bbox": (100, 310, 400, 330)}]
        assert find_caption(img_info, text_blocks) is None

    def test_extract_mock_topics(self):
        from spiritwriter.ingest.mock import extract_mock_topics
        topics = extract_mock_topics(
            "Machine Learning for Climate Analysis using Deep Neural Networks")
        assert 0 < len(topics) <= 3
        assert any("machine" in t or "learning" in t or "climate" in t for t in topics)
