"""Tests for the spiritwriter-geo module."""

import pytest

from spiritwriter.geo import (
    GeoView,
    SCOPE_GEO_VIEW,
    SCOPE_GEO_VIEW_SEALED,
    geo_view_to_shard,
    shard_to_geo_view,
)
from spiritwriter.trace.shard import AtomKind, DecayClass


class TestGeoView:
    def test_basic_creation(self):
        view = GeoView(zoom=8, center_lat=36.78, center_lng=-119.42)
        assert view.zoom == 8
        assert view.center_lat == 36.78
        assert view.center_lng == -119.42
        assert view.layers is None
        assert view.select is None
        assert view.annotation is None
        assert view.bbox is None

    def test_full_creation(self):
        view = GeoView(
            zoom=9,
            center_lat=34.5,
            center_lng=-117.4,
            layers=["ice", "fam"],
            select="ice:adelanto",
            annotation="Look at this cluster",
        )
        assert view.layers == ["ice", "fam"]
        assert view.select == "ice:adelanto"
        assert view.annotation == "Look at this cluster"

    def test_annotation_stripped(self):
        view = GeoView(zoom=5, center_lat=0, center_lng=0, annotation="  hello  ")
        assert view.annotation == "hello"

    def test_annotation_truncated(self):
        long_text = "x" * 300
        view = GeoView(zoom=5, center_lat=0, center_lng=0, annotation=long_text)
        assert len(view.annotation) == 280

    def test_annotation_empty_becomes_none(self):
        view = GeoView(zoom=5, center_lat=0, center_lng=0, annotation="   ")
        assert view.annotation is None

    def test_annotation_control_chars_removed(self):
        view = GeoView(zoom=5, center_lat=0, center_lng=0, annotation="hello\x00world")
        assert view.annotation == "helloworld"


class TestGeoViewToShard:
    def test_minimal_view(self):
        view = GeoView(zoom=8, center_lat=36.78, center_lng=-119.42)
        shard = geo_view_to_shard(view, origin="frio")
        assert shard.scope == SCOPE_GEO_VIEW
        assert shard.origin == "frio"
        assert shard.decay_class == DecayClass.ACTIVE
        assert "geo:view" in shard.tags

        # Should have zoom, center, and app origin atoms (3 total)
        assert len(shard.atoms) == 3

        atom_keys = {(a.entity, a.key) for a in shard.atoms}
        assert ("view", "zoom") in atom_keys
        assert ("view", "center") in atom_keys
        assert ("app", "origin") in atom_keys

    def test_full_view(self):
        view = GeoView(
            zoom=9,
            center_lat=34.5,
            center_lng=-117.4,
            layers=["ice", "fam"],
            select="ice:adelanto",
            annotation="SoCal ICE facilities",
            bbox=(34.0, -118.5, 35.0, -117.0),
        )
        shard = geo_view_to_shard(view, origin="frio")

        # zoom, center, layers, select, annotation, bbox, app origin
        assert len(shard.atoms) == 7

        atom_map = {(a.entity, a.key): a for a in shard.atoms}

        assert atom_map[("view", "zoom")].value == "9"
        assert atom_map[("view", "center")].value == "34.5000,-117.4000"
        assert atom_map[("view", "layers")].value == "ice,fam"
        assert atom_map[("view", "select")].value == "ice:adelanto"
        assert atom_map[("view", "annotation")].value == "SoCal ICE facilities"
        assert atom_map[("view", "bbox")].value == "34.0000,-118.5000,35.0000,-117.0000"
        assert atom_map[("app", "origin")].value == "frio"

    def test_atom_kinds(self):
        view = GeoView(
            zoom=8, center_lat=36.78, center_lng=-119.42,
            annotation="Note",
        )
        shard = geo_view_to_shard(view, origin="frio")
        atom_map = {(a.entity, a.key): a for a in shard.atoms}

        # Most view atoms are FACT
        assert atom_map[("view", "zoom")].kind == AtomKind.FACT
        assert atom_map[("view", "center")].kind == AtomKind.FACT
        # Annotation is ENTITY (user-generated content)
        assert atom_map[("view", "annotation")].kind == AtomKind.ENTITY

    def test_meta_defaults(self):
        view = GeoView(zoom=5, center_lat=0, center_lng=0)
        shard = geo_view_to_shard(view, origin="frio")
        assert shard.meta["view_count"] == 0

    def test_meta_merge(self):
        view = GeoView(zoom=5, center_lat=0, center_lng=0)
        shard = geo_view_to_shard(view, origin="frio", meta={"creator_pubkey": "abc123"})
        assert shard.meta["view_count"] == 0
        assert shard.meta["creator_pubkey"] == "abc123"

    def test_optional_fields_omitted(self):
        """When layers/select/annotation are None, those atoms should not exist."""
        view = GeoView(zoom=5, center_lat=0, center_lng=0)
        shard = geo_view_to_shard(view, origin="test")
        atom_keys = {(a.entity, a.key) for a in shard.atoms}
        assert ("view", "layers") not in atom_keys
        assert ("view", "select") not in atom_keys
        assert ("view", "annotation") not in atom_keys
        assert ("view", "bbox") not in atom_keys


class TestShardToGeoView:
    def test_round_trip_minimal(self):
        original = GeoView(zoom=8, center_lat=36.78, center_lng=-119.42)
        shard = geo_view_to_shard(original, origin="frio")
        restored = shard_to_geo_view(shard)

        assert restored.zoom == 8
        assert abs(restored.center_lat - 36.78) < 0.001
        assert abs(restored.center_lng - (-119.42)) < 0.001
        assert restored.layers is None
        assert restored.select is None
        assert restored.annotation is None

    def test_round_trip_full(self):
        original = GeoView(
            zoom=9,
            center_lat=34.5,
            center_lng=-117.4,
            layers=["ice", "fam", "287j"],
            select="287g:floyd-ga",
            annotation="Georgia 287g cluster",
            bbox=(34.0, -118.5, 35.0, -117.0),
        )
        shard = geo_view_to_shard(original, origin="frio")
        restored = shard_to_geo_view(shard)

        assert restored.zoom == 9
        assert abs(restored.center_lat - 34.5) < 0.001
        assert abs(restored.center_lng - (-117.4)) < 0.001
        assert restored.layers == ["ice", "fam", "287j"]
        assert restored.select == "287g:floyd-ga"
        assert restored.annotation == "Georgia 287g cluster"
        assert restored.bbox is not None
        assert abs(restored.bbox[0] - 34.0) < 0.001

    def test_missing_atoms_use_defaults(self):
        """A shard with no view atoms should return sensible defaults."""
        from spiritwriter.trace.shard import MemoryShard, ShardAtom
        shard = MemoryShard(
            atoms=[ShardAtom(text="empty", entity="app", key="origin", value="test")],
            scope=SCOPE_GEO_VIEW,
            origin="test",
        )
        view = shard_to_geo_view(shard)
        assert view.zoom == 5
        assert view.center_lat == 0.0
        assert view.center_lng == 0.0


class TestShardDeterminism:
    def test_same_view_same_shard_id(self):
        view = GeoView(zoom=8, center_lat=36.78, center_lng=-119.42, layers=["ice"])
        shard1 = geo_view_to_shard(view, origin="frio")
        shard2 = geo_view_to_shard(view, origin="frio")
        assert shard1.shard_id == shard2.shard_id

    def test_different_view_different_shard_id(self):
        view1 = GeoView(zoom=8, center_lat=36.78, center_lng=-119.42)
        view2 = GeoView(zoom=9, center_lat=36.78, center_lng=-119.42)
        shard1 = geo_view_to_shard(view1, origin="frio")
        shard2 = geo_view_to_shard(view2, origin="frio")
        assert shard1.shard_id != shard2.shard_id

    def test_different_origin_different_shard_id(self):
        view = GeoView(zoom=8, center_lat=36.78, center_lng=-119.42)
        shard1 = geo_view_to_shard(view, origin="frio")
        shard2 = geo_view_to_shard(view, origin="forager")
        assert shard1.shard_id != shard2.shard_id


class TestScopeConstants:
    def test_scope_values(self):
        assert SCOPE_GEO_VIEW == "geo:view"
        assert SCOPE_GEO_VIEW_SEALED == "geo:view:sealed"
