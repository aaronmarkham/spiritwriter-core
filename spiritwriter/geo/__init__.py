"""spiritwriter-geo — domain-agnostic geo-shard primitives.

A shared map view is a memory of a place. This module provides the
encoding/decoding layer between geographic view state and Spiritwriter
memory shards. The primitive is app-agnostic — Frio, foraging apps,
and any future geo app share the same atom schema.

Usage:
    from spiritwriter.geo import GeoView, geo_view_to_shard, shard_to_geo_view

    view = GeoView(zoom=9, center_lat=34.5, center_lng=-117.4, layers=["ice", "fam"])
    shard = geo_view_to_shard(view, origin="frio")
    restored = shard_to_geo_view(shard)
"""

from spiritwriter.geo.types import GeoView
from spiritwriter.geo.view_shard import (
    SCOPE_GEO_VIEW,
    SCOPE_GEO_VIEW_SEALED,
    geo_view_to_shard,
    shard_to_geo_view,
)

__all__ = [
    "GeoView",
    "SCOPE_GEO_VIEW",
    "SCOPE_GEO_VIEW_SEALED",
    "geo_view_to_shard",
    "shard_to_geo_view",
]
