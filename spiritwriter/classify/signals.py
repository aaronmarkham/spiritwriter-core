"""Stateless detection functions for document type classification signals."""

import re
from typing import Dict, List, Optional

from spiritwriter.stopwords import INSTITUTION_PATTERNS, INSTITUTION_INDICATOR_WORDS


def has_doi(text: str) -> bool:
    return bool(re.search(r"10\.\d{4,}/[\w\.\-/]+", text))


def extract_doi(text: str) -> Optional[str]:
    match = re.search(r"(10\.\d{4,}/[\w\.\-/]+)", text)
    return match.group(1) if match else None


def has_abstract_header(text_blocks: List[Dict]) -> bool:
    for block in text_blocks[:15]:
        text = block.get("text", "").strip().lower()
        if text.startswith("abstract") or text == "abstract":
            return True
    return False


def has_references_section(text_blocks: List[Dict]) -> bool:
    start_idx = int(len(text_blocks) * 0.7)
    for block in text_blocks[start_idx:]:
        text = block.get("text", "").strip().lower()
        if text in ("references", "bibliography", "works cited"):
            return True
    return False


def has_dateline(text_blocks: List[Dict]) -> bool:
    pattern = r"^[A-Z]{3,}\s*[,\-]\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    for block in text_blocks[:10]:
        if re.match(pattern, block.get("text", "").strip()):
            return True
    return False


def has_news_byline(text_blocks: List[Dict]) -> bool:
    for block in text_blocks[:10]:
        text = block.get("text", "").strip()
        if re.match(r"^By\s+[A-Z][a-z]+\s+[A-Z][a-z]+", text):
            return True
        if re.search(r"(AP|Reuters|AFP|UPI)\s*[-–—]", text):
            return True
    return False


def count_equations(text_blocks: List[Dict]) -> int:
    count = 0
    for block in text_blocks:
        text = block.get("text", "")
        if re.search(r"\(\d+\)\s*$", text):
            count += 1
        if re.search(r"\\[a-z]+\{", text):
            count += 1
    return count


def is_affiliation_block(text: str) -> bool:
    text_lower = text.lower()
    if any(word in text_lower for word in INSTITUTION_INDICATOR_WORDS):
        return True
    if re.search(r"[\w\.\-]+@[\w\.\-]+\.\w+", text):
        return True
    return False


def is_byline(text: str) -> bool:
    return bool(re.match(r"^By\s+[A-Z][a-z]+", text.strip()))


def extract_institutions(text: str) -> List[str]:
    institutions = []
    for pattern in INSTITUTION_PATTERNS:
        matches = pattern.findall(text)
        institutions.extend(matches)
    return institutions
