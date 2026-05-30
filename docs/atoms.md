# Atoms

Atoms in Spiritwriter are the smallest units of understanding in a dialogue or document. These are more than objects/nouns and actions/verbs or entities; think more like a statement that's a decision, fact, convention, or preference. Memories in spiritwriter can be a collection of atoms, a skill file, context data, or some combination.

Other memory systems use a similar process to "...atomize complex dialogue flows into self-contained factual statements." (SimpleMem 2026, §2.1 Semantic Structured Compression). SimpleMem proves improved context management by reducing redundancy through compression while maintaining semantic accuracy. Zep takes the related-but-distinct angle of building a temporal knowledge graph over the same kind of atomized memory, with entity nodes and time-bounded edges rather than a flat collection. Both motivate spiritwriter's atom-first design, but spiritwriter stays closer to the flat-collection end of the spectrum — graph relationships emerge from shared `entity` strings and `parent_shard_id` lineage, not from an upfront ontology.

Entity Sense Signatures (ESS) are how Spiritwriter achieves fast, reliable, local compression of atoms. Figuring which atoms are talking about the same thing can be achieved by applying disambiguation and coreference resolution (which of these are not like the other and which are "pretty close"). The basis for this kind of signature-based entity resolution was introduced in the 2018 paper by Zhang et al., "Scalable Entity Resolution Using Probabilistic Signatures on Parallel Databases". Spiritwriter uses a simplified approach for this with a local sqlite db.

Named entities frequently collide with common terms: "Bear" the dog, "bear" the animal, "Bear" the brand. This isn't an edge case — it occurs constantly with pet names, product names, place names, and people names. Without explicit sense disambiguation, simple entity alignment will group all "Bear" atoms together, and a naive scoring system would see high entity overlap between facts about completely different references. ESS adds a small number of tokens (~80) per atom at extraction time but eliminates entire categories of false merges that would otherwise require expensive LLM calls or, worse, silently corrupt the canonical registry.

In 2024, another paper related to entity alignment came out and discusses using a knowledge graph to handle the ETL (extract/transform/load) and then canonicalize. "Extract, Define, Canonicalize: An LLM-based Framework for Knowledge Graph Construction" calls this EDC.

Graph architects prefer using a strict ontology for mapping entities and creating associations. This works well in a pre-defined or well-known space, but it falls apart when new content is considered that doesn't align with the existing schema. EDC solves this problem through multi-stage LLM calls to first extract important data, then define a schema based on this data, and finally create the ontology by canonicalizing the terms.

Spiritwriter deviates from these flows by using ESS instead. While the EDC technique may provide higher rates of precision, it comes at the cost of complexity. An atom will have "scope" and this scope provides a cheap way to graph relationships without all the overhead. A full KG can be useful, but when you're dealing with a conversation or a specific document, the back and forth isn't necessary, and keeping your context lean and in-memory is preferable. Conversely, it's easy to teach a delegate about spiritwriter primitives without actually installing anything or having access to a database.

For the measured results — auto-merge precision, false-merge rate, and the falsification battery that backstops the numbers — see [`docs/benchmarks/README.md`](benchmarks/README.md) and the per-corpus results under [`benchmarks/eval/ess_accuracy/`](../benchmarks/eval/ess_accuracy/). The runs log at [`docs/benchmarks/runs-log.md`](benchmarks/runs-log.md) tracks measurements over time.

---

## What's flexible vs not

Two axes. The first is which fields you fill in and which kinds you use; the second is the shape of the primitive itself.

| Axis | Flexible | Not flexible |
|---|---|---|
| **AtomKind enum** | Any of 8 values applies to many domains; choice signals semantic intent | Closed set: `fact, decision, convention, preference, entity, context, checkpoint, instruction`. Adding new kinds means modifying `spiritwriter.fabric.shard` and re-deploying everything that imports it |
| **ShardAtom fields** | Only `text` is required; entity/key/value/confidence/source_ref can all be absent in any combination | Dataclass shape is fixed — adding new fields breaks content-addressing for existing shards |
| **(entity, key, value) triple** | Free-form strings; any namespace scheme; can be partial (just entity, just (entity, key), or full triple) | Once chosen, the triple becomes load-bearing for canonicalization. The `CanonicalRegistry`'s `ess_fields` must reference real triple positions |
| **Atom count per shard** | Unbounded; a shard can have 1 atom or 100 | All atoms in a shard share the same scope and origin (those live on the MemoryShard, not the atom). Different scope = different shard |
| **Kind composition** | Any mix of kinds in one shard — a shard about "Project Foo" can hold facts, decisions, conventions about Foo together | Atoms within a shard can't FK to each other. Relationships happen via shared `entity` strings or at the shard level via `parent_shard_id` |
| **Scope** | Free-form colon-namespaced string (`sw:article:run-abc`, `project:myapp`, `user:aaron:preferences`); any scheme works | Scope is part of `shard_id` — once published you can't re-scope without minting new shards |
| **Domain semantics** | A `FACT` atom can be a measurement, a citation, a learned preference, a config value — same shape, different content | If you want entity resolution, the (entity, key, value) structure has to align with your `CanonicalSchema.ess_fields` |
| **`source_ref`** | Optional; useful for citing where the atom came from (URL, trace event hash, source document atom id) | When present it should be opaque — no schema is enforced on the string format |

**The single most-missed point**: most fields are optional. Documentation overweights the "facts about an entity" shape because that's what the entity-resolution example does — but a perfectly valid atom is `ShardAtom(text="Use FastAPI for new services", kind=CONVENTION)` with no entity, key, or value at all. The examples below cover both extremes plus the middle.

---

## Use cases (worked examples)

Each subsection below corresponds to a runnable example under [`examples/atoms/`](../examples/atoms/). Run any with `python examples/atoms/<file>.py` — they're self-contained, no flags, no LLM. Regression tests live at [`tests/test_atom_examples.py`](../tests/test_atom_examples.py) and verify every example parses, hashes deterministically, and round-trips through `ShardStore`.

### Knowledge fact extraction (`FACT`) — [`01_fact.py`](../examples/atoms/01_fact.py)

The most common shape: atomize a sentence from a paper or article into multiple `FACT` atoms about one entity, full (entity, key, value) triple filled.

```python
ShardAtom(text="The Fugaku supercomputer is in Kobe, Japan.",
          kind=AtomKind.FACT,
          entity="Fugaku", key="location", value="Kobe, Japan",
          source_ref="paper:fugaku-overview-2020#p3")
```

Why the triple matters here: it gives `CanonicalRegistry` something to deduplicate on later. Two atoms with `entity="Fugaku"` + `key="location"` + `value="Kobe, Japan"` will resolve to the same canonical record even if they came from different sources. `source_ref` is the citation trail back to whatever produced the atom.

### Decision with rationale (`DECISION`) — [`02_decision.py`](../examples/atoms/02_decision.py)

Captures why an agent or human chose X. The `text` field carries the rationale prose; (entity, key, value) carries the decision itself in machine-queryable form. Stacking multiple `DECISION` atoms over time tells the evolution story; `parent_shard_id` ties them together.

```python
ShardAtom(text="PostgreSQL chosen over SQLite for concurrent writes.",
          kind=AtomKind.DECISION,
          entity="myproject", key="database", value="postgresql")
```

### User preference (`PREFERENCE`) — [`03_preference.py`](../examples/atoms/03_preference.py)

Structured config shape — pure (entity, key, value) with minimal `text`. Use `PREFERENCE` for user settings that are less binding than `CONVENTION` (an override the user might toggle, not a rule the agent must obey).

```python
ShardAtom(text="dark", kind=AtomKind.PREFERENCE,
          entity="aaron", key="display.theme", value="dark")
```

Scope matters here: `user:aaron:preferences` vs `project:myapp:defaults` lets entitlement tokens grant access at the right granularity.

### Convention / rule (`CONVENTION`) — [`04_convention.py`](../examples/atoms/04_convention.py)

Behavioral rule with no triple at all — `text` is the whole atom. Use for "always X" / "never Y" policies: agent behavior, deploy rules, code conventions.

```python
ShardAtom(text="Always run migrations before deploy.",
          kind=AtomKind.CONVENTION)
```

This is the shape readers most often miss because every other example fills out entity/key/value. They're optional.

### Hydrated context for prompts (`CONTEXT`) — [`05_context.py`](../examples/atoms/05_context.py)

The prompt-engineering shape. Free-form `text` with no triple required. Composes through `ShardStore.hydrate()` into XML-tagged prompt context — the rendering rules live in [`docs/memory-shards.md`](memory-shards.md#hydration).

```python
ShardAtom(text="The user prefers concise, technical responses.",
          kind=AtomKind.CONTEXT)
```

### Pipeline checkpoint (`CHECKPOINT`) — [`06_checkpoint.py`](../examples/atoms/06_checkpoint.py) and [`07_checkpoint_with_trace.py`](../examples/atoms/07_checkpoint_with_trace.py)

Resume-point pattern. The base example shows the atom shape: `entity` names the run, `key/value` encodes which stage, `source_ref` pins to the trace event.

```python
ShardAtom(text="Completed stage 3 of 5 (transcript generated).",
          kind=AtomKind.CHECKPOINT,
          entity="run-abc-123", key="pipeline.stage", value="3",
          source_ref="chain:run-abc-123#a7b3c2...")
```

`07_checkpoint_with_trace.py` closes the loop: an agent emits a real trace event via `TraceEmitter`, captures the chain ref via `emitter.current_trace_ref()`, and pins the `CHECKPOINT` atom's `source_ref` to it. On resume, the reader parses `source_ref` back into a run_id + event_hash, calls `verify_chain()` on the trace JSONL, and continues from the verified point. The pattern: trace events are the audit log; `CHECKPOINT` atoms are the queryable index into them. See [`docs/tracing.md`](tracing.md) for the chain verification details.

### Sub-agent instruction (`INSTRUCTION`) — [`08_instruction.py`](../examples/atoms/08_instruction.py) and [`09_instruction_delegation.py`](../examples/atoms/09_instruction_delegation.py)

Delegation pattern. Base example shows the atom in isolation:

```python
ShardAtom(text="Summarize in 3 paragraphs, no bullet lists.",
          kind=AtomKind.INSTRUCTION,
          entity="job-xyz", key="output.format", value="prose-only")
```

`09_instruction_delegation.py` closes the loop. The whole point of `INSTRUCTION` atoms is that they're the directive half of a delegated job. The example runs end-to-end: orchestrator builds content atoms (`FACT`-shaped knowledge) plus a `JobSpec` (which `to_atoms()` converts to `INSTRUCTION` + `CONVENTION` atoms), calls `package_job()` to mint encrypted content + task shards bound by an `EntitlementToken`, emits `job_packaged` / `job_started` trace events, hands the bundle to a sub-agent that hydrates via the entitlement, then emits `job_completed` with the cost. See [`docs/jobs.md`](jobs.md) for the package/hydrate/settle workflow, [`docs/entitlements.md`](entitlements.md) for the cap-chain, [`docs/tracing.md`](tracing.md) for the chain-of-custody events.

Distinction from `CONVENTION`: a convention is a broader rule scoped to a project or user ("never deploy on Fridays"). An instruction is specific to one delegated job ("summarize this paper in 3 paragraphs"). Same primitive, different audience.

### Canonical entity record (`ENTITY`) — [`10_entity.py`](../examples/atoms/10_entity.py)

The entity-resolution-shaped use case. Full (entity, key, value) — these are the atoms that drive `CanonicalRegistry` ess_fields. See [`docs/entity-resolution.md`](entity-resolution.md) for how the registry consumes them.

```python
ShardAtom(text="Carlos Rodriguez, b. 1985-03-12",
          kind=AtomKind.ENTITY,
          entity="person:carlos-rodriguez-1985",
          key="dob", value="1985-03-12")
```

### Mixed-kind shard (composition) — [`11_mixed_kind.py`](../examples/atoms/11_mixed_kind.py)

A real-world shard usually mixes kinds. The example builds a "Project Foo" shard with `FACT` (framework used), `CONVENTION` (deploy rule), `DECISION` (why this framework), `CONTEXT` (recent changes) — all in one shard, tied together by shared `scope`. Mixing kinds is normal, not exceptional.

### Minimal atom (`text`-only) — [`12_minimal.py`](../examples/atoms/12_minimal.py)

The absolute minimum. Worth documenting explicitly because users often think they need to fill every field.

```python
ShardAtom(text="The thing to remember.")  # kind defaults to CONTEXT
```

A shard with one minimal atom still has a valid `shard_id` and hydrates cleanly. No bookkeeping required.

### Parent atom with child variants (lineage permutations) — [`13_lineage_variants.py`](../examples/atoms/13_lineage_variants.py)

The "same thing, multiple expressions" pattern. One canonical article plus multiple bias-rewritten variants — the zeitghost bias-news shape. Each variant shard's `parent_shard_id` links back to the canonical one; all variants share the same `scope` and `entity` (the article's URL hash), so `CanonicalRegistry` resolves them to a single canonical entity and a downstream bias-slider consumer picks which variant is visible.

```python
# Parent: the canonical article
parent = MemoryShard(
    atoms=[
        ShardAtom(text=headline, kind=AtomKind.FACT,
                  entity=f"article:{url_sha}", key="headline", value=headline),
        ShardAtom(text=body, kind=AtomKind.FACT,
                  entity=f"article:{url_sha}", key="body", value=body),
    ],
    scope="sw:article:zeitghost",
    origin="zeitghost-ingest",
)

# Variant: same scope/entity, different body, parent_shard_id pinning lineage
left = MemoryShard(
    atoms=[
        ShardAtom(text=rewritten_body, kind=AtomKind.FACT,
                  entity=f"article:{url_sha}", key="body", value=rewritten_body),
        ShardAtom(text="left", kind=AtomKind.ENTITY,
                  entity=f"article:{url_sha}", key="bias_variant", value="left"),
    ],
    scope="sw:article:zeitghost",
    origin="zeitghost-bias-rewriter",
    parent_shard_id=parent.shard_id,
)
```

Most existing docs only show "new fact, new atom" — they don't show how to model "same entity, multiple expressions." `parent_shard_id` is the cheap, schema-level way to express "this is a derivation of that": no inter-atom FK needed, the relationship lives at the shard level where it belongs.

---

## Related reading

- [`memory-shards.md`](memory-shards.md) — the `MemoryShard` bundle, the `ShardAtom` dataclass reference, hydration rendering rules.
- [`shard-store.md`](shard-store.md) — `put`/`get`/`hydrate`, named refs, content-addressing on disk.
- [`entity-resolution.md`](entity-resolution.md) — how `CanonicalRegistry` consumes (entity, key, value) triples via ESS.
- [`tracing.md`](tracing.md) — hash-chained events that `CHECKPOINT` atoms pin to.
- [`jobs.md`](jobs.md) — the package/hydrate/settle workflow `INSTRUCTION` atoms participate in.
- [`entitlements.md`](entitlements.md) — cap-chain and scope enforcement.
- [`benchmarks/README.md`](benchmarks/README.md) — measured precision, false-merge rate, falsification battery.
