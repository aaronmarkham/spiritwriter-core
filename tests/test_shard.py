"""Tests for the memory shard system."""

import json
import tempfile
from pathlib import Path

import pytest

from spiritwriter.trace.shard import (
    MemoryShard,
    ShardAtom,
    ShardRef,
    AtomKind,
    DecayClass,
)
from spiritwriter.trace.store import ShardStore
from spiritwriter.trace.emitter import TraceEmitter
from spiritwriter.trace.extract import (
    extract_decisions,
    extract_facts,
    extract_atoms,
    extract_from_conversation,
    classify_decay,
)


# === ShardAtom Tests ===

class TestShardAtom:
    def test_basic_creation(self):
        atom = ShardAtom(text="Aaron prefers dark mode", kind=AtomKind.PREFERENCE)
        assert atom.text == "Aaron prefers dark mode"
        assert atom.kind == AtomKind.PREFERENCE
        assert atom.confidence == 1.0

    def test_structured_atom(self):
        atom = ShardAtom(
            text="Aaron's birthday is November 13",
            kind=AtomKind.FACT,
            entity="Aaron",
            key="birthday",
            value="November 13",
        )
        assert atom.entity == "Aaron"
        assert atom.key == "birthday"
        assert atom.value == "November 13"

    def test_content_hash_deterministic(self):
        atom1 = ShardAtom(text="test", kind=AtomKind.FACT, entity="x", key="y", value="z")
        atom2 = ShardAtom(text="test", kind=AtomKind.FACT, entity="x", key="y", value="z")
        assert atom1.content_hash == atom2.content_hash

    def test_content_hash_changes_with_content(self):
        atom1 = ShardAtom(text="test1", kind=AtomKind.FACT)
        atom2 = ShardAtom(text="test2", kind=AtomKind.FACT)
        assert atom1.content_hash != atom2.content_hash

    def test_roundtrip_dict(self):
        atom = ShardAtom(
            text="We chose X over Y",
            kind=AtomKind.DECISION,
            entity="decision",
            key="X over Y",
            value="performance",
            confidence=0.9,
            source_ref="trace:abc123",
        )
        d = atom.to_dict()
        restored = ShardAtom.from_dict(d)
        assert restored.text == atom.text
        assert restored.kind == atom.kind
        assert restored.entity == atom.entity
        assert restored.confidence == 0.9
        assert restored.source_ref == "trace:abc123"

    def test_minimal_dict(self):
        """Minimal atom should not include None fields."""
        atom = ShardAtom(text="hello", kind=AtomKind.CONTEXT)
        d = atom.to_dict()
        assert "entity" not in d
        assert "key" not in d
        assert "value" not in d
        assert "confidence" not in d  # default 1.0 omitted


# === MemoryShard Tests ===

class TestMemoryShard:
    def _make_shard(self, **kwargs):
        defaults = dict(
            atoms=[
                ShardAtom(text="CSP uses Python CLI", kind=AtomKind.FACT),
                ShardAtom(
                    text="Chose namespace packages over submodules",
                    kind=AtomKind.DECISION,
                    entity="decision",
                    key="namespace packages over submodules",
                    value="git submodules are painful",
                ),
            ],
            scope="project:csp",
            origin="agent:main:main",
            tags=["CSP project context"],
        )
        defaults.update(kwargs)
        return MemoryShard(**defaults)

    def test_shard_id_deterministic(self):
        s1 = self._make_shard()
        s2 = self._make_shard()
        assert s1.shard_id == s2.shard_id

    def test_shard_id_changes_with_atoms(self):
        s1 = self._make_shard()
        s2 = self._make_shard(atoms=[ShardAtom(text="different")])
        assert s1.shard_id != s2.shard_id

    def test_ref_creation(self):
        shard = self._make_shard()
        ref = shard.ref
        assert isinstance(ref, ShardRef)
        assert ref.shard_id == shard.shard_id
        assert ref.scope == "project:csp"
        assert ref.label == "CSP project context"

    def test_token_estimate(self):
        shard = self._make_shard()
        estimate = shard.token_estimate
        assert estimate > 0
        assert isinstance(estimate, int)

    def test_roundtrip_json(self):
        shard = self._make_shard(
            trace_ref="chain:abc123#42",
            meta={"version": "0.7.0"},
        )
        raw = shard.to_json()
        restored = MemoryShard.from_json(raw)
        assert restored.shard_id == shard.shard_id
        assert len(restored.atoms) == 2
        assert restored.scope == "project:csp"
        assert restored.trace_ref == "chain:abc123#42"
        assert restored.meta == {"version": "0.7.0"}

    def test_content_verification_on_load(self):
        shard = self._make_shard()
        d = json.loads(shard.to_json())
        # Tamper with content
        d["atoms"][0]["text"] = "TAMPERED"
        with pytest.raises(ValueError, match="content mismatch"):
            MemoryShard.from_dict(d)

    def test_hydrate_context(self):
        shard = self._make_shard()
        ctx = shard.hydrate_context()
        assert '<shard scope="project:csp"' in ctx
        assert "[fact]" in ctx
        assert "[decision]" in ctx
        assert "namespace packages" in ctx
        assert "</shard>" in ctx

    def test_instruction_hydration_rendering(self):
        shard = MemoryShard(
            atoms=[
                ShardAtom(text="Use cs figures inject to add diagrams", kind=AtomKind.INSTRUCTION, key="inject figure"),
                ShardAtom(text="Generic instruction without key", kind=AtomKind.INSTRUCTION),
                ShardAtom(text="A regular fact", kind=AtomKind.FACT),
            ],
            scope="project:test",
            origin="agent:test",
            tags=["test"],
        )
        ctx = shard.hydrate_context()
        assert "**inject figure**:" in ctx  # keyed instruction
        assert "⚡ Generic instruction" in ctx  # unkeyed instruction
        assert "[fact]" in ctx  # regular fact unchanged

    def test_parent_shard_tracking(self):
        s1 = self._make_shard()
        s2 = self._make_shard(
            atoms=[ShardAtom(text="updated fact")],
            parent_shard_id=s1.shard_id,
        )
        assert s2.parent_shard_id == s1.shard_id


# === ShardStore Tests ===

class TestShardStore:
    @pytest.fixture
    def store(self, tmp_path):
        return ShardStore(tmp_path / "shards")

    def _make_shard(self, text="test fact", scope="project:test"):
        return MemoryShard(
            atoms=[ShardAtom(text=text, kind=AtomKind.FACT)],
            scope=scope,
            origin="agent:test",
        )

    def test_put_and_get(self, store):
        shard = self._make_shard()
        ref = store.put(shard)
        assert ref.shard_id == shard.shard_id

        retrieved = store.get(ref.shard_id)
        assert retrieved is not None
        assert retrieved.shard_id == shard.shard_id
        assert len(retrieved.atoms) == 1

    def test_idempotent_put(self, store):
        shard = self._make_shard()
        ref1 = store.put(shard)
        ref2 = store.put(shard)
        assert ref1.shard_id == ref2.shard_id
        assert store.count() == 1

    def test_has(self, store):
        shard = self._make_shard()
        assert not store.has(shard.shard_id)
        store.put(shard)
        assert store.has(shard.shard_id)

    def test_delete(self, store):
        shard = self._make_shard()
        store.put(shard)
        assert store.delete(shard.shard_id)
        assert not store.has(shard.shard_id)
        assert store.count() == 0

    def test_delete_nonexistent(self, store):
        assert not store.delete("nonexistent")

    def test_get_nonexistent(self, store):
        assert store.get("nonexistent") is None

    def test_resolve_ref(self, store):
        shard = self._make_shard()
        ref = store.put(shard)
        resolved = store.resolve(ref)
        assert resolved is not None
        assert resolved.shard_id == shard.shard_id

    def test_resolve_many(self, store):
        s1 = self._make_shard("fact 1", "project:a")
        s2 = self._make_shard("fact 2", "project:b")
        ref1 = store.put(s1)
        ref2 = store.put(s2)

        shards = store.resolve_many([ref1, ref2])
        assert len(shards) == 2

    def test_resolve_many_skips_missing(self, store):
        s1 = self._make_shard()
        ref1 = store.put(s1)
        fake_ref = ShardRef(shard_id="nonexistent", scope="test")

        shards = store.resolve_many([ref1, fake_ref])
        assert len(shards) == 1

    def test_hydrate(self, store):
        s1 = self._make_shard("CSP uses Python", "project:csp")
        s2 = self._make_shard("Aaron likes dark mode", "user:prefs")
        ref1 = store.put(s1)
        ref2 = store.put(s2)

        ctx = store.hydrate([ref1, ref2])
        assert "CSP uses Python" in ctx
        assert "Aaron likes dark mode" in ctx
        assert "<shard" in ctx

    def test_hydrate_empty(self, store):
        assert store.hydrate([]) == ""

    def test_by_scope(self, store):
        s1 = self._make_shard("fact 1", "project:csp")
        s2 = self._make_shard("fact 2", "project:csp")
        s3 = self._make_shard("other fact", "project:other")
        store.put(s1)
        store.put(s2)
        store.put(s3)

        csp_shards = store.by_scope("project:csp")
        assert len(csp_shards) == 2
        other_shards = store.by_scope("project:other")
        assert len(other_shards) == 1

    def test_list_scopes(self, store):
        store.put(self._make_shard("a", "project:csp"))
        store.put(self._make_shard("b", "user:prefs"))
        scopes = store.list_scopes()
        assert set(scopes) == {"project:csp", "user:prefs"}

    def test_named_refs(self, store):
        shard = self._make_shard()
        store.put(shard)
        store.set_ref("project-csp-latest", shard.shard_id)

        resolved_id = store.get_ref("project-csp-latest")
        assert resolved_id == shard.shard_id

        resolved_shard = store.resolve_ref("project-csp-latest")
        assert resolved_shard is not None
        assert resolved_shard.shard_id == shard.shard_id

    def test_named_ref_nonexistent(self, store):
        assert store.get_ref("nope") is None
        assert store.resolve_ref("nope") is None

    def test_stats(self, store):
        store.put(self._make_shard("a", "project:csp"))
        store.put(self._make_shard("b", "project:csp"))
        store.put(self._make_shard("c", "user:prefs"))

        stats = store.stats()
        assert stats["total_shards"] == 3
        assert stats["total_atoms"] == 3
        assert stats["scopes"]["project:csp"] == 2

    def test_iter_all(self, store):
        store.put(self._make_shard("a"))
        store.put(self._make_shard("b"))
        store.put(self._make_shard("c"))

        all_shards = list(store.iter_all())
        assert len(all_shards) == 3

    def test_prune_expired(self, store):
        # Create a session shard with old timestamp
        shard = MemoryShard(
            atoms=[ShardAtom(text="temp state")],
            scope="session:debug",
            origin="agent:test",
            decay_class=DecayClass.SESSION,
            created_at="2020-01-01T00:00:00Z",  # Very old
        )
        store.put(shard)
        assert store.count() == 1

        pruned = store.prune_expired()
        assert pruned == 1
        assert store.count() == 0

    def test_prune_keeps_permanent(self, store):
        shard = MemoryShard(
            atoms=[ShardAtom(text="permanent fact")],
            scope="core:identity",
            origin="agent:test",
            decay_class=DecayClass.PERMANENT,
            created_at="2020-01-01T00:00:00Z",
        )
        store.put(shard)
        pruned = store.prune_expired()
        assert pruned == 0
        assert store.count() == 1


# === TraceEmitter Tests ===

class TestTraceEmitter:
    def test_emit_chain(self, tmp_path):
        out = str(tmp_path / "trace.jsonl")
        emitter = TraceEmitter(
            run_id="run-1",
            agent_id="agent:main",
            out_path=out,
        )

        evt1 = emitter.emit("shard_created", shard_id="sha:abc", scope="project:csp")
        assert evt1["prev_event_hash"] is None
        assert evt1["hash"] is not None

        evt2 = emitter.emit("shard_resolved", shard_id="sha:abc", resolved_by="agent:sub1")
        assert evt2["prev_event_hash"] == evt1["hash"]

        # Verify JSONL
        lines = Path(out).read_text().strip().split("\n")
        assert len(lines) == 2

    def test_shard_lifecycle_events(self, tmp_path):
        out = str(tmp_path / "trace.jsonl")
        emitter = TraceEmitter(run_id="run-1", agent_id="agent:main", out_path=out)

        evt = emitter.shard_created(shard_id="sha:abc", scope="project:csp", atom_count=5)
        assert evt["type"] == "shard_created"
        assert evt["atom_count"] == 5

        evt = emitter.shard_resolved(shard_id="sha:abc", by_agent="agent:sub1")
        assert evt["type"] == "shard_resolved"

        evt = emitter.shard_superseded(old_shard_id="sha:abc", new_shard_id="sha:def")
        assert evt["type"] == "shard_superseded"

    def test_spawn_with_shards(self, tmp_path):
        out = str(tmp_path / "trace.jsonl")
        emitter = TraceEmitter(run_id="run-1", agent_id="agent:main", out_path=out)

        refs = [
            {"shard_id": "sha:abc", "scope": "project:csp"},
            {"shard_id": "sha:def", "scope": "user:prefs"},
        ]
        evt = emitter.spawn_with_shards(
            child_agent_id="agent:sub1",
            shard_refs=refs,
            task="verify AssetRecord fields",
        )
        assert evt["type"] == "spawn_with_shards"
        assert len(evt["shard_refs"]) == 2
        assert evt["task"] == "verify AssetRecord fields"


# === Extraction Tests ===

class TestExtraction:
    def test_extract_decision_because(self):
        atoms = extract_decisions("We decided to use SQLite because it's faster")
        assert len(atoms) == 1
        assert atoms[0].kind == AtomKind.DECISION
        assert atoms[0].entity == "decision"
        assert "SQLite" in atoms[0].key

    def test_extract_decision_chose(self):
        atoms = extract_decisions("Chose namespace packages over submodules because git submodules are painful")
        assert len(atoms) >= 1
        assert atoms[0].kind in (AtomKind.DECISION, AtomKind.CONVENTION)

    def test_extract_convention_always(self):
        atoms = extract_decisions("Always use the librarian path")
        assert len(atoms) == 1
        assert atoms[0].kind == AtomKind.CONVENTION
        assert atoms[0].value == "always"

    def test_extract_convention_never(self):
        atoms = extract_decisions("Never use the legacy fallback")
        assert len(atoms) == 1
        assert atoms[0].kind == AtomKind.CONVENTION
        assert atoms[0].value == "never"

    def test_extract_fact_possessive(self):
        atoms = extract_facts("Aaron's birthday is November 13")
        assert len(atoms) == 1
        assert atoms[0].kind == AtomKind.FACT
        assert atoms[0].entity == "Aaron"
        assert atoms[0].key == "birthday"
        assert atoms[0].value == "November 13"

    def test_extract_fact_my(self):
        atoms = extract_facts("My timezone is America/Los_Angeles")
        assert len(atoms) == 1
        assert atoms[0].entity == "user"
        assert atoms[0].key == "timezone"

    def test_extract_preference(self):
        atoms = extract_facts("I prefer dark mode")
        assert len(atoms) == 1
        assert atoms[0].kind == AtomKind.PREFERENCE
        assert atoms[0].entity == "user"
        assert atoms[0].value == "dark mode"

    def test_extract_atoms_decisions_take_priority(self):
        atoms = extract_atoms("We decided to use Opus because it's smarter")
        assert len(atoms) >= 1
        assert atoms[0].kind == AtomKind.DECISION

    def test_extract_atoms_fallback_to_facts(self):
        atoms = extract_atoms("Aaron's email is aaron@example.com")
        assert len(atoms) >= 1

    def test_extract_from_conversation(self):
        messages = [
            {"role": "user", "text": "We decided to use namespace packages because submodules are painful"},
            {"role": "assistant", "text": "Got it, I'll track that decision."},
            {"role": "user", "text": "My timezone is Pacific"},
            {"role": "user", "text": "hi"},  # Too short, should be skipped
        ]
        atoms = extract_from_conversation(messages, source_ref="trace:abc")
        assert len(atoms) >= 1
        # At least the decision should be extracted
        decision_atoms = [a for a in atoms if a.kind == AtomKind.DECISION]
        assert len(decision_atoms) >= 1
        assert atoms[0].source_ref == "trace:abc"

    def test_extract_deduplication(self):
        messages = [
            {"role": "user", "text": "We decided to use X because Y"},
            {"role": "user", "text": "We decided to use X because Y"},  # duplicate
        ]
        atoms = extract_from_conversation(messages)
        assert len(atoms) == 1

    def test_classify_decay_decision(self):
        atom = ShardAtom(text="chose X", kind=AtomKind.DECISION)
        assert classify_decay(atom) == DecayClass.PERMANENT

    def test_classify_decay_convention(self):
        atom = ShardAtom(text="always use X", kind=AtomKind.CONVENTION)
        assert classify_decay(atom) == DecayClass.PERMANENT

    def test_classify_decay_session(self):
        atom = ShardAtom(text="currently debugging the build", kind=AtomKind.CONTEXT)
        assert classify_decay(atom) == DecayClass.SESSION

    def test_classify_decay_active(self):
        atom = ShardAtom(text="working on the figures CLI", kind=AtomKind.CONTEXT)
        assert classify_decay(atom) == DecayClass.ACTIVE

    def test_classify_decay_permanent_by_key(self):
        atom = ShardAtom(text="Aaron's birthday", kind=AtomKind.FACT, key="birthday")
        assert classify_decay(atom) == DecayClass.PERMANENT

    def test_classify_decay_default_stable(self):
        atom = ShardAtom(text="spiritwriter uses Apache 2.0", kind=AtomKind.FACT)
        assert classify_decay(atom) == DecayClass.STABLE


# === Integration Test ===

class TestIntegration:
    """End-to-end: extract → shard → store → hydrate."""

    def test_full_pipeline(self, tmp_path):
        # 1. Extract from conversation
        messages = [
            {"role": "user", "text": "We decided to use namespace packages because submodules suck"},
            {"role": "user", "text": "Always use the librarian path in CSP"},
            {"role": "user", "text": "My timezone is America/Los_Angeles"},
        ]
        atoms = extract_from_conversation(messages, source_ref="session:abc")
        assert len(atoms) >= 2

        # 2. Create shard
        shard = MemoryShard(
            atoms=atoms,
            scope="project:csp",
            origin="agent:main:main",
            tags=["CSP decisions"],
        )
        assert shard.shard_id  # has content address
        assert shard.token_estimate > 0

        # 3. Store
        store = ShardStore(tmp_path / "store")
        ref = store.put(shard)
        assert store.has(ref.shard_id)

        # 4. Hydrate (simulate sub-agent receiving refs)
        ctx = store.hydrate([ref])
        assert "namespace packages" in ctx or "librarian" in ctx
        assert '<shard scope="project:csp"' in ctx

        # 5. Verify stats
        stats = store.stats()
        assert stats["total_shards"] == 1
        assert stats["total_atoms"] == len(atoms)
