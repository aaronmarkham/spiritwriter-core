"""Geo-shard types — domain-agnostic geographic view primitives.

A GeoView represents a map view state: where you're looking, what's visible,
and optionally what's selected and annotated. This is the shared primitive
for any app that has a map — Frio, foraging apps, travel tools, etc.
"""

from __future__ import annotations

from dataclasses import dataclass


MAX_ANNOTATION_LENGTH = 280


@dataclass
class GeoView:
    """A geographic map view state.

    Encodes everything needed to restore a map view: position, zoom,
    visible layers, selected element, and an optional annotation.
    """
    zoom: int
    center_lat: float
    center_lng: float
    layers: list[str] | None = None       # None = all layers visible
    select: str | None = None             # element selection key
    annotation: str | None = None         # plaintext note, max 280 chars
    bbox: tuple[float, float, float, float] | None = None  # (s, w, n, e) — future

    def __post_init__(self):
        if self.annotation is not None:
            # Strip control characters and whitespace
            cleaned = "".join(
                c for c in self.annotation if c.isprintable() or c in ("\n",)
            ).strip()
            if len(cleaned) > MAX_ANNOTATION_LENGTH:
                cleaned = cleaned[:MAX_ANNOTATION_LENGTH]
            self.annotation = cleaned if cleaned else None
