"""Regression coverage for examples/atoms/*.

Each shipped example exposes a ``build()`` function returning a
MemoryShard (or list of shards for the lineage case). The test
imports it, builds the shard(s), and verifies they parse, hash
deterministically, and round-trip through a temp ShardStore.

The tests exist so the docs don't drift from the runtime. If the
ShardAtom / MemoryShard / ShardStore API changes, these break — and
the docs need to be updated to match.

Per-example details (kinds present, expected atom count) live with
each example's docstring; tests assert only the invariants every
example must satisfy.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from spiritwriter.fabric.shard import MemoryShard
from spiritwriter.fabric.store import ShardStore


_EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples" / "atoms"


# Each entry: (filename, expected atom count in the shard).
# For 13_lineage_variants the entry covers the parent shard only;
# its variants are checked separately.
_SINGLE_SHARD_EXAMPLES = [
    ("01_fact.py", 3),
    ("02_decision.py", 2),
    ("03_preference.py", 3),
    ("04_convention.py", 3),
    ("05_context.py", 2),
    ("06_checkpoint.py", 1),
    ("08_instruction.py", 2),
    ("10_entity.py", 3),
    ("11_mixed_kind.py", 4),
    ("12_minimal.py", 1),
]


def _load_module(filename: str):
    """Load an example module by filename (no PYTHONPATH games)."""
    path = _EXAMPLES_DIR / filename
    spec = importlib.util.spec_from_file_location(
        f"_atom_example_{filename[:-3]}", path,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("filename,expected_atom_count", _SINGLE_SHARD_EXAMPLES)
def test_example_builds_valid_shard(tmp_path, filename, expected_atom_count):
    """Each example's build() returns a usable MemoryShard."""
    mod = _load_module(filename)
    shard = mod.build()
    assert isinstance(shard, MemoryShard), \
        f"{filename}: build() must return a MemoryShard, got {type(shard)}"
    assert len(shard.atoms) == expected_atom_count, \
        f"{filename}: expected {expected_atom_count} atoms, got {len(shard.atoms)}"
    assert shard.scope, f"{filename}: scope must be non-empty"
    assert shard.origin, f"{filename}: origin must be non-empty"


@pytest.mark.parametrize("filename,_", _SINGLE_SHARD_EXAMPLES)
def test_example_hashes_deterministically(tmp_path, filename, _):
    """Same build() call across two invocations must produce identical shard_id."""
    mod = _load_module(filename)
    s1 = mod.build()
    s2 = mod.build()
    assert s1.shard_id == s2.shard_id, \
        f"{filename}: build() is non-deterministic; shard_id differs"


@pytest.mark.parametrize("filename,_", _SINGLE_SHARD_EXAMPLES)
def test_example_round_trips_through_store(tmp_path, filename, _):
    """put() returns a ref whose shard_id matches; get() returns equivalent atoms."""
    mod = _load_module(filename)
    shard = mod.build()
    store = ShardStore(tmp_path)
    ref = store.put(shard)
    assert ref.shard_id == shard.shard_id
    retrieved = store.get(ref.shard_id)
    assert retrieved is not None
    assert len(retrieved.atoms) == len(shard.atoms)
    # Each atom's content_hash must match — proves dataclass round-trip
    for orig, back in zip(shard.atoms, retrieved.atoms):
        assert orig.content_hash == back.content_hash, \
            f"{filename}: atom content_hash mismatch on round-trip"


# ── 07: CHECKPOINT with trace ──────────────────────────────────────


def test_checkpoint_with_trace(tmp_path):
    """07 builds a CHECKPOINT shard pinned to a real trace event; the
    trace chain must verify and the shard must round-trip through the
    store with source_ref/trace_ref preserved.

    Note on determinism: unlike the single-shard examples, 07's shard_id
    is *not* stable across runs because trace events include wall-clock
    timestamps that flow into the chain hash → source_ref → atom hash →
    shard_id. The base CHECKPOINT shape (06) is covered by the
    parametrized determinism check; 07 is the trace-integration overlay."""
    from spiritwriter.fabric.emitter import verify_chain
    mod = _load_module("07_checkpoint_with_trace.py")
    trace_path = tmp_path / "trace.jsonl"
    shard, events = mod.build(trace_path=trace_path)
    assert len(shard.atoms) == 1
    atom = shard.atoms[0]
    assert atom.source_ref is not None, \
        "checkpoint atom must carry source_ref pinning the trace event"
    assert atom.source_ref.startswith("chain:run-abc-123#"), \
        "source_ref should encode run_id + event hash"
    assert shard.trace_ref == atom.source_ref, \
        "shard-level trace_ref should mirror the atom's source_ref"
    assert verify_chain(events), "trace chain integrity must hold"

    # Round-trip through the store with source_ref + trace_ref preserved
    store = ShardStore(tmp_path / "shards")
    ref = store.put(shard)
    assert ref.shard_id == shard.shard_id
    back = store.get(ref.shard_id)
    assert back is not None
    assert back.atoms[0].source_ref == atom.source_ref, \
        "source_ref must round-trip through store"
    assert back.trace_ref == shard.trace_ref, \
        "shard-level trace_ref must round-trip through store"


# ── 09: INSTRUCTION via delegation ──────────────────────────────────


def test_instruction_delegation():
    """09 runs the full package_job flow end to end. Smoke-tests that
    the example doesn't crash; main() manages its own temp dir via
    tempfile.TemporaryDirectory(), so no fixtures are needed here.
    Details (instructions present in the task shard, content shard
    reachable via the entitlement) are covered by the example's own
    runtime path."""
    mod = _load_module("09_instruction_delegation.py")
    mod.main()  # raises if any step fails


# ── 13: parent + variants ──────────────────────────────────────────


def test_lineage_variants_build_parent_plus_two(tmp_path):
    """13 returns parent + 2 variants, variants link back via parent_shard_id,
    all share scope + entity. All three round-trip through the store."""
    mod = _load_module("13_lineage_variants.py")
    shards = mod.build()
    assert len(shards) == 3, "expected parent + 2 variants"
    parent, *variants = shards
    assert parent.parent_shard_id is None, "parent has no parent"
    for v in variants:
        assert v.parent_shard_id == parent.shard_id, \
            "variant must link back to the parent's shard_id"
        assert v.scope == parent.scope, \
            "variants must share scope with parent"
        # Same entity field on all atoms
        parent_entity = parent.atoms[0].entity
        for atom in v.atoms:
            assert atom.entity == parent_entity, \
                "variant atoms must share the parent's entity"

    # Determinism: rebuild → same shard_ids
    shards2 = mod.build()
    for s1, s2 in zip(shards, shards2):
        assert s1.shard_id == s2.shard_id, \
            "13 build() is non-deterministic; shard_id differs across runs"

    # Round-trip parent + variants through the store; parent_shard_id
    # must survive the trip on the variants.
    store = ShardStore(tmp_path / "shards")
    refs = [store.put(s) for s in shards]
    for s, r in zip(shards, refs):
        assert r.shard_id == s.shard_id
        back = store.get(r.shard_id)
        assert back is not None
        assert back.parent_shard_id == s.parent_shard_id, \
            "parent_shard_id must round-trip through store"
        for orig_atom, back_atom in zip(s.atoms, back.atoms):
            assert orig_atom.content_hash == back_atom.content_hash, \
                "atom content_hash mismatch on round-trip"
