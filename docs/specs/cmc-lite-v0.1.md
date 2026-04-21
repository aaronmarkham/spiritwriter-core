# CMC-Lite: Pragmatic Memory Extraction with Consensus

## Lightweight Implementation of CMC Spec Insights

**Version**: 0.1
**Date**: 2026-02-19
**Status**: Implementation-ready
**Parent spec**: `specs/cmc-spec-v0.1.md` (full CMC pipeline — deferred)

---

## 1. What This Is

Take the three best ideas from the full CMC spec and apply them to our existing tooling:

1. **Structured slot extraction** (from EDC's Define step)
2. **Entity Sense Signatures** (from the Bear Problem analysis)
3. **Multi-pass consensus** (from our shingle pattern)

No new infrastructure. SQLite + vec0 + GPT-4.1-mini. Everything lives in spiritwriter-core's `trace/` module.

---

## 2. What Changes From Current System

### Current (broken)
```
memory file → GPT-4.1-mini → flat JSON atoms → fuzzy key matching → store
```

Problems:
- Atoms are flat key/value with no structure
- Consensus matching uses Jaccard on key tokens (9-36% match rate)
- No entity disambiguation
- No coreference resolution
- Truncation on large files

### Proposed
```
memory file → chunk → GPT-4.1-mini (upgraded prompt) → SlottedAtom → vec0 blocking → consensus → store
```

Key differences:
- Atoms have **typed slots** with definitions (enables definition-based matching)
- Entities get **sense tags** (prevents Bear Problem)
- Consensus uses **embedding similarity** on slot definitions (not Jaccard on strings)
- Chunking prevents truncation
- One file at a time (no SIGKILL)

---

## 3. Data Model

### 3.1 SlottedAtom (replaces current ShardAtom for extraction)

```python
@dataclass
class SlottedAtom:
    """An extracted atom with typed slots and entity sense."""
    text: str                       # Clean, coreference-resolved surface form
    kind: AtomKind                  # decision, fact, convention, preference, etc.
    confidence: float               # 0.0-1.0

    # Structured slots (the EDC insight)
    slots: list[TypedSlot]

    # Provenance
    source_file: str                # memory/2026-02-19.md
    run_id: str                     # extraction run identifier
    chunk_idx: int                  # which chunk within the file

@dataclass
class TypedSlot:
    """A typed argument within an atom."""
    role: str                       # "subject", "object", "attribute", "value"
    key: str                        # normalized key: "pet_name"
    key_definition: str             # "The given name of a domesticated animal"
    value: str                      # "Bear"
    slot_type: str                  # "entity", "attribute", "relation", "temporal"

    # Entity sense (only for entity-typed slots)
    sense_type: str | None          # "proper_name", "common_noun", "brand", etc.
    scoped_to: str | None           # parent entity: "Aaron"
    domain: str | None              # "personal_pet", "wildlife", etc.
```

### 3.2 Canonical Registry (SQLite, no graph DB)

```sql
CREATE TABLE canonical_atoms (
    canon_id TEXT PRIMARY KEY,
    canonical_text TEXT NOT NULL,
    kind TEXT NOT NULL,
    confidence REAL NOT NULL,
    agreement_count INTEGER DEFAULT 1,
    total_extractions INTEGER DEFAULT 1,
    first_seen TEXT NOT NULL,
    last_confirmed TEXT NOT NULL,
    slots_json TEXT NOT NULL,        -- JSON array of TypedSlot
    embedding BLOB                   -- vec0 compatible
);

CREATE TABLE atom_provenance (
    canon_id TEXT REFERENCES canonical_atoms(canon_id),
    source_file TEXT NOT NULL,
    run_id TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    extracted_at TEXT NOT NULL
);

CREATE TABLE entity_senses (
    surface_form TEXT NOT NULL,      -- "Bear"
    sense_type TEXT NOT NULL,        -- "proper_name"
    scoped_to TEXT,                  -- "Aaron"
    domain TEXT,                     -- "personal_pet"
    gloss TEXT,                      -- "Aaron's golden retriever"
    context_tokens TEXT,             -- JSON array: ["dog", "pet", "golden retriever"]
    UNIQUE(surface_form, sense_type, scoped_to, domain)
);

-- vec0 virtual table for embedding search
CREATE VIRTUAL TABLE canonical_vectors USING vec0(
    canon_id TEXT PRIMARY KEY,
    embedding FLOAT[384]            -- dimension matches our embedding model
);
```

---

## 4. Extraction Prompt (the key upgrade)

Single LLM call per chunk. The prompt does coreference resolution, slot decomposition, and entity sense tagging all at once.

```
You are a knowledge extraction system. Extract structured memory atoms from this text.

For each extractable fact, decision, convention, or preference, output:

{
  "text": "Coreference-resolved, self-contained statement (1-2 sentences)",
  "kind": "fact|decision|convention|preference|entity|instruction",
  "confidence": 0.0-1.0,
  "slots": [
    {
      "role": "subject|object|attribute|value|relation|temporal",
      "key": "short normalized key (e.g., 'pet_name', 'project_version')",
      "key_definition": "One-sentence definition of what this key means",
      "value": "the actual value",
      "slot_type": "entity|attribute|relation|temporal",
      "sense_type": "proper_name|common_noun|brand|organization|place" (entities only),
      "scoped_to": "parent entity that owns/contains this" (proper names only),
      "domain": "category like 'personal_pet', 'software_project', 'person'" (entities only)
    }
  ]
}

IMPORTANT:
- Resolve all pronouns: "he" → "Aaron", "it" → the specific project name
- Each atom must be self-contained — readable without surrounding context
- For entity slots: always include sense_type, scoped_to, and domain
- key_definition should be specific enough to distinguish from similar keys
  (e.g., "pet_name" definition should mention domesticated animal, not just "a name")
- Skip transient debugging notes and routine status updates
- Prefer fewer high-quality atoms over many noisy ones

Output ONLY a JSON array. No markdown, no explanation. If nothing to extract: []
```

---

## 5. Consensus Strategy

### 5.1 Definition-Based Matching (replaces Jaccard)

Instead of comparing `key="pet_name"` vs `key="dog_name"` with string similarity,
embed the `key_definition` fields and compare those:

```python
def definitions_match(def_a: str, def_b: str, threshold: float = 0.85) -> bool:
    """Compare slot definitions via embedding similarity."""
    emb_a = embed(def_a)  # Uses OpenClaw's existing embedding infrastructure
    emb_b = embed(def_b)
    return cosine_similarity(emb_a, emb_b) >= threshold
```

"The given name of a domesticated animal" vs "The given name of a domesticated canine"
→ cosine similarity ~0.94 → MATCH

"The given name of a domesticated animal" vs "The version string of a software project"
→ cosine similarity ~0.12 → NO MATCH

### 5.2 Entity Sense Gate (prevents Bear Problem)

Before comparing two atoms, check if their shared entities have compatible senses:

```python
def senses_compatible(atom_a: SlottedAtom, atom_b: SlottedAtom) -> bool:
    """Quick check: do shared entity names refer to the same thing?"""
    entities_a = {s.value.lower(): s for s in atom_a.slots if s.slot_type == "entity"}
    entities_b = {s.value.lower(): s for s in atom_b.slots if s.slot_type == "entity"}

    shared = set(entities_a) & set(entities_b)
    for name in shared:
        sa, sb = entities_a[name], entities_b[name]
        # Different sense types → different entities
        if sa.sense_type != sb.sense_type:
            return False
        # Same sense type but different scopes → different entities
        if sa.scoped_to and sb.scoped_to and sa.scoped_to != sb.scoped_to:
            return False
    return True
```

### 5.3 Multi-Pass Consensus (improved from current)

```
Pass 1: Extract atoms from chunk at temp=0.1
Pass 2: Extract atoms from chunk at temp=0.1 (same temp, different sampling)

For each atom in Pass 1:
    Find best match in Pass 2 by:
        1. Entity sense compatibility gate (hard filter)
        2. Slot definition embedding similarity (≥0.85)
        3. Slot value overlap

    If match found → consensus atom (high confidence)
    If no match → check if high-confidence singleton (≥0.9) → keep with lower weight
    Otherwise → reject
```

### 5.4 Registry Upsert (incremental)

When a new consensus atom is produced, check against existing canonical atoms:

```python
def upsert_canonical(new_atom: SlottedAtom, registry_db: sqlite3.Connection):
    """Add or update a canonical atom."""
    # 1. Search vec0 for similar existing atoms
    candidates = vec0_search(new_atom.embedding, k=5)

    for candidate in candidates:
        if (senses_compatible(new_atom, candidate)
            and definitions_match(new_atom, candidate)):
            # Update existing: bump agreement_count, add provenance
            update_canonical(candidate.canon_id, new_atom)
            return

    # 2. No match → insert new canonical atom
    insert_canonical(new_atom)
```

---

## 6. Execution Model (no SIGKILL)

**The orchestrator pattern:** Lilit drives the loop, not a script.

```
For each memory file:
    1. Read file, chunk into ~2000 char windows with 400 char overlap
    2. For each chunk:
        a. Run extraction (single exec, <60s)
        b. Get atoms back
        c. Store raw atoms to disk (checkpoint)
    3. After all chunks: run consensus across passes
    4. Upsert consensus atoms into registry
    5. Mark file as processed
    6. Next file
```

Each step is a short exec call. No background processes. No polling. Progress is
checkpointed after every chunk so interruptions lose at most one chunk of work.

---

## 7. What We're Deferring (from full CMC spec)

| Feature | Why defer |
|---------|----------|
| Abstraction chains | LLM adjudication handles subsumption when it comes up; precomputing chains for every value is expensive for marginal gain |
| Graph database (Neo4j) | Our entity relationships are shallow; SQLite foreign keys are sufficient |
| FAISS / ANN index | vec0 in SQLite handles our scale (hundreds of atoms, not millions) |
| Coreference as separate pass | Bundled into extraction prompt — one LLM call does everything |
| Slot Key Registry with ANN | Definition embedding comparison is done at consensus time; caching can come later if we see repeated key normalization costs |
| Entropy-gated fuzzy matching | Our corpus is small enough that we can afford to compare all candidates in a block |
| Fine-tuned embedding models | text-embedding-3-small via OpenClaw is fine for our scale |

---

## 8. Implementation Plan

### Step 1: Upgrade extraction prompt (30 min)
- Update `shards/extract_memory.py` with the new SlottedAtom prompt
- Test on one file, inspect output quality

### Step 2: Add entity sense fields to ShardAtom (1 hour)
- Extend `spiritwriter/fabric/shard.py` with optional slot fields
- Or: create new `SlottedAtom` class alongside existing `ShardAtom`
- Backward compatible — existing shards still work

### Step 3: Definition-based consensus matching (1 hour)
- Replace Jaccard matching with embedding similarity on key_definitions
- Use OpenClaw's memory_search embedding or direct OpenAI call
- Wire sense compatibility gate into consensus loop

### Step 4: SQLite canonical registry (1 hour)
- Create `canonical_registry.py` in `shards/`
- Schema from Section 3.2
- Upsert logic from Section 5.4
- vec0 integration for blocking

### Step 5: Orchestrator-driven extraction (30 min)
- Refactor extract_memory.py to process one file at a time
- Each chunk is a single short exec
- Checkpoint after every chunk

### Step 6: Run full extraction + validate (1 hour)
- Process all 10 memory files through new pipeline
- Compare output quality vs old regex and old LLM extraction
- Measure: atom count, consensus rate, entity disambiguation accuracy

**Total: ~5 hours of implementation**

---

## 9. Success Criteria

| Metric | Current | Target |
|--------|---------|--------|
| Consensus match rate | 33-50% (Jaccard) | ≥70% (definition-based) |
| False merges | Unknown (no sense gating) | ≤5% |
| Atoms per memory file | 14 avg (noisy) | 8-12 (higher quality) |
| Extraction cost per file | ~$0.003 | ~$0.005 (acceptable for quality gain) |
| Total extraction time (10 files) | 20+ min (SIGKILL) | <10 min (orchestrator) |
| Truncation incidents | 2/10 files | 0 |

---

## 10. Migration Path to Full CMC

If we need to go deeper later:

1. **Abstraction chains** → Add when we see subsumption conflicts in the conflict_log
2. **Graph DB** → Export canonical_atoms + entity_senses to Neo4j when relationship traversal becomes a bottleneck
3. **Slot Key Registry** → Build when we see the same key being re-normalized across multiple extraction runs
4. **ANN index** → Swap vec0 for FAISS when atom count exceeds ~10k
5. **Reification** → Aaron has ideas about this; defer to his direction

Each of these is additive — nothing in CMC-Lite needs to be torn out.
