"""Shared hashing and string-extraction helpers for the audit module.

Factored out of provenance.py and verify.py so the two paths stay in
sync — if they drift, evidence derivation (L4) silently produces false
positives or negatives.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path


def sha256_file(path: str | Path) -> str:
    """SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dex_string_pattern(min_length: int) -> bytes:
    """Canonical pattern for ASCII string extraction from DEX files.

    Both the provenance emitter (during extraction) and the verifier
    (during L4 finding derivability) must use the exact same pattern,
    otherwise evidence strings written by one won't be found by the
    other.
    """
    return rb"[\x20-\x7e]{" + str(min_length).encode() + rb",}"


def extract_dex_strings(dex_path: str | Path, min_length: int = 8) -> set[str]:
    """Extract ASCII strings (length >= min_length) from a DEX file."""
    data = Path(dex_path).read_bytes()
    matches = re.findall(dex_string_pattern(min_length), data)
    return {m.decode("ascii", errors="replace") for m in matches}
