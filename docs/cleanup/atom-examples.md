# Cleanup: Atom Examples — What's Flexible, What's Not

**Status:** Working doc. Decisions are open unless tagged `LOCKED`.
Action items have per-repo checkboxes — tick as we land PRs.

**Driving todo:** #5 (atom examples for different use cases — what's
flexible vs not).

**Why this exists:** the `ShardAtom` primitive is the smallest unit of
knowledge in spiritwriter and is referenced from many places, but the
docs don't show the breadth of what an atom *can* be. Newcomers see
the dataclass and reach for "facts about an entity" because that's the
worked example everywhere — they miss that the same primitive serves
decisions, preferences, conventions, checkpoints, sub-agent
instructions, and pure context. This spec captures the use cases worth
documenting so the final examples cover a defensible range without
duplication or drift.

---

## 1 · The flexibility frame

Two axes:

| Axis | Flexible | Not flexible |
|---|---|---|
| **AtomKind enum** | Any of 8 values applies to many domains; choice signals semantic intent | Closed set: `fact, decision, convention, preference, entity, context, checkpoint, instruction`. Can't add new kinds without modifying `spiritwriter.fabric.shard` and re-deploying everything that imports it |
| **ShardAtom fields** | Only `text` is required; entity/key/value/confidence/source_ref can all be absent or any combination | Dataclass shape is fixed — can't add new fields without changing the schema (which would break content-addressing for existing shards) |
| **(entity, key, value) triple** | Free-form strings; any namespace scheme; can be partial (just entity, just (entity, key), or full triple) | Once chosen, the triple structure becomes load-bearing for canonicalization. The `CanonicalRegistry`'s ess_fields must reference real triple positions |
| **Atom count per shard** | Unbounded; a shard can have 1 atom or 100 | All atoms in a shard share the same scope and origin (those live on the MemoryShard, not the atom). If you need different scopes, that's different shards |
| **Kind composition** | Any mix of kinds in one shard — a shard about "Project Foo" can contain facts about Foo, decisions about Foo, conventions for Foo, all together | Atoms within a shard can't FK to each other (no inter-atom relationships at the schema level). Relationships happen via shared `entity` strings or at the shard level via `parent_shard_id` |
| **Scope** | Free-form colon-namespaced string (`sw:article:run-abc`, `project:myapp`, `user:aaron:preferences`); any scheme works | Scope affects retrieval — once chosen and shards published, can't migrate without writing new shards. `shard_id` includes scope, so re-scoping = new shard |
| **Domain semantics** | A `FACT` atom can be a measurement, citation, learned preference, config value — same shape, different content | If you want entity resolution, the (entity, key, value) structure has to align with your `CanonicalSchema.ess_fields` |
| **`source_ref`** | Optional; useful for citing where the atom came from (URL, trace event hash, document atom id) | When present, should be opaque — no schema enforced on the string format |

**The single most-missed point**: most fields are optional. The
documentation overweights the "facts about an entity" shape because
that's what the `entity-resolution.md` example does — but a perfectly
valid atom can be `ShardAtom(text="Use FastAPI for new services",
kind=CONVENTION)` with no entity / key / value at all. The examples in
this spec should cover both extremes plus the middle.

---

## 2 · Use cases to cover

Each use case below becomes a worked example. The goal is **range, not
depth** — every example should be short enough to read inline in
`docs/atoms.md` (15–25 lines of Python max), and together they should
span the AtomKind enum without redundancy.

Priority order (build from most-used downward):

### 2.1 — Knowledge fact extraction (FACT) `LOCKED priority 1`

Atomize a sentence from a paper / article into multiple atoms. Shows:
- Same shard, multiple FACT atoms about one entity
- (entity, key, value) triple fully filled
- Why the triple matters: enables `CanonicalRegistry` deduplication later
- Optional `source_ref` pointing at the source document/atom

Example shape:
```python
ShardAtom(text="The Fugaku supercomputer is in Kobe, Japan.",
          kind=AtomKind.FACT,
          entity="Fugaku", key="location", value="Kobe, Japan")
```

### 2.2 — Decision with rationale (DECISION) `LOCKED priority 1`

Capturing why an agent or human chose X. Shows:
- DECISION kind, single atom per choice
- `text` carries the rationale prose; (entity, key, value) carries the
  decision itself in machine-queryable form
- Multiple DECISIONs can stack as the entity evolves; lineage via
  `parent_shard_id`

Example shape:
```python
ShardAtom(text="PostgreSQL chosen over SQLite for concurrent writes.",
          kind=AtomKind.DECISION,
          entity="myproject", key="database", value="postgresql")
```

### 2.3 — User preference (PREFERENCE) `LOCKED priority 1`

Structured-config shape. Shows:
- PREFERENCE kind for user settings
- Pure (entity, key, value) — minimal `text` (or repeat the value)
- Why scope matters: `user:aaron:preferences` vs `project:myapp:defaults`

Example shape:
```python
ShardAtom(text="dark", kind=AtomKind.PREFERENCE,
          entity="aaron", key="display.theme", value="dark")
```

### 2.4 — Convention / rule (CONVENTION) `LOCKED priority 1`

Behavioral rule with no entity/key/value at all. Shows:
- CONVENTION kind for "always X" / "never Y" rules
- `text` is the whole atom — no need for the triple structure
- Use case: agent behavior policies, deploy rules, code conventions

Example shape:
```python
ShardAtom(text="Always run migrations before deploy.",
          kind=AtomKind.CONVENTION)
```

### 2.5 — Hydrated context for prompts (CONTEXT) `LOCKED priority 1`

The prompt-engineering use case. Shows:
- CONTEXT kind for "here's what you need to know"
- Often free-form `text` only; no need for entity/key/value
- How it composes through `ShardStore.hydrate()` into XML-tagged
  prompt context

Example shape:
```python
ShardAtom(text="The user prefers concise, technical responses.",
          kind=AtomKind.CONTEXT)
```

### 2.6 — Pipeline checkpoint (CHECKPOINT) `LOCKED priority 2`

Resume-point pattern. Shows:
- CHECKPOINT kind for "agent reached step N"
- Often paired with `source_ref` pointing at the trace event
- How the (entity, key, value) triple encodes which pipeline,
  which step

Example shape:
```python
ShardAtom(text="Completed stage 3 of 5 (transcript generated).",
          kind=AtomKind.CHECKPOINT,
          entity="run-abc-123", key="pipeline.stage", value="3",
          source_ref="trace:abc#42")
```

### 2.7 — Sub-agent instruction (INSTRUCTION) `LOCKED priority 2`

Delegation pattern. Shows:
- INSTRUCTION kind for "do X" / "constraint: Y"
- How instructions package into a job's task_shard alongside content
- Difference from CONVENTION (broader rule) vs INSTRUCTION (specific
  to this delegation)

Example shape:
```python
ShardAtom(text="Summarize in 3 paragraphs, no bullet lists.",
          kind=AtomKind.INSTRUCTION,
          entity="job-xyz", key="output.format", value="prose-only")
```

### 2.8 — Canonical entity record (ENTITY) `LOCKED priority 2`

The entity-resolution-shaped use case. Shows:
- ENTITY kind for "this is the canonical representation of X"
- Full (entity, key, value) — drives `CanonicalRegistry` ess_fields
- Cross-references `docs/entity-resolution.md`

Example shape:
```python
ShardAtom(text="Carlos Rodriguez, b. 1985-03-12",
          kind=AtomKind.ENTITY,
          entity="person:carlos-rodriguez-1985",
          key="dob", value="1985-03-12")
```

### 2.9 — Mixed-kind shard (composition) `LOCKED priority 2`

Real-world shard with multiple kinds in one place. Shows:
- A "Project Foo" shard with: FACT (framework used), CONVENTION (deploy
  rule), DECISION (why this framework), CONTEXT (recent changes)
- How `scope` ties them together for retrieval
- That mixing kinds in a shard is normal, not exceptional

Example shape: a single shard with 4–5 atoms covering the above kinds.

### 2.10 — Minimal atom (`text`-only) `LOCKED priority 3`

Shows the absolute minimum. Just `ShardAtom(text="...")`. Worth
documenting explicitly because users often think they need to fill
every field.

---

### `OPEN` — Use cases under consideration but not yet locked

- **Memory shard from a chat transcript** — full conversation atomized
  into multiple FACT / DECISION / PREFERENCE atoms in one shard.
  Probably belongs in the phalanx-flow worked example (todo #7), not
  here.
- **Encrypted shard variant** — same atoms but the parent MemoryShard
  is sealed. May belong in `docs/encryption.md` examples, not the atoms
  doc. Decision: defer.
- **Atom with high-confidence vs low-confidence** — the `confidence`
  field; defaults to 1.0, omitted from serialization at default.
  Could be a small extension to use case 2.6 or 2.7 rather than its
  own example.

---

## 3 · Per-repo action items

### `spiritwriter-core`

- [ ] **Finalize `docs/atoms.md`** — your plane-notes draft is on the
  `cleanup/cmc-canonicalize` branch. Move it forward: section per use
  case from §2, the flexibility-frame table from §1, intro prose, link
  to deep-dive docs.
- [ ] **Add runnable examples under `examples/atoms/`** — short Python
  modules (one per use case) that build the atom, store it in a temp
  shard, and print the hydrated context. Each in <40 lines.
- [ ] **Tests at `tests/test_atom_examples.py`** — verify each example
  parses, hashes, and round-trips through the store. Regression
  coverage so the docs don't drift from the runtime.
- [ ] **Cross-link from `docs/memory-shards.md`** — that doc currently
  doesn't link to the atoms explainer. Should.
- [ ] **Cross-link from `docs/entity-resolution.md`** — points readers
  who land at the resolver to the atom doc for the underlying primitive.

### `frio`

- [ ] (Low priority) Update frio's `README.md` if it has any "what's an
  atom?" prose — likely just point at spiritwriter-core's atoms doc
  rather than reproducing.

### `claude-studio-producer`

- [ ] (Low priority) Same as frio — link, don't duplicate.

### `zeitghost`

- [ ] No action. Atoms aren't part of the marketing surface (they're
  one layer below "Memory Shards" which is on the homepage).

---

## 4 · Downstream effects on other cleanup work

| Touches | How |
|---|---|
| **todo #7 (phalanx-flow worked example)** | The "memory shard from a chat transcript" use case probably belongs there, not in the atoms doc. Atoms doc shows the primitive; phalanx-flow shows the pipeline that uses it. |
| **todo #8 (examples cleanup — `extract_memory.py` rewrite)** | The rewrite will produce atoms; can use the §2 examples as templates for what good output looks like. |
| **todo #9 (workshop docs)** | Workshop's "what does an agent remember" angle should cite the atoms doc rather than re-explain the primitive. |
| **todo #10 (references check)** | Final pass should confirm no doc duplicates the atoms explainer; all point at it. |

---

## 5 · Cross-references to sibling cleanup docs

- [`docs/cleanup/cmc-phalanx-canonicalize.md`](cmc-phalanx-canonicalize.md)
  — the naming cleanup that motivated this whole working-doc pattern.
- (planned) `docs/cleanup/openclaw-lilit-vec0.md` — todo #6.
- (planned) `docs/cleanup/examples.md` — todo #8.

---

## 6 · Decision log

| Date | Decision | Notes |
|---|---|---|
| 2026-05-30 | Stand up this spec before writing examples | The plane-notes `[examples of atoms]` placeholder gave no scoping; without explicit use-case selection the examples would likely overweight FACT-shaped cases (because that's what existing docs lead with). |
| 2026-05-30 | 10 use cases scoped: 5 priority-1, 4 priority-2, 1 priority-3 | Range over depth — each example short enough to read inline. Heavier walkthroughs belong in the phalanx-flow worked example (todo #7). |
| 2026-05-30 | Examples live in TWO places: inline prose in `docs/atoms.md` and runnable Python under `examples/atoms/` | Inline for readability, runnable for regression coverage. Tests verify the runnable ones don't drift from the prose ones. |
| 2026-05-30 | Encrypted-shard, chat-transcript, and confidence-field cases deferred | Each belongs in another doc (encryption.md, phalanx-flow walkthrough, or as a minor extension to existing examples). |

---

## 7 · Open questions for Aaron

1. **Priorities right?** 5 priority-1 (FACT, DECISION, PREFERENCE, CONVENTION, CONTEXT — the load-bearing kinds for most use cases), 4 priority-2 (CHECKPOINT, INSTRUCTION, ENTITY, composition), 1 priority-3 (minimal). Want to re-shuffle or cut anything?
2. **Examples directory layout** — `examples/atoms/` with one file per use case (`01_fact.py`, `02_decision.py`, etc.) or all in one `atom_examples.py`? I'd lean per-file for navigation; you may have a preference.
3. **`docs/atoms.md` ownership** — that draft is currently on `cleanup/cmc-canonicalize`. Move it to a new `claude/atom-examples` branch with this spec, or fold both into a single PR eventually?
4. **Any use cases missing from my list?** Things you've encountered in frio / csp / spiritwriter usage that exercised the atoms primitive in a way I haven't captured.
