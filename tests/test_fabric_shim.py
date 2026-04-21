"""Tests for the spiritwriter.trace → spiritwriter.fabric deprecation shim.

The rename in 0.5.0 moved all shard-protocol runtime code from
``spiritwriter.trace`` to ``spiritwriter.fabric``. The old package
remains as a shim that re-exports from fabric and emits a
``DeprecationWarning``. These tests pin that contract so the shim
can't silently drift before it's removed in 0.6.0.
"""

from __future__ import annotations

import importlib
import sys
import warnings


def _reimport_trace():
    """Drop cached trace modules and return a freshly imported spiritwriter.trace."""
    for name in list(sys.modules):
        if name == "spiritwriter.trace" or name.startswith("spiritwriter.trace."):
            del sys.modules[name]
    return importlib.import_module("spiritwriter.trace")


def test_deprecation_warning_fires_on_import():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _reimport_trace()

    messages = [
        str(w.message)
        for w in caught
        if issubclass(w.category, DeprecationWarning)
        and "spiritwriter.trace" in str(w.message)
    ]
    assert messages, "importing spiritwriter.trace must emit a DeprecationWarning"
    assert any("0.6.0" in m for m in messages), (
        "warning should name the removal version so users have a concrete deadline"
    )
    assert any("spiritwriter.fabric" in m for m in messages), (
        "warning should point users at the replacement package"
    )


def test_top_level_reexports_are_same_objects():
    """from spiritwriter.trace import X must yield the exact fabric object."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import spiritwriter.fabric as fabric
        import spiritwriter.trace as trace

    for name in fabric.__all__:
        assert getattr(trace, name) is getattr(fabric, name), (
            f"{name} differs between spiritwriter.trace and spiritwriter.fabric"
        )


def test_submodule_imports_resolve_to_fabric():
    """from spiritwriter.trace.<sub> import X must work for every fabric submodule."""
    submodules = [
        "shard",
        "store",
        "emitter",
        "crypto",
        "sealed",
        "entitlement",
        "network",
        "studio_job",
        "studio_runner",
        "canonicalize",
        "extract",
        "visualize",
        "backends",
        "backends.ipfs",
    ]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        for sub in submodules:
            trace_mod = importlib.import_module(f"spiritwriter.trace.{sub}")
            fabric_mod = importlib.import_module(f"spiritwriter.fabric.{sub}")
            assert trace_mod is fabric_mod, (
                f"spiritwriter.trace.{sub} is not aliased to spiritwriter.fabric.{sub}"
            )


def test_memoryshard_roundtrip_via_shim():
    """Creating and serializing a shard through the old import path still works."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from spiritwriter.trace.shard import MemoryShard, ShardAtom, AtomKind
        from spiritwriter.fabric.shard import MemoryShard as FabricMemoryShard

    assert MemoryShard is FabricMemoryShard

    shard = MemoryShard(
        atoms=[ShardAtom(kind=AtomKind.FACT, text="shim smoke test")],
        scope="test",
        origin="test-agent",
        tags=["shim-test"],
    )
    assert shard.shard_id
    roundtripped = MemoryShard.from_json(shard.to_json())
    assert roundtripped.shard_id == shard.shard_id


def test_fabric_docstring_mentions_fabric():
    """The new package docstring should explain the name (per issue #4 comment)."""
    import spiritwriter.fabric as fabric

    assert fabric.__doc__ is not None
    assert "fabric" in fabric.__doc__.lower()
