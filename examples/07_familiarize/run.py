#!/usr/bin/env python3
"""Demo 7: Familiarize — docs folder → CMC-aligned, agent-ready brief.

The pain this answers: every session you tell the agent to "get familiar
with my app", then watch it fumble through your docs re-deriving the same
facts. This demo does that derivation ONCE, with an LLM, then ALIGNS the
result along the CMC spec (docs/specs/cmc-spec-v0.1.md) so semantic
duplicates collapse — and stores it as a content-addressed shard the agent
hydrates on startup.

  ./sample_app/*.md
        │  extract_atoms_llm()   atoms enriched with key_definition + sense
        ▼
  ShardAtom[]
        │  align_atoms()         tiered: exact → sense-gate → LLM adjudicate
        ▼
  MemoryShard ──set_ref──▶ "project-familiarity"
        │  hydrate_context()
        ▼
  <shard>…</shard>               injected at agent startup

Alignment is NOT string-fuzzy (that gets 20-30% recall, CMC §1). It uses
the LLM to judge the hard pairs:
  - 'Postgres' and 'PostgreSQL' are recognized as the SAME entity and
    unified — the case bare string-matching missed.
  - 'database' and 'datastore' (different keys, same meaning) merge by
    DEFINITION, not spelling.
  - the sense gate keeps genuine homonyms apart before any LLM call.

Determinism / cost: a MockLLMProvider answers both the extraction AND the
adjudication prompts, so this runs offline, free, repeatable. The
extraction + alignment code is REAL; only the model responses are canned.
For a live run, swap one line:

    provider = AnthropicProvider()          # needs ANTHROPIC_API_KEY

Usage:
    python examples/07_familiarize/run.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from spiritwriter.fabric.familiarize import familiarize
from spiritwriter.fabric.store import ShardStore
from spiritwriter.llm import MockLLMProvider


FAMILIARITY_REF = "project-familiarity"
SAMPLE_APP = Path(__file__).parent / "sample_app"


# ── The canned model output ─────────────────────────────────────────


def _atoms(*objs: dict) -> str:
    return json.dumps({"atoms": list(objs)})


_TASKFLOW_SENSE = {"sense_type": "organization", "scoped_to": None,
                   "domain": "software_project"}
_PG_SENSE = {"sense_type": "organization", "scoped_to": None, "domain": "datastore"}


_EXTRACTION = {
    # README.md
    "TaskFlow is a small task-management API": _atoms(
        {"kind": "fact", "entity": "TaskFlow", "key": "database", "value": "PostgreSQL",
         "key_definition": "The primary persistent datastore the service reads and writes.",
         "text": "TaskFlow's primary database is PostgreSQL.", "confidence": 0.95,
         "sense": _TASKFLOW_SENSE},
        {"kind": "fact", "entity": "TaskFlow", "key": "api_framework", "value": "FastAPI",
         "key_definition": "The web framework the HTTP API is built on.",
         "text": "TaskFlow's API is built on FastAPI.", "confidence": 0.95,
         "sense": _TASKFLOW_SENSE},
        {"kind": "fact", "entity": "TaskFlow", "key": "deployment_target", "value": "AWS ECS",
         "key_definition": "Where the service is deployed to run.",
         "text": "TaskFlow deploys to AWS ECS.", "confidence": 0.9, "sense": _TASKFLOW_SENSE},
        {"kind": "decision", "entity": "TaskFlow", "key": "auth_method", "value": "JWT",
         "key_definition": "The mechanism used to authenticate API requests.",
         "text": "JWT was chosen so the API stays stateless.", "confidence": 0.9,
         "sense": _TASKFLOW_SENSE},
        {"kind": "decision", "entity": "TaskFlow", "key": "migrations_tool", "value": "Alembic",
         "key_definition": "The tool used to version and apply schema migrations.",
         "text": "Alembic was chosen to keep migrations reviewable in git.", "confidence": 0.85,
         "sense": _TASKFLOW_SENSE},
        {"kind": "fact", "entity": "PostgreSQL", "key": "version", "value": "15",
         "key_definition": "The major version of the datastore in use.",
         "text": "The project targets PostgreSQL 15.", "confidence": 0.8, "sense": _PG_SENSE},
    ),
    # CONVENTIONS.md
    "rules the team holds itself to": _atoms(
        {"kind": "convention", "entity": "TaskFlow", "key": "migration_policy",
         "value": "migrate before deploy",
         "key_definition": "When schema migrations must run relative to deploys.",
         "text": "Always run migrations before deploying.", "confidence": 0.95,
         "sense": _TASKFLOW_SENSE},
        {"kind": "convention", "entity": "TaskFlow", "key": "secrets_policy",
         "value": "never commit secrets",
         "key_definition": "Rule governing where secrets may live.",
         "text": "Never commit secrets to the repository.", "confidence": 0.95,
         "sense": _TASKFLOW_SENSE},
        {"kind": "convention", "entity": "TaskFlow", "key": "test_policy",
         "value": "test every endpoint",
         "key_definition": "Testing requirement for new endpoints.",
         "text": "Always write a test for every new endpoint.", "confidence": 0.9,
         "sense": _TASKFLOW_SENSE},
        {"kind": "convention", "entity": "TaskFlow", "key": "db_access_policy",
         "value": "no direct db in handlers",
         "key_definition": "Rule about where database access may occur.",
         "text": "Never call the database directly from a route handler.", "confidence": 0.9,
         "sense": _TASKFLOW_SENSE},
    ),
    # BACKLOG.md
    "rate limiter because the public API is getting abused": _atoms(
        {"kind": "decision", "entity": "TaskFlow", "key": "rate_limiting", "value": "enabled",
         "key_definition": "Whether request rate limiting is applied to the public API.",
         "text": "A rate limiter was added because the public API was being abused.",
         "confidence": 0.85, "sense": _TASKFLOW_SENSE},
        {"kind": "convention", "entity": "TaskFlow", "key": "webhook_policy",
         "value": "verify signatures",
         "key_definition": "Rule for handling inbound webhooks.",
         "text": "Always validate webhook signatures before processing.", "confidence": 0.9,
         "sense": _TASKFLOW_SENSE},
        {"kind": "instruction", "entity": "TaskFlow", "key": "oauth_login",
         "key_definition": "Planned work to add third-party login.",
         "text": "Add Google OAuth login (tracked in TICKET-412).", "confidence": 0.9,
         "sense": _TASKFLOW_SENSE},
        {"kind": "instruction", "entity": "TaskFlow", "key": "worker_queue_migration",
         "key_definition": "Planned work to change the background job queue.",
         "text": "Migrate the worker queue off Redis (tracked in TICKET-419).", "confidence": 0.85,
         "sense": _TASKFLOW_SENSE},
        # Restates README's database fact verbatim — EXACT merge, cite both docs.
        {"kind": "fact", "entity": "TaskFlow", "key": "database", "value": "PostgreSQL",
         "key_definition": "The primary persistent datastore the service reads and writes.",
         "text": "TaskFlow's primary database is PostgreSQL.", "confidence": 0.9,
         "sense": _TASKFLOW_SENSE},
        # Same fact under a DIFFERENT key ('datastore') — granularity merge by definition.
        {"kind": "fact", "entity": "TaskFlow", "key": "datastore", "value": "PostgreSQL",
         "key_definition": "The persistent data store backing the service.",
         "text": "The service's data store is PostgreSQL.", "confidence": 0.8,
         "sense": _TASKFLOW_SENSE},
        # Near-variant ENTITY ('Postgres' vs 'PostgreSQL') — LLM unifies the entity.
        {"kind": "fact", "entity": "Postgres", "key": "extension", "value": "pgvector",
         "key_definition": "A datastore extension the project relies on.",
         "text": "Postgres uses the pgvector extension.", "confidence": 0.75, "sense": _PG_SENSE},
    ),
}


def respond(prompt: str) -> str:
    """Route a prompt to the right canned reply (extraction vs adjudication)."""
    if "Two entity mentions" in prompt:
        # Postgres ⇄ PostgreSQL are the same entity; anything else is not.
        if "postgres" in prompt.lower():
            return json.dumps({"verdict": "SAME", "confidence": 0.95})
        return json.dumps({"verdict": "DIFFERENT", "confidence": 0.6})
    if "Two knowledge atoms about the entity" in prompt:
        # The 'database' / 'datastore' pair is the same fact; otherwise distinct.
        # Match the key= fields precisely (the 'database' definition itself
        # contains the word "datastore", so loose matching would over-merge).
        if 'key="database"' in prompt and 'key="datastore"' in prompt:
            return json.dumps({"verdict": "SAME", "more_specific": None, "confidence": 0.9})
        return json.dumps({"verdict": "DIFFERENT", "more_specific": None, "confidence": 0.7})
    for needle, payload in _EXTRACTION.items():
        if needle in prompt:
            return payload
    return '{"atoms": []}'


async def run(output_dir: Path) -> int:
    store = ShardStore(output_dir / "shards")
    provider = MockLLMProvider(respond)   # swap for AnthropicProvider() to run live

    print("=== Demo 7: Familiarize — docs folder → CMC-aligned brief ===\n")
    docs = sorted(SAMPLE_APP.glob("*.md"))
    print(f"Source docs: {len(docs)} ({', '.join(p.name for p in docs)})")
    print(f"Provider:    {type(provider).__name__} (offline; swap in AnthropicProvider for live)\n")

    result = await familiarize(
        SAMPLE_APP, provider, store, FAMILIARITY_REF,
        scope="app:taskflow", tags=["TaskFlow familiarity brief"],
    )
    align = result.alignment

    # ── Extract + align report ─────────────────────────────────────
    print("── Extract + align (tiered) ───────────────────────────────")
    print(f"Atoms extracted (raw):  {result.raw_atom_count}")
    print(f"Atoms after alignment:  {align.output_count}")
    print(f"  · exact duplicates merged (deterministic): {align.exact_merges}")
    print(f"  · entities unified by LLM adjudication:    {align.entities_merged_by_llm}")
    print(f"  · facts merged by definition (LLM):        {align.facts_merged_by_llm}")
    print(f"  · LLM adjudication calls (hard pairs only): {align.llm_calls}")
    print()

    # ── What alignment actually resolved ───────────────────────────
    print("── What alignment resolved ────────────────────────────────")
    entities = {a.entity for a in result.shard.atoms if a.entity}
    print(f"'Postgres' + 'PostgreSQL' → unified entity present as: "
          f"{'PostgreSQL' if 'PostgreSQL' in entities else sorted(entities)}")
    print("  (the case bare string-matching could not merge)")
    db = next(a for a in result.shard.atoms if a.entity == "TaskFlow" and a.key == "database")
    print(f"'database' fact cites every doc that stated it: {db.source_ref}")
    has_datastore = any(a.key == "datastore" for a in result.shard.atoms)
    print(f"'datastore' fact folded into 'database' by definition: {not has_datastore}")
    print()

    # ── What the agent hydrates on startup ─────────────────────────
    print("── Agent startup: one ref instead of N files ──────────────")
    print(f"    store.resolve_ref({FAMILIARITY_REF!r}).hydrate_context()\n")
    brief = store.resolve_ref(FAMILIARITY_REF)
    print(brief.hydrate_context())
    print()

    # ── Verification ───────────────────────────────────────────────
    assert align.exact_merges >= 1, "the verbatim database restatement should merge"
    assert "doc:README.md" in db.source_ref and "doc:BACKLOG.md" in db.source_ref, \
        "merged atom should cite both source docs"
    assert align.entities_merged_by_llm >= 1, "Postgres/PostgreSQL should be unified by the LLM"
    assert "Postgres" not in entities and "PostgreSQL" in entities, \
        "the near-variant entity should be canonicalized away"
    assert align.facts_merged_by_llm >= 1, "database/datastore should merge by definition"
    assert not has_datastore, "the 'datastore' key should fold into 'database'"
    assert any(a.key == "oauth_login" for a in result.shard.atoms), \
        "LLM extraction should capture the prose OAuth task"
    assert any(a.sense is not None for a in result.shard.atoms), \
        "atoms should carry entity sense tags"
    assert any(a.key_definition for a in result.shard.atoms), \
        "atoms should carry key definitions"
    print("=== Demo complete ===")
    print(f"Brief is one ref away: {FAMILIARITY_REF!r} in {store.root}")
    return 0


def main(output_dir: Path | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="demo07_"))
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    return asyncio.run(run(output_dir))


if __name__ == "__main__":
    sys.exit(main())
