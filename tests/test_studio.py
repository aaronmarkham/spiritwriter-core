"""Tests for studio job packaging (Phase 4) and studio runner (Phase 5)."""

import json
import tempfile
from pathlib import Path

import pytest

from spiritwriter.trace.shard import MemoryShard, ShardAtom, AtomKind, DecayClass
from spiritwriter.trace.store import ShardStore
from spiritwriter.trace.crypto import generate_job_key, encrypt_shard, serialize_key
from spiritwriter.trace.entitlement import (
    Capability, create_entitlement, serialize_token, deserialize_token,
)
from spiritwriter.trace.studio_job import (
    StudioJobSpec, PackagedJob, package_job,
)
from spiritwriter.trace.studio_runner import (
    StudioRunnerError, JobContext, BudgetTracker,
    parse_job_block, hydrate_job, create_result_shard,
)


@pytest.fixture
def tmp_store(tmp_path):
    return ShardStore(tmp_path)


@pytest.fixture
def sample_atoms():
    return [
        ShardAtom(text="Ingrown hairs occur after waxing", kind=AtomKind.FACT,
                  entity="ingrown_hair", key="cause", value="waxing"),
        ShardAtom(text="Use salicylic acid to prevent", kind=AtomKind.INSTRUCTION,
                  key="prevention"),
        ShardAtom(text="Fur Ingrown Concentrate rated 4.8/5", kind=AtomKind.FACT,
                  entity="fur_concentrate", key="rating", value="4.8/5"),
    ]


@pytest.fixture
def sample_spec():
    return StudioJobSpec(
        prompt="Create a 60-second explainer about Brazilian wax aftercare",
        style="explainer",
        budget_usd=15.0,
        duration_seconds=60,
        voice="nova",
        upload_target="youtube:unlisted",
        constraints={"max_scenes": "8", "tier": "standard"},
    )


# === Phase 4: Job Packaging ===

class TestStudioJobSpec:
    def test_to_atoms_basic(self, sample_spec):
        atoms = sample_spec.to_atoms()
        assert len(atoms) >= 3  # prompt + config + budget
        prompts = [a for a in atoms if a.key == "production_prompt"]
        assert len(prompts) == 1
        assert "Brazilian wax" in prompts[0].text

    def test_to_atoms_with_upload(self, sample_spec):
        atoms = sample_spec.to_atoms()
        uploads = [a for a in atoms if a.key == "upload_target"]
        assert len(uploads) == 1
        assert "youtube:unlisted" in uploads[0].text

    def test_to_atoms_constraints(self, sample_spec):
        atoms = sample_spec.to_atoms()
        constraint_atoms = [a for a in atoms if a.key and a.key.startswith("constraint.")]
        assert len(constraint_atoms) == 2

    def test_no_upload_target(self):
        spec = StudioJobSpec(prompt="test", budget_usd=5.0)
        atoms = spec.to_atoms()
        uploads = [a for a in atoms if a.key == "upload_target"]
        assert len(uploads) == 0


class TestPackageJob:
    def test_package_roundtrip(self, tmp_store, sample_atoms, sample_spec):
        pkg = package_job(
            store=tmp_store,
            content_atoms=sample_atoms,
            job_spec=sample_spec,
        )
        assert pkg.content_shard_id
        assert pkg.task_shard_id
        assert pkg.entitlement_token is not None
        assert len(pkg.job_key) == 32

    def test_encrypted_shards_stored(self, tmp_store, sample_atoms, sample_spec):
        pkg = package_job(tmp_store, sample_atoms, sample_spec)
        assert tmp_store.has_encrypted(pkg.content_shard_id)
        assert tmp_store.has_encrypted(pkg.task_shard_id)

    def test_entitlement_has_both_keys(self, tmp_store, sample_atoms, sample_spec):
        pkg = package_job(tmp_store, sample_atoms, sample_spec)
        token = pkg.entitlement_token
        assert pkg.content_shard_id in token.shard_keys
        assert pkg.task_shard_id in token.shard_keys

    def test_entitlement_capabilities(self, tmp_store, sample_atoms, sample_spec):
        pkg = package_job(tmp_store, sample_atoms, sample_spec)
        token = pkg.entitlement_token
        assert Capability.SHARD_READ in token.capabilities
        assert Capability.KB_PRODUCE in token.capabilities

    def test_entitlement_budget(self, tmp_store, sample_atoms, sample_spec):
        pkg = package_job(tmp_store, sample_atoms, sample_spec)
        assert pkg.entitlement_token.budget_usd == 15.0

    def test_spawn_task_text(self, tmp_store, sample_atoms, sample_spec):
        pkg = package_job(tmp_store, sample_atoms, sample_spec)
        text = pkg.spawn_task_text()
        assert "<studio-job>" in text
        assert "<entitlement>" in text
        assert pkg.content_shard_id in text
        assert pkg.task_shard_id in text
        assert "studio runner" in text.lower()

    def test_custom_capabilities(self, tmp_store, sample_atoms, sample_spec):
        pkg = package_job(
            tmp_store, sample_atoms, sample_spec,
            capabilities=[Capability.SHARD_READ],
            secrets=["LUMA_API_KEY"],
        )
        assert pkg.entitlement_token.capabilities == [Capability.SHARD_READ]
        assert pkg.entitlement_token.secrets == ["LUMA_API_KEY"]


# === Phase 5: Studio Runner ===

class TestParseJobBlock:
    def test_parse_valid(self):
        text = (
            "<studio-job>\n"
            "<entitlement>{\"token_id\": \"abc\"}</entitlement>\n"
            "<content-shard>sha256_content</content-shard>\n"
            "<task-shard>sha256_task</task-shard>\n"
            "</studio-job>"
        )
        token_str, content_id, task_id = parse_job_block(text)
        assert "abc" in token_str
        assert content_id == "sha256_content"
        assert task_id == "sha256_task"

    def test_parse_missing_fields(self):
        with pytest.raises(StudioRunnerError, match="Missing required"):
            parse_job_block("no job block here")


class TestHydrateJob:
    def test_full_roundtrip(self, tmp_store, sample_atoms, sample_spec):
        """Package → spawn text → parse → hydrate → get content back."""
        pkg = package_job(tmp_store, sample_atoms, sample_spec)
        task_text = pkg.spawn_task_text()

        job = hydrate_job(tmp_store, task_text)

        assert job.prompt is not None
        assert "Brazilian wax" in job.prompt
        assert len(job.content_shard.atoms) == 3
        assert job.budget_usd == 15.0

    def test_config_extraction(self, tmp_store, sample_atoms, sample_spec):
        pkg = package_job(tmp_store, sample_atoms, sample_spec)
        job = hydrate_job(tmp_store, pkg.spawn_task_text())
        config = job.config
        assert "production_prompt" in config
        assert "budget_limit" in config

    def test_content_text(self, tmp_store, sample_atoms, sample_spec):
        pkg = package_job(tmp_store, sample_atoms, sample_spec)
        job = hydrate_job(tmp_store, pkg.spawn_task_text())
        text = job.content_text
        assert "ingrown" in text.lower() or "salicylic" in text.lower()

    def test_wrong_store_fails(self, tmp_store, sample_atoms, sample_spec):
        """Hydrating from a different store should fail."""
        pkg = package_job(tmp_store, sample_atoms, sample_spec)
        task_text = pkg.spawn_task_text()

        empty_store = ShardStore(tmp_store.root / "empty")
        with pytest.raises(StudioRunnerError, match="not found"):
            hydrate_job(empty_store, task_text)


class TestBudgetTracker:
    def test_basic_tracking(self):
        bt = BudgetTracker(budget_usd=10.0)
        bt.record("luma_gen", 4.32)
        bt.record("tts", 0.02)
        assert bt.spent == pytest.approx(4.34)
        assert bt.remaining == pytest.approx(5.66)

    def test_over_budget(self):
        bt = BudgetTracker(budget_usd=5.0)
        bt.record("luma_gen", 4.32)
        with pytest.raises(StudioRunnerError, match="Budget exceeded"):
            bt.record("more_luma", 4.32)

    def test_summary(self):
        bt = BudgetTracker(budget_usd=10.0)
        bt.record("test", 1.50)
        s = bt.summary()
        assert s["budget_usd"] == 10.0
        assert s["spent_usd"] == 1.5
        assert len(s["entries"]) == 1


class TestCreateResultShard:
    def test_basic_result(self, tmp_store, sample_atoms, sample_spec):
        pkg = package_job(tmp_store, sample_atoms, sample_spec)
        job = hydrate_job(tmp_store, pkg.spawn_task_text())

        result = create_result_shard(job, {
            "budget": {"spent_usd": 4.34, "budget_usd": 15.0},
            "outputs": [
                {"type": "video", "ref": "/path/to/video.mp4"},
                {"type": "youtube", "ref": "https://youtu.be/abc123"},
            ],
            "warnings": ["QA verifier skipped due to known bug"],
        })

        assert result.scope == "studio:result"
        assert len(result.atoms) >= 4  # summary + cost + 2 outputs + 1 warning
        assert result.meta["content_shard_id"] == pkg.content_shard_id


# === Phase 3: Entitlement-Aware Hydration on Store ===

class TestEntitlementHydration:
    def test_hydrate_encrypted_shards(self, tmp_store):
        """Store encrypted shards, hydrate via entitlement token."""
        key = generate_job_key()
        shard = MemoryShard(
            atoms=[ShardAtom(text="secret research", kind=AtomKind.FACT)],
            scope="project:secret",
            origin="test",
        )
        enc = tmp_store.encrypt_and_store(shard, key)

        token = create_entitlement(
            granted_to="runner",
            granted_by="test",
            shard_keys={shard.shard_id: key},
            scopes=["project:*"],
            capabilities=[Capability.SHARD_READ],
            secrets=[],
            budget_usd=10.0,
        )

        result = tmp_store.hydrate_with_entitlement(token)
        assert "secret research" in result

    def test_wrong_scope_rejected(self, tmp_store):
        key = generate_job_key()
        shard = MemoryShard(
            atoms=[ShardAtom(text="secret", kind=AtomKind.FACT)],
            scope="project:secret",
            origin="test",
        )
        tmp_store.encrypt_and_store(shard, key)

        token = create_entitlement(
            granted_to="runner",
            granted_by="test",
            shard_keys={shard.shard_id: key},
            scopes=["other:*"],  # Wrong scope
            capabilities=[Capability.SHARD_READ],
            secrets=[],
            budget_usd=10.0,
        )

        with pytest.raises(PermissionError, match="not entitled"):
            tmp_store.hydrate_with_entitlement(token)

    def test_expired_token_rejected(self, tmp_store):
        key = generate_job_key()
        shard = MemoryShard(
            atoms=[ShardAtom(text="secret", kind=AtomKind.FACT)],
            scope="project:x",
            origin="test",
        )
        tmp_store.encrypt_and_store(shard, key)

        token = create_entitlement(
            granted_to="runner",
            granted_by="test",
            shard_keys={shard.shard_id: key},
            scopes=["project:*"],
            capabilities=[Capability.SHARD_READ],
            secrets=[],
            budget_usd=10.0,
            expires_at="2020-01-01T00:00:00Z",
        )

        with pytest.raises(PermissionError, match="expired"):
            tmp_store.hydrate_with_entitlement(token)

    def test_missing_capability_rejected(self, tmp_store):
        key = generate_job_key()
        shard = MemoryShard(
            atoms=[ShardAtom(text="secret", kind=AtomKind.FACT)],
            scope="project:x",
            origin="test",
        )
        tmp_store.encrypt_and_store(shard, key)

        token = create_entitlement(
            granted_to="runner",
            granted_by="test",
            shard_keys={shard.shard_id: key},
            scopes=["project:*"],
            capabilities=[Capability.SHARD_WRITE],  # No READ
            secrets=[],
            budget_usd=10.0,
        )

        with pytest.raises(PermissionError, match="shard:read"):
            tmp_store.hydrate_with_entitlement(token)

    def test_hydrate_plaintext_fallback(self, tmp_store):
        """Entitlement can hydrate unencrypted shards too."""
        shard = MemoryShard(
            atoms=[ShardAtom(text="public info", kind=AtomKind.FACT)],
            scope="project:public",
            origin="test",
        )
        tmp_store.put(shard)
        key = generate_job_key()  # Dummy key (won't be used for decryption)

        token = create_entitlement(
            granted_to="runner",
            granted_by="test",
            shard_keys={shard.shard_id: serialize_key(key).encode()},
            scopes=["project:*"],
            capabilities=[Capability.SHARD_READ],
            secrets=[],
            budget_usd=10.0,
        )

        result = tmp_store.hydrate_with_entitlement(token)
        assert "public info" in result
