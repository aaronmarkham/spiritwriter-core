"""Geo-shard types — domain-agnostic geographic view primitives.

A GeoView represents a map view state: where you're looking, what's visible,
and optionally what's selected and annotated. This is the shared primitive
for any app that has a map — Frio, foraging apps, travel tools, etc.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# Default max annotation length. Consumers can override per-instance via
# max_annotation_length. 280 is a sensible default — long enough for context,
# short enough to discourage graffiti. The real abuse defense is at the
# rendering layer (always use textContent, never innerHTML).
DEFAULT_MAX_ANNOTATION_LENGTH = 280


@dataclass
class GeoView:
    """A geographic map view state.

    Encodes everything needed to restore a map view: position, zoom,
    visible layers, selected element, and an optional annotation.

    Annotations are user-generated content. This layer validates length
    and strips control characters, but **rendering must also sanitize**
    (textContent only, no innerHTML). Unicode is fully supported —
    emoji, CJK, accented characters all pass through. Only non-printable
    control characters (null bytes, escape sequences) are stripped.
    """
    zoom: int
    center_lat: float
    center_lng: float
    layers: list[str] | None = None       # None = all layers visible
    select: str | None = None             # element selection key
    annotation: str | None = None         # plaintext note (unicode OK)
    bbox: tuple[float, float, float, float] | None = None  # (s, w, n, e) — future
    max_annotation_length: int = field(default=DEFAULT_MAX_ANNOTATION_LENGTH, repr=False)

    def __post_init__(self):
        if self.annotation is not None:
            # Strip non-printable control characters (keeps unicode, emoji, etc.)
            cleaned = "".join(
                c for c in self.annotation if c.isprintable() or c in ("\n",)
            ).strip()
            if len(cleaned) > self.max_annotation_length:
                cleaned = cleaned[:self.max_annotation_length]
            self.annotation = cleaned if cleaned else None
