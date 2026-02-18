"""PDF content extraction — PyMuPDF text blocks, rendered figures, embedded images."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Any


@dataclass
class ExtractionResult:
    """Result of document extraction before LLM analysis"""
    text_blocks: List[Dict[str, Any]]   # Raw text blocks with page/position
    images: List[Dict[str, Any]]        # Extracted images with metadata
    page_count: int
    metadata: Dict[str, Any]            # PDF metadata (title, author, etc.)


def extract_with_pymupdf(path: Path, use_rendered_figures: bool = True) -> ExtractionResult:
    """
    Use PyMuPDF (fitz) to extract raw text blocks and images.

    Returns structured extraction with positional information.

    Args:
        path: Path to the PDF file
        use_rendered_figures: If True, extract figures by rendering pages and
            detecting figure regions (better for academic PDFs). If False, use
            raw embedded image extraction (faster but may produce fragments).
    """
    import fitz  # PyMuPDF

    doc = fitz.open(str(path))
    page_count = len(doc)

    text_blocks: List[Dict[str, Any]] = []
    images: List[Dict[str, Any]] = []
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
        images = extract_rendered_figures(path, text_blocks)
    else:
        # Fallback: Extract embedded images (may produce fragments)
        images = extract_embedded_images(path)

    return ExtractionResult(
        text_blocks=text_blocks,
        images=images,
        page_count=page_count,
        metadata=metadata,
    )


def extract_rendered_figures(
    path: Path, text_blocks: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Extract figures by rendering pages and detecting figure regions.

    This method finds figure captions in the text and extracts the image
    region above each caption. Works much better for academic PDFs where
    embedded images are often fragmented.
    """
    import fitz
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


def extract_embedded_images(path: Path) -> List[Dict[str, Any]]:
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


def find_caption(
    img_info: Dict[str, Any], text_blocks: List[Dict[str, Any]]
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
