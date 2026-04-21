"""Encode/decode GeoView as a Spiritwriter MemoryShard.

A shared map view is a memory of a place. This module converts between
GeoView (structured data) and MemoryShard (content-addressed atoms).

Atom schema (entity="view"):
    zoom    — FACT, integer as string
    center  — FACT, "lat,lng" to 4 decimal places
    layers  — FACT, comma-separated layer keys (omitted if all visible)
    select  — FACT, element selection key (omitted if none)
    annotation — ENTITY, plaintext note (omitted if none)

Plus an app origin atom (entity="app", key="origin") to identify
which application created the view.
"""

from __future__ import annotations

from spiritwriter.geo.types import GeoView
from spiritwriter.fabric.shard import (
    AtomKind,
    DecayClass,
    MemoryShard,
    ShardAtom,
)

SCOPE_GEO_VIEW = "geo:view"
SCOPE_GEO_VIEW_SEALED = "geo:view:sealed"


def geo_view_to_shard(
    view: GeoView,
    origin: str = "unknown",
    meta: dict | None = None,
) -> MemoryShard:
    """Encode a GeoView as a MemoryShard.

    Args:
        view: The geographic view state to encode.
        origin: Creating agent/app ID (e.g., "frio", "forager").
        meta: Optional metadata dict (view_count, creator_pubkey, etc.).

    Returns:
        A content-addressed MemoryShard with geo:view scope.
    """
    atoms = [
        ShardAtom(
            text=f"Zoom level: {view.zoom}",
            kind=AtomKind.FACT,
            entity="view",
            key="zoom",
            value=str(view.zoom),
        ),
        ShardAtom(
            text=f"Center: {view.center_lat:.4f},{view.center_lng:.4f}",
            kind=AtomKind.FACT,
            entity="view",
            key="center",
            value=f"{view.center_lat:.4f},{view.center_lng:.4f}",
        ),
    ]

    if view.layers is not None:
        atoms.append(ShardAtom(
            text=f"Visible layers: {','.join(view.layers)}",
            kind=AtomKind.FACT,
            entity="view",
            key="layers",
            value=",".join(view.layers),
        ))

    if view.select is not None:
        atoms.append(ShardAtom(
            text=f"Selected: {view.select}",
            kind=AtomKind.FACT,
            entity="view",
            key="select",
            value=view.select,
        ))

    if view.annotation is not None:
        atoms.append(ShardAtom(
            text=view.annotation,
            kind=AtomKind.ENTITY,
            entity="view",
            key="annotation",
            value=view.annotation,
        ))

    if view.bbox is not None:
        s, w, n, e = view.bbox
        atoms.append(ShardAtom(
            text=f"Bounding box: {s:.4f},{w:.4f},{n:.4f},{e:.4f}",
            kind=AtomKind.FACT,
            entity="view",
            key="bbox",
            value=f"{s:.4f},{w:.4f},{n:.4f},{e:.4f}",
        ))

    # App origin atom
    atoms.append(ShardAtom(
        text=f"App: {origin}",
        kind=AtomKind.FACT,
        entity="app",
        key="origin",
        value=origin,
    ))

    shard_meta = {"view_count": 0}
    if meta:
        shard_meta.update(meta)

    return MemoryShard(
        atoms=atoms,
        scope=SCOPE_GEO_VIEW,
        origin=origin,
        decay_class=DecayClass.ACTIVE,
        tags=["geo:view"],
        meta=shard_meta,
    )


def shard_to_geo_view(shard: MemoryShard) -> GeoView:
    """Decode a MemoryShard back to a GeoView.

    Handles missing atoms gracefully with sensible defaults.
    """
    zoom = 5
    center_lat = 0.0
    center_lng = 0.0
    layers = None
    select = None
    annotation = None
    bbox = None

    for atom in shard.atoms:
        if atom.entity == "view" and atom.key == "zoom":
            try:
                zoom = int(atom.value)
            except (TypeError, ValueError):
                pass
        elif atom.entity == "view" and atom.key == "center":
            try:
                parts = atom.value.split(",")
                center_lat = float(parts[0])
                center_lng = float(parts[1])
            except (TypeError, ValueError, IndexError):
                pass
        elif atom.entity == "view" and atom.key == "layers":
            if atom.value:
                layers = [k.strip() for k in atom.value.split(",") if k.strip()]
        elif atom.entity == "view" and atom.key == "select":
            select = atom.value
        elif atom.entity == "view" and atom.key == "annotation":
            annotation = atom.value
        elif atom.entity == "view" and atom.key == "bbox":
            try:
                parts = atom.value.split(",")
                bbox = (float(parts[0]), float(parts[1]),
                        float(parts[2]), float(parts[3]))
            except (TypeError, ValueError, IndexError):
                pass

    return GeoView(
        zoom=zoom,
        center_lat=center_lat,
        center_lng=center_lng,
        layers=layers,
        select=select,
        annotation=annotation,
        bbox=bbox,
    )
