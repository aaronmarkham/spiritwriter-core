# Consensus Memory Canonicalization (CMC)
## Technical Specification for Cross-Run Entity Resolution and Atom Reification

**Version**: 0.1 Draft  
**Date**: 2026-02-19  
**Author**: Aaron (with Claude)

---

## 1. Problem Statement

When multiple agent runs extract "memory atoms" (atomic facts, entities, relationships) from the same or overlapping source material, the resulting atoms exhibit three categories of mismatch that defeat naive deduplication:

| Mismatch Type | Example | Why Fuzzy Matching Fails |
|---|---|---|
| **Granularity** | `pet_name: "Bear"` vs `dog_name: "Bear"` vs `animal_name: "Bear"` | String similarity is high on the value but the *slot key* diverges at different abstraction levels |
| **Framing** | `"Aaron has a dog named Bear"` vs `"Bear is Aaron's pet"` vs `"Aaron owns a golden retriever called Bear"` | Same referent, different predicate structure |
| **Decomposition** | One run: `(Aaron, owns, Bear)` + `(Bear, is_a, dog)` vs another: `(Aaron, has_pet, Bear the dog)` | Same information split into different numbers of triples |
| **Homonym Collision** | `"Bear"` (dog's name) vs `"bear"` (the animal) vs `"Bear"` (product/brand) | The same token is a proper noun in one context and a common noun in another — embeddings conflate them |

Empirically, fuzzy string matching (Levenshtein, Jaro-Winkler, token-set-ratio) on extracted atoms yields only **20-30% recall** on true semantic duplicates. Embedding cosine similarity improves this to ~50-60% but still fails on granularity mismatches where the embedding captures the abstraction level as a meaningful distinction.

### 1.1 Design Goals

1. **≥85% recall** on semantic duplicates across runs (measured on held-out annotated pairs)
2. **≤5% false merge rate** (merging genuinely distinct facts)
3. **Incremental**: works on streaming atoms, not just batch
4. **Deterministic where possible**: minimize LLM calls in the hot path (follow Graphiti's entropy-gated pattern)
5. **Canonical output**: produce a single canonical atom with provenance chain back to source runs

---

## 2. Key Prior Art & What We Borrow

### 2.1 EDC (Extract, Define, Canonicalize) — Zhang & Soh, EMNLP 2024

Three-phase pipeline: Open IE → Schema Definition → Schema Canonicalization. The key insight is the **Define** step: before canonicalizing, generate natural-language *definitions* for each extracted relation/entity type, then canonicalize by comparing definitions (not surface strings). This collapses `profession`/`job`/`occupation` because their definitions converge.

**We borrow**: The Define step as our "Slot Normalization" phase.

### 2.2 Graphiti/Zep — Rasmussen 2025

Production-grade temporal knowledge graph for agent memory. Their entity deduplication evolved from all-LLM to a hybrid approach:
- **Entropy-gated fuzzy matching**: compute Shannon entropy over normalized entity name characters. Low-entropy strings (short, repetitive) skip fuzzy matching and go straight to LLM. High-entropy strings use classical IR first.
- **Hybrid edge deduplication**: classical text overlap + embedding similarity for candidates, then hybrid search (RRF) for reconciliation. LLMs only called when classical methods are ambiguous.

**We borrow**: The tiered resolution strategy (deterministic → embedding → LLM) and the edge deduplication constraint of limiting search to same entity-pair edges.

### 2.3 SimpleMem — 2025

Reformulates raw dialogue into "compact memory units" — self-contained facts with resolved coreferences and absolute timestamps. Online semantic synthesis consolidates related fragments during writing.

**We borrow**: The atomic unit specification — each memory atom must be self-contained and coreference-resolved before entering the canonicalization pipeline.

### 2.4 EMem-G — Event-Centric Memory Graph, 2025

Uses neo-Davidsonian event semantics to decompose into Enriched Discourse Units (EDUs). Key insight: conversational references often use unnamed or generic entities ("my pet", "that conference"), so they perform entity/concept mention detection on queries and use mentions as anchors into the graph rather than relying on exact entity strings.

**We borrow**: The argument-node extraction pattern for decomposing atoms into typed argument slots.

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    CMC Pipeline                              │
│                                                              │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐ │
│  │  Stage 1  │──▶│  Stage 2  │──▶│  Stage 3  │──▶│  Stage 4  │ │
│  │ Normalize │   │  Cluster  │   │ Consensus │   │  Reify   │ │
│  │  & Embed  │   │  & Block  │   │  & Merge  │   │  & Store │ │
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘ │
│       ▲                                              │       │
│       │              ┌──────────┐                    │       │
│       └──────────────│  Canon   │◀───────────────────┘       │
│                      │ Registry │                            │
│                      └──────────┘                            │
└─────────────────────────────────────────────────────────────┘
```

### Data Flow

```
Raw Atoms (from N agent runs)
    │
    ▼
[Stage 1] Normalize each atom → NormalizedAtom
    │  - Coreference resolution
    │  - Slot decomposition into typed arguments
    │  - Slot key normalization via definitions
    │  - Embed: slot_key, slot_value, full_atom
    │
    ▼
[Stage 2] Block & Cluster → CandidateGroups
    │  - Embedding-based blocking (not n²)
    │  - Intra-block pairwise scoring
    │  - Connected component extraction
    │
    ▼
[Stage 3] Consensus Merge → CanonicalAtom
    │  - Per-cluster: select canonical slot keys
    │  - Per-cluster: resolve value conflicts
    │  - Confidence scoring via agreement count
    │
    ▼
[Stage 4] Reify → CanonicalMemoryStore
    - Upsert into canonical registry
    - Link provenance to source atoms/runs
    - Update entity graph edges
```

---

## 4. Data Model

### 4.1 Raw Atom (input)

```python
@dataclass
class RawAtom:
    """An atomic fact extracted by a single agent run."""
    atom_id: str                    # UUID
    run_id: str                     # Which agent run produced this
    source_ref: str                 # Reference to source material
    timestamp: datetime             # When extracted
    surface_form: str               # Natural language: "Aaron has a dog named Bear"
    triples: list[Triple]           # Structured: [(Aaron, has_pet, Bear)]
    confidence: float               # Extraction confidence [0, 1]

@dataclass
class Triple:
    subject: str
    predicate: str
    object: str
    qualifiers: dict[str, str]      # e.g., {"species": "dog", "breed": "golden retriever"}
```

### 4.2 Normalized Atom (Stage 1 output)

```python
@dataclass
class NormalizedAtom:
    """Atom with decomposed, typed argument slots."""
    atom_id: str
    source_atom_ids: list[str]      # Provenance
    run_id: str

    # Decomposed representation
    slots: list[TypedSlot]

    # Embeddings (precomputed)
    slot_embeddings: dict[str, np.ndarray]   # slot_key → embedding
    value_embeddings: dict[str, np.ndarray]  # slot_value → embedding
    composite_embedding: np.ndarray          # Full atom embedding

    # Normalized canonical intent
    canonical_intent: str           # e.g., "entity_attribute" | "relationship" | "event"
    intent_definition: str          # NL definition of what this atom expresses

@dataclass
class TypedSlot:
    """A single typed argument slot within an atom."""
    slot_key: str                   # Normalized: "pet_name" → "entity.name"
    slot_key_definition: str        # NL: "The given name of a domesticated animal"
    slot_value: str                 # "Bear"
    slot_type: SlotType             # ENTITY | ATTRIBUTE | RELATION | TEMPORAL | ...
    abstraction_level: int          # 0=most specific, higher=more abstract
    # The abstraction chain: ["Bear", "dog named Bear", "pet named Bear", "animal named Bear"]
    abstraction_chain: list[str]
    # Entity Sense Signature (populated for ENTITY-typed slots only)
    entity_sense: EntitySenseSignature | None = None  # See Section 5.5

class SlotType(Enum):
    ENTITY = "entity"               # A thing: person, place, object
    ATTRIBUTE = "attribute"         # A property of an entity
    RELATION = "relation"           # A connection between entities
    TEMPORAL = "temporal"           # A time reference
    QUANTITATIVE = "quantitative"   # A numeric value
    QUALITATIVE = "qualitative"     # A descriptive quality
```

### 4.3 Canonical Atom (Stage 3 output)

```python
@dataclass
class CanonicalAtom:
    """The consensus-merged, reified atom."""
    canon_id: str                   # Stable canonical ID
    canonical_form: str             # NL: "Aaron owns a golden retriever named Bear"
    canonical_slots: list[TypedSlot]
    canonical_embedding: np.ndarray

    # Consensus metadata
    agreement_count: int            # How many runs produced this
    total_runs_seen: int            # How many runs processed the source material
    consensus_confidence: float     # agreement_count / total_runs_seen
    first_seen: datetime
    last_confirmed: datetime

    # Provenance
    source_atom_ids: list[str]      # All raw atoms that merged into this
    source_run_ids: list[str]       # All runs that contributed
    conflict_log: list[ConflictRecord]  # Any value disagreements

    # Graph integration
    entity_refs: list[str]          # Canonical entity IDs this atom connects
    supersedes: list[str]           # Previous canonical atoms this replaces

@dataclass
class ConflictRecord:
    slot_key: str
    values: dict[str, int]          # value → count of runs asserting it
    resolution: str                 # "majority" | "most_specific" | "llm_adjudicated"
    resolved_value: str
```

---

## 5. Stage Details

### 5.1 Stage 1: Normalize & Embed

This is the most critical stage. The goal is to transform heterogeneous raw atoms into a comparable representation.

#### 5.1.1 Coreference Resolution

Before anything else, resolve all pronouns and anaphora within each atom's surface form. This is already well-handled by modern LLMs — a single pass with a small model suffices.

```
Input:  "He told me his dog's name is Bear"
Output: "Aaron told the assistant that Aaron's dog's name is Bear"
```

#### 5.1.2 Slot Decomposition

Decompose each atom into typed argument slots using a neo-Davidsonian frame:

```
Surface: "Aaron has a golden retriever named Bear"
Decomposition:
  - (ENTITY, "actor", "Aaron")
  - (RELATION, "relationship", "ownership")
  - (ENTITY, "object", "Bear")
  - (ATTRIBUTE, "object.species", "dog")
  - (ATTRIBUTE, "object.breed", "golden retriever")
  - (ATTRIBUTE, "object.name", "Bear")
```

#### 5.1.3 Slot Key Normalization (the EDC "Define" step)

This is where we solve the pet/dog/animal problem. For each slot key, generate a **definition**:

```
"pet_name"    → "The given name of a domesticated animal owned by a person"
"dog_name"    → "The given name of a domesticated canine owned by a person"
"animal_name" → "The given name of an animal"
```

Then embed the *definitions* and compare. The definition embeddings will be much closer than the slot key embeddings because the semantic content converges.

**Optimization**: Maintain a **Slot Key Registry** that caches known mappings:

```python
class SlotKeyRegistry:
    """Maps variant slot keys to canonical forms."""

    def __init__(self):
        self._canonical: dict[str, CanonicalSlotKey] = {}
        self._variants: dict[str, str] = {}  # variant → canonical_id
        self._definition_index: AnnoyIndex = None  # For fast lookup

    def normalize(self, key: str, definition: str) -> str:
        """Returns canonical key, creating new entry if needed."""
        # 1. Exact match in variants cache
        if key in self._variants:
            return self._variants[key]

        # 2. Definition embedding similarity search
        embedding = embed(definition)
        candidates = self._definition_index.get_nns_by_vector(
            embedding, n=5, include_distances=True
        )

        # 3. Threshold check (cosine sim > 0.92 for definitions)
        for canon_id, distance in candidates:
            if distance < 0.08:  # cosine distance
                self._variants[key] = canon_id
                return canon_id

        # 4. No match → create new canonical entry
        canon_id = self._create_canonical(key, definition, embedding)
        return canon_id
```

#### 5.1.4 Abstraction Level Tagging

For each slot, determine its position in a type hierarchy. This prevents false negatives when "golden retriever" and "dog" appear in the same slot:

```
Level 0 (most specific): "golden retriever named Bear"
Level 1: "dog named Bear"
Level 2: "pet named Bear"
Level 3: "animal named Bear"
```

**Implementation**: Use an LLM to generate the abstraction chain once per unique value, then cache. For entities, this is cheap — you're just asking for hypernyms.

#### 5.1.5 Embedding Strategy

Generate three embeddings per atom:

1. **Composite embedding**: Embed the full normalized surface form
2. **Slot key embedding**: Embed the slot key definition (for blocking)
3. **Slot value embedding**: Embed each unique slot value (for matching)

Use a strong embedding model (e.g., `text-embedding-3-large` or a fine-tuned E5) with instructions prefix:

```
"Represent this memory fact for deduplication: Aaron owns a golden retriever named Bear"
```

---

### 5.2 Stage 2: Block & Cluster

#### 5.2.1 Blocking (avoiding O(n²))

The key insight from Graphiti: constrain comparisons to atoms that share at least one entity reference. This turns O(n²) into O(n·k) where k is the average cluster size.

**Primary blocking strategies** (use all, union the candidate sets):

1. **Entity-anchor blocking**: Group atoms by their ENTITY-typed slot values. Two atoms sharing any entity go into the same block.

2. **Embedding-based blocking**: Use approximate nearest neighbor search on composite embeddings. Return top-k (k=20) candidates per atom.

3. **Slot-value hash blocking**: Normalize and hash entity values (lowercase, strip articles/possessives). Atoms sharing any hash go into the same block.

```python
def generate_blocks(atoms: list[NormalizedAtom]) -> list[set[str]]:
    """Generate candidate blocks for pairwise comparison."""
    blocks: dict[str, set[str]] = defaultdict(set)

    for atom in atoms:
        # Entity-anchor blocking
        for slot in atom.slots:
            if slot.slot_type == SlotType.ENTITY:
                key = normalize_entity_key(slot.slot_value)
                blocks[f"entity:{key}"].add(atom.atom_id)

        # Embedding blocking (ANN)
        neighbors = ann_index.get_nns_by_vector(
            atom.composite_embedding, n=20
        )
        for neighbor_id in neighbors:
            pair_key = tuple(sorted([atom.atom_id, neighbor_id]))
            blocks[f"ann:{pair_key}"].update(pair_key)

    return list(blocks.values())
```

#### 5.2.2 Pairwise Scoring (within blocks)

For each pair within a block, compute a multi-signal similarity score:

```python
@dataclass
class PairScore:
    composite_sim: float        # Cosine sim of composite embeddings
    slot_key_overlap: float     # Jaccard of normalized slot keys
    slot_value_sim: float       # Average best-match cosine sim across slot values
    abstraction_compatible: bool # Are abstraction chains overlapping?
    entity_overlap: float       # Jaccard of entity slot values

    @property
    def aggregate(self) -> float:
        """Weighted aggregate score."""
        weights = {
            'composite_sim': 0.25,
            'slot_key_overlap': 0.15,
            'slot_value_sim': 0.30,
            'abstraction_compatible': 0.10,  # Boolean → 1.0 or 0.0
            'entity_overlap': 0.20,
        }
        return sum(
            getattr(self, k) * v
            for k, v in weights.items()
        )
```

#### 5.2.3 Tiered Resolution (Graphiti pattern)

```python
def resolve_pair(a: NormalizedAtom, b: NormalizedAtom) -> Resolution:
    score = compute_pair_score(a, b)

    # Tier 1: High-confidence deterministic merge
    if score.aggregate > 0.90 and score.entity_overlap > 0.8:
        return Resolution.MERGE

    # Tier 2: High-confidence deterministic distinct
    if score.aggregate < 0.30:
        return Resolution.DISTINCT

    # Tier 3: Ambiguous → LLM adjudication
    if 0.30 <= score.aggregate <= 0.90:
        return llm_adjudicate(a, b, score)

    # Entropy gate (from Graphiti): skip fuzzy for low-entropy names
    if shannon_entropy(a.canonical_form) < 2.5:
        return llm_adjudicate(a, b, score)
```

**LLM Adjudication Prompt**:

```
Given two memory atoms extracted from different agent runs, determine if they
refer to the same underlying fact.

Atom A: {a.canonical_form}
  Slots: {a.slots}

Atom B: {b.canonical_form}
  Slots: {b.slots}

Similarity signals:
  - Composite embedding similarity: {score.composite_sim:.3f}
  - Shared entities: {shared_entities}
  - Slot key overlap: {score.slot_key_overlap:.3f}

Consider:
1. Do they refer to the same real-world fact, even if expressed differently?
2. Could one be a more specific/general version of the other?
3. Are there any contradictions that indicate they're genuinely different facts?

Respond with:
- SAME: They express the same fact (possibly at different granularity)
- DIFFERENT: They express genuinely different facts
- SUBSUMES: Atom A is strictly more specific than Atom B (or vice versa)
- CONFIDENCE: [0.0-1.0]
```

#### 5.2.4 Connected Components

After pairwise resolution, build a graph of SAME/SUBSUMES edges and extract connected components. Each component becomes a **CandidateGroup** for consensus merging.

---

### 5.3 Stage 3: Consensus Merge

For each CandidateGroup (connected component of semantically equivalent atoms), produce a single CanonicalAtom.

#### 5.3.1 Canonical Slot Key Selection

For each slot position, multiple variant keys exist. Select the canonical key by:

1. **Prefer the most specific non-ambiguous key**: "dog_name" beats "pet_name" if we have evidence it's a dog (from any atom in the group).
2. **Majority vote on abstraction level**: If 3/5 runs say "dog" and 2/5 say "pet", use "dog".
3. **Definition-similarity to the value**: The key whose definition best matches the actual slot value wins.

```python
def select_canonical_key(variants: list[TypedSlot]) -> TypedSlot:
    """Select the best slot key from a group of variants."""
    # Group by abstraction level
    by_level = defaultdict(list)
    for slot in variants:
        by_level[slot.abstraction_level].append(slot)

    # Prefer most specific level with majority support
    for level in sorted(by_level.keys()):
        slots_at_level = by_level[level]
        if len(slots_at_level) >= len(variants) * 0.4:  # ≥40% support
            # Among these, pick the most frequent key
            key_counts = Counter(s.slot_key for s in slots_at_level)
            best_key = key_counts.most_common(1)[0][0]
            return next(s for s in slots_at_level if s.slot_key == best_key)

    # Fallback: most frequent key overall
    key_counts = Counter(s.slot_key for s in variants)
    best_key = key_counts.most_common(1)[0][0]
    return next(s for s in variants if s.slot_key == best_key)
```

#### 5.3.2 Value Conflict Resolution

When atoms agree on the slot key but disagree on values:

```python
def resolve_value_conflict(
    slot_key: str,
    values: list[tuple[str, str]]  # (value, run_id) pairs
) -> tuple[str, ConflictRecord]:
    """Resolve conflicting values for the same slot."""
    value_counts = Counter(v for v, _ in values)

    # Case 1: Unanimous → easy
    if len(value_counts) == 1:
        return values[0][0], None

    # Case 2: Subsumption → prefer most specific
    # e.g., "dog" vs "golden retriever" → "golden retriever"
    if is_subsumption_set(list(value_counts.keys())):
        most_specific = get_most_specific(list(value_counts.keys()))
        return most_specific, ConflictRecord(
            slot_key=slot_key,
            values=dict(value_counts),
            resolution="most_specific",
            resolved_value=most_specific
        )

    # Case 3: True conflict → majority vote + log
    majority_value = value_counts.most_common(1)[0][0]
    majority_count = value_counts.most_common(1)[0][1]

    if majority_count / len(values) >= 0.6:
        return majority_value, ConflictRecord(
            slot_key=slot_key,
            values=dict(value_counts),
            resolution="majority",
            resolved_value=majority_value
        )

    # Case 4: No clear majority → LLM adjudication
    resolved = llm_resolve_value(slot_key, value_counts)
    return resolved, ConflictRecord(
        slot_key=slot_key,
        values=dict(value_counts),
        resolution="llm_adjudicated",
        resolved_value=resolved
    )
```

#### 5.3.3 Confidence Scoring

```python
def compute_consensus_confidence(group: CandidateGroup) -> float:
    """
    Confidence based on:
    - Agreement ratio (how many runs agree)
    - Extraction confidence (average confidence of source atoms)
    - Conflict severity (how many slots had conflicts)
    """
    agreement_ratio = group.agreement_count / group.total_runs
    avg_extraction_conf = mean(a.confidence for a in group.atoms)
    conflict_penalty = 1.0 - (group.conflict_count / group.total_slots) * 0.3

    return agreement_ratio * 0.5 + avg_extraction_conf * 0.3 + conflict_penalty * 0.2
```

---

### 5.4 Stage 4: Reify & Store

#### 5.4.1 Canonical Registry

The registry is the persistent store of all canonical atoms, serving as the "source of truth" for the memory system.

```python
class CanonicalRegistry:
    """Persistent store of canonical atoms with entity graph."""

    def __init__(self, graph_db, vector_store):
        self.graph = graph_db          # Neo4j / FalkorDB
        self.vectors = vector_store     # FAISS / Pinecone / pgvector

    def upsert(self, canon: CanonicalAtom) -> str:
        """Insert or update a canonical atom."""
        # Check for existing atom that this might update
        existing = self._find_existing(canon)

        if existing:
            # Merge: update confidence, add provenance
            merged = self._merge_with_existing(existing, canon)
            self.graph.update_node(merged)
            self.vectors.update(merged.canon_id, merged.canonical_embedding)
            return merged.canon_id
        else:
            # New canonical atom
            self.graph.create_node(canon)
            self.vectors.insert(canon.canon_id, canon.canonical_embedding)

            # Create/update entity nodes and edges
            for entity_ref in canon.entity_refs:
                self.graph.ensure_entity(entity_ref)
                self.graph.create_edge(
                    entity_ref, canon.canon_id,
                    type="HAS_FACT",
                    temporal_valid=canon.first_seen
                )

            return canon.canon_id

    def _find_existing(self, canon: CanonicalAtom) -> Optional[CanonicalAtom]:
        """Check if a semantically equivalent canonical atom already exists."""
        # Use same tiered resolution as Stage 2 but against the registry
        candidates = self.vectors.search(
            canon.canonical_embedding, k=10
        )
        for candidate_id, distance in candidates:
            if distance < 0.08:  # Very high similarity
                existing = self.graph.get_node(candidate_id)
                if self._is_same_fact(canon, existing):
                    return existing
        return None
```

#### 5.4.2 Provenance Graph

Every canonical atom maintains a full provenance chain:

```
CanonicalAtom("Aaron owns a golden retriever named Bear")
  ├── sourced_from: RawAtom(run_1, "Aaron has a dog named Bear")
  ├── sourced_from: RawAtom(run_2, "Bear is Aaron's pet")
  ├── sourced_from: RawAtom(run_3, "Aaron owns a golden retriever called Bear")
  ├── consensus_confidence: 0.92
  ├── first_seen: 2026-01-15
  ├── last_confirmed: 2026-02-19
  └── supersedes: CanonicalAtom("Aaron has a pet named Bear")  # less specific version
```

---

### 5.5 The "Bear Problem": Named Entity–Common Term Collision

#### 5.5.1 Problem Statement

Aaron's dog is named "Bear." This creates a class of ambiguity that's invisible to embedding-based methods and actively harmful at the blocking stage:

```
Atom A: "Aaron's dog Bear loves the park"
Atom B: "Aaron saw a bear while hiking"
Atom C: "Aaron bought Bear brand mattress"
Atom D: "Bear Stearns was acquired by JPMorgan"
Atom E: "Bear likes belly rubs"          ← Is this the dog or... the animal?
```

All five atoms will cluster together under entity-anchor blocking because they share the token "Bear." Embedding similarity won't save you either — `"Bear loves the park"` and `"Bear likes belly rubs"` will have high cosine similarity regardless of whether Bear is a dog or a grizzly.

This problem is **pervasive** in real-world memory systems:

| Category | Examples |
|---|---|
| Pet names from common nouns | Bear, Shadow, Cookie, Ginger, Pepper, Blue, Scout, Moose |
| Product names from common words | Apple (tech vs fruit), Slack (app vs adjective), Teams (app vs groups) |
| Place names from common words | Mobile (Alabama vs adjective), Reading (UK vs activity), Nice (France vs adjective) |
| People names from common words | Grace (name vs quality), Mark (name vs verb), Bill (name vs invoice) |
| Brand names from animals/objects | Jaguar (car vs animal), Amazon (company vs river), Shell (company vs object) |

The failure mode isn't just false merging — it's **false blocking**. If "Bear" the dog gets blocked with "bear" the animal, and "bear" the animal has an abstraction chain, the system might try to subsume Aaron's pet into a wildlife fact, or worse, merge a product review with a veterinary record.

#### 5.5.2 Why This Defeats Standard Approaches

**Embedding similarity**: Contextual embeddings (BERT, E5, etc.) do capture *some* word sense information, but short atoms like `"Bear likes belly rubs"` provide minimal disambiguation context. The embedding for this sentence sits somewhere between "dog behavior" and "wildlife documentary" in the vector space.

**Fuzzy string matching**: `"Bear"` == `"Bear"` == `"bear"` at 100% match. Worse, the Slot Key Registry will normalize all these into the same entity anchor.

**Abstraction chains**: Actually make it worse — if the system generates `Bear → dog → pet → animal` for the pet, and `bear → animal` for the wildlife fact, the shared `animal` hypernym creates a false subsumption signal.

#### 5.5.3 Solution: Entity Sense Signatures

We introduce an **Entity Sense Signature (ESS)** — a lightweight disambiguator attached to every entity-typed slot value at normalization time. The ESS captures *what kind of thing this token refers to* in this specific atom's context.

```python
@dataclass
class EntitySenseSignature:
    """Disambiguator for entities whose names collide with common terms."""
    surface_form: str           # "Bear"
    sense_id: str               # "bear_dog_001" (stable across runs)
    sense_type: SenseType       # PROPER_NAME | COMMON_NOUN | BRAND | ORGANIZATION
    sense_gloss: str            # "A golden retriever belonging to Aaron"
    parent_entity: str | None   # "Aaron" (the entity this name is scoped under)
    domain: str                 # "personal_pet" | "wildlife" | "product" | "finance"
    distinguishing_context: list[str]  # ["dog", "pet", "golden retriever", "Aaron's"]

class SenseType(Enum):
    PROPER_NAME = "proper_name"       # A name given to a specific entity
    COMMON_NOUN = "common_noun"       # A general category/concept
    BRAND = "brand"                   # A product or brand name
    ORGANIZATION = "organization"     # A company, institution, etc.
    PLACE = "place"                   # A geographic name
```

#### 5.5.4 Where in the Pipeline: Three Checkpoints

The Bear Problem requires intervention at **three separate stages**, not just one:

**Checkpoint 1: Stage 1 (Normalize) — Sense Tagging**

During slot decomposition, every ENTITY-typed slot gets a sense tag. This is cheap — it's just an additional field in the LLM extraction prompt:

```
Extract entities from: "Aaron's dog Bear loves the park"

For each entity, provide:
- name: the entity's surface form
- type: PERSON | ANIMAL | OBJECT | PLACE | ORGANIZATION | BRAND | CONCEPT
- sense: Is this a PROPER_NAME (given name of a specific thing), 
         COMMON_NOUN (general category), BRAND, or ORGANIZATION?
- scoped_to: If this is a proper name, what larger entity "owns" this name?
- distinguishing_context: 2-3 words from the surrounding text that 
                          help distinguish this entity from homonyms

Result:
- {name: "Aaron", type: PERSON, sense: PROPER_NAME, scoped_to: null, 
   context: ["dog owner"]}
- {name: "Bear", type: ANIMAL, sense: PROPER_NAME, scoped_to: "Aaron", 
   context: ["dog", "Aaron's", "loves park"]}
- {name: "the park", type: PLACE, sense: COMMON_NOUN, scoped_to: null, 
   context: ["local", "dog walking"]}
```

**Critical implementation detail**: The `scoped_to` field is what prevents cross-sense merging. "Bear" scoped to "Aaron" is a completely different entity than "Bear" scoped to null (wildlife) or "Bear" scoped to "mattress company."

**Checkpoint 2: Stage 2 (Block & Cluster) — Sense-Aware Blocking**

Modify the blocking function to incorporate sense signatures:

```python
def generate_blocks_sense_aware(atoms: list[NormalizedAtom]) -> list[set[str]]:
    """Block atoms, but segregate by entity sense."""
    blocks: dict[str, set[str]] = defaultdict(set)

    for atom in atoms:
        for slot in atom.slots:
            if slot.slot_type == SlotType.ENTITY:
                ess = slot.entity_sense  # The EntitySenseSignature

                # CRITICAL: Block key includes sense type + scope
                # This prevents "Bear" (dog) from clustering with "bear" (animal)
                if ess.sense_type == SenseType.PROPER_NAME and ess.parent_entity:
                    # Scoped proper names: block by (name, scope_owner)
                    key = f"entity:{ess.surface_form}|scoped:{ess.parent_entity}"
                elif ess.sense_type == SenseType.PROPER_NAME:
                    # Unscoped proper names: block by (name, domain)
                    key = f"entity:{ess.surface_form}|domain:{ess.domain}"
                else:
                    # Common nouns, brands, etc.: block by (name, sense_type)
                    key = f"entity:{ess.surface_form}|sense:{ess.sense_type.value}"

                blocks[key].add(atom.atom_id)

        # Still do embedding-based blocking as a fallback
        # (but embedding comparison now includes sense context)
        neighbors = ann_index.get_nns_by_vector(
            atom.composite_embedding, n=20
        )
        for neighbor_id in neighbors:
            pair_key = tuple(sorted([atom.atom_id, neighbor_id]))
            blocks[f"ann:{pair_key}"].update(pair_key)

    return list(blocks.values())
```

**Checkpoint 3: Stage 2 (Pairwise Scoring) — Sense Compatibility Gate**

Before computing the full pairwise score, check sense compatibility. This is a hard gate — incompatible senses skip the expensive comparison entirely:

```python
def are_senses_compatible(ess_a: EntitySenseSignature, ess_b: EntitySenseSignature) -> bool:
    """Fast check: can these two entity senses possibly refer to the same thing?"""

    # Different sense types are almost never the same entity
    if ess_a.sense_type != ess_b.sense_type:
        # Exception: PROPER_NAME and BRAND can sometimes overlap
        # (e.g., "Apple" as Steve Jobs' creation vs "Apple" as the company)
        compatible_pairs = {
            (SenseType.PROPER_NAME, SenseType.BRAND),
            (SenseType.BRAND, SenseType.PROPER_NAME),
            (SenseType.PROPER_NAME, SenseType.ORGANIZATION),
            (SenseType.ORGANIZATION, SenseType.PROPER_NAME),
        }
        if (ess_a.sense_type, ess_b.sense_type) not in compatible_pairs:
            return False

    # Same surface form but different scopes → different entities
    if (ess_a.parent_entity and ess_b.parent_entity and
            ess_a.parent_entity != ess_b.parent_entity):
        return False

    # Same surface form but different domains with no overlap
    if ess_a.domain != ess_b.domain:
        context_overlap = set(ess_a.distinguishing_context) & set(ess_b.distinguishing_context)
        if len(context_overlap) == 0:
            return False

    return True

def resolve_pair(a: NormalizedAtom, b: NormalizedAtom) -> Resolution:
    """Extended with sense compatibility gate."""
    # Check entity sense compatibility FIRST (cheap, O(1))
    shared_entities = get_shared_entity_surfaces(a, b)
    for entity_name in shared_entities:
        ess_a = get_ess(a, entity_name)
        ess_b = get_ess(b, entity_name)
        if ess_a and ess_b and not are_senses_compatible(ess_a, ess_b):
            return Resolution.DISTINCT  # Hard gate: don't even score

    # Proceed with normal tiered resolution...
    score = compute_pair_score(a, b)
    # ... (rest of tiered resolution as before)
```

#### 5.5.5 The Ambiguous Case: When Context Isn't Enough

Sometimes the source atom genuinely doesn't provide enough context to determine sense:

```
"Bear is doing great this week"
```

Is this the dog? A stock ticker? A sports team? The system handles this with **deferred disambiguation**:

```python
@dataclass
class DeferredSenseResolution:
    """When sense can't be determined from a single atom."""
    atom_id: str
    ambiguous_entity: str           # "Bear"
    candidate_senses: list[EntitySenseSignature]  # All possible senses
    resolution_strategy: str        # "context_accumulation" | "user_query" | "majority_vote"

class SenseResolver:
    def resolve_deferred(self, deferred: DeferredSenseResolution) -> EntitySenseSignature:
        """Resolve ambiguous sense using accumulated context."""

        # Strategy 1: Check other atoms in the same run/session
        # "Bear is doing great" + "took Bear to the vet" → dog
        same_session_atoms = self.get_session_atoms(deferred.atom_id)
        session_context = extract_context_clues(same_session_atoms, deferred.ambiguous_entity)

        if session_context.confidence > 0.8:
            return session_context.best_sense

        # Strategy 2: Check existing canonical atoms for this user
        # If we already know Aaron has a dog named Bear, bias toward that
        existing_senses = self.registry.get_known_senses(
            deferred.ambiguous_entity,
            user_context="Aaron"
        )
        if len(existing_senses) == 1:
            return existing_senses[0]  # Unambiguous in this user's context

        # Strategy 3: Hold in limbo, attach to ALL candidate senses
        # with low confidence, let future atoms disambiguate
        return EntitySenseSignature(
            surface_form=deferred.ambiguous_entity,
            sense_id=f"{deferred.ambiguous_entity}_AMBIGUOUS",
            sense_type=SenseType.PROPER_NAME,  # Default assumption for capitalized
            sense_gloss="Ambiguous reference, pending disambiguation",
            parent_entity=None,
            domain="unknown",
            distinguishing_context=[]
        )
```

#### 5.5.6 The Entity Sense Registry

Parallel to the Slot Key Registry, maintain an **Entity Sense Registry** that accumulates known senses for entity surface forms:

```python
class EntitySenseRegistry:
    """Tracks all known senses for entity surface forms."""

    def __init__(self):
        # "Bear" → [ESS(dog, scoped:Aaron), ESS(animal, scoped:null), ESS(brand, scoped:mattress)]
        self._senses: dict[str, list[EntitySenseSignature]] = defaultdict(list)
        self._sense_embeddings: dict[str, np.ndarray] = {}

    def register_sense(self, ess: EntitySenseSignature):
        """Add a new sense, or merge with existing if duplicate."""
        existing = self._senses[ess.surface_form.lower()]

        for existing_ess in existing:
            if self._is_same_sense(ess, existing_ess):
                # Merge: update context, keep stable sense_id
                existing_ess.distinguishing_context = list(
                    set(existing_ess.distinguishing_context) | set(ess.distinguishing_context)
                )
                return existing_ess.sense_id

        # New sense for this surface form
        self._senses[ess.surface_form.lower()].append(ess)
        self._sense_embeddings[ess.sense_id] = embed(ess.sense_gloss)
        return ess.sense_id

    def lookup(self, surface_form: str, context_clues: list[str]) -> list[tuple[EntitySenseSignature, float]]:
        """Given a surface form and context, return ranked candidate senses."""
        candidates = self._senses.get(surface_form.lower(), [])
        if not candidates:
            return []

        # Score each sense by context overlap
        context_set = set(c.lower() for c in context_clues)
        scored = []
        for ess in candidates:
            ess_context = set(c.lower() for c in ess.distinguishing_context)
            overlap = len(context_set & ess_context) / max(len(context_set | ess_context), 1)
            scored.append((ess, overlap))

        return sorted(scored, key=lambda x: -x[1])

    def get_ambiguity_score(self, surface_form: str) -> float:
        """How ambiguous is this surface form? 0 = unambiguous, 1 = highly ambiguous."""
        senses = self._senses.get(surface_form.lower(), [])
        if len(senses) <= 1:
            return 0.0
        # More senses + more diverse domains = more ambiguous
        domains = set(s.domain for s in senses)
        return min(1.0, (len(senses) - 1) * 0.3 + (len(domains) - 1) * 0.2)
```

#### 5.5.7 Worked Example: Full Pipeline with "Bear"

**Input atoms from 3 runs:**

```
Run 1: "Aaron has a dog named Bear"
Run 2: "Bear is Aaron's golden retriever"  
Run 3: "Aaron saw a bear on his camping trip"
Run 4: "Bear loves belly rubs"
Run 5: "The bear was about 200 pounds"
```

**After Stage 1 (Normalize):**

```
Atom 1: slots=[
  (ENTITY, "owner", "Aaron", ESS(PROPER_NAME, scope=null, domain=person)),
  (ENTITY, "pet", "Bear", ESS(PROPER_NAME, scope="Aaron", domain=personal_pet, context=["dog","named"])),
  (RELATION, "relationship", "ownership")
]

Atom 2: slots=[
  (ENTITY, "pet", "Bear", ESS(PROPER_NAME, scope="Aaron", domain=personal_pet, context=["golden retriever"])),
  (ENTITY, "owner", "Aaron", ESS(PROPER_NAME, scope=null, domain=person)),
  (ATTRIBUTE, "breed", "golden retriever")
]

Atom 3: slots=[
  (ENTITY, "observer", "Aaron", ESS(PROPER_NAME, scope=null, domain=person)),
  (ENTITY, "animal", "bear", ESS(COMMON_NOUN, scope=null, domain=wildlife, context=["camping","saw"])),
  (RELATION, "event", "sighting")
]

Atom 4: slots=[
  (ENTITY, "subject", "Bear", ESS(PROPER_NAME, scope=null, domain=UNKNOWN, context=["belly rubs"])),
  (RELATION, "activity", "belly rubs")
]  ← AMBIGUOUS: could be dog or... unlikely but technically unresolved

Atom 5: slots=[
  (ENTITY, "subject", "bear", ESS(COMMON_NOUN, scope=null, domain=wildlife, context=["200 pounds"])),
  (ATTRIBUTE, "weight", "200 pounds")
]
```

**After Stage 2 (Block & Cluster):**

```
Block A (entity:Bear|scoped:Aaron):  {Atom 1, Atom 2}  ← Clean match
Block B (entity:bear|sense:common_noun): {Atom 3, Atom 5}  ← Clean match
Block C (entity:Bear|domain:UNKNOWN):    {Atom 4}  ← Orphan, needs deferred resolution

Deferred resolution for Atom 4:
  - Context clues: ["belly rubs"]  
  - Known senses for "Bear": [dog(scope:Aaron), wildlife(common)]
  - "belly rubs" strongly correlates with "personal_pet" domain
  - → Resolved to ESS(PROPER_NAME, scope="Aaron", domain=personal_pet)
  - Atom 4 moves to Block A
```

**After Stage 3 (Consensus Merge):**

```
Block A → CanonicalAtom:
  "Aaron owns a golden retriever named Bear"
  consensus_confidence: 1.0 (3/3 atoms agree after deferred resolution)
  canonical_slots: [owner=Aaron, pet_name=Bear, breed=golden_retriever]

Block B → CanonicalAtom:
  "Aaron saw a ~200 pound bear while camping"
  consensus_confidence: 0.85
  canonical_slots: [observer=Aaron, animal=bear(common), weight=200lbs]
```

**Zero false merges.** Without ESS, Atoms 1 and 3 would have been blocked together (both contain "Aaron" + "Bear/bear") and the pairwise scorer would have seen high entity overlap, potentially merging a pet fact with a wildlife sighting.

#### 5.5.8 Cost Analysis

The ESS system adds overhead at Stage 1 only — the sense tagging is bundled into the same LLM call that does slot decomposition (adding ~50 tokens to the prompt and ~30 tokens to the response per atom). Checkpoints 2 and 3 are purely deterministic (dict lookups and set operations). The Entity Sense Registry is a lightweight in-memory structure.

The **savings** from avoided false merges and avoided LLM adjudication calls (pairs that would have been in the ambiguous tier but are now gated out) more than compensate for the extraction overhead.

---

## 6. Incremental Operation

The system must work incrementally as new runs produce atoms:

```python
class IncrementalCMC:
    """Process new atoms as they arrive."""

    def __init__(self, registry: CanonicalRegistry):
        self.registry = registry
        self.slot_key_registry = SlotKeyRegistry()

    async def ingest_atoms(self, atoms: list[RawAtom], run_id: str):
        """Process a batch of atoms from a single run."""

        # Stage 1: Normalize
        normalized = [self._normalize(atom) for atom in atoms]

        # Stage 2: Block against existing registry + new atoms
        blocks = self._generate_blocks_incremental(normalized)

        # Stage 3: For each block, attempt merge with existing canonicals
        for block in blocks:
            existing_canonicals = self._get_existing_in_block(block)
            new_atoms_in_block = [a for a in normalized if a.atom_id in block]

            for atom in new_atoms_in_block:
                matched = self._find_best_match(atom, existing_canonicals)
                if matched:
                    # Update existing canonical with new evidence
                    self._update_canonical(matched, atom, run_id)
                else:
                    # Create new canonical atom
                    canon = self._create_canonical(atom, run_id)
                    self.registry.upsert(canon)

    def _generate_blocks_incremental(
        self, new_atoms: list[NormalizedAtom]
    ) -> list[set[str]]:
        """Block new atoms against existing registry."""
        blocks = []
        for atom in new_atoms:
            # Search existing registry
            candidates = self.registry.vectors.search(
                atom.composite_embedding, k=20
            )
            block = {atom.atom_id}
            for canon_id, distance in candidates:
                if distance < 0.15:  # Generous threshold for blocking
                    block.add(canon_id)
            blocks.append(block)
        return blocks
```

---

## 7. Evaluation Strategy

### 7.1 Metrics

| Metric | Definition | Target |
|---|---|---|
| **Merge Recall** | True semantic duplicates correctly merged / total true duplicates | ≥ 0.85 |
| **Merge Precision** | Correctly merged / total merges attempted | ≥ 0.95 |
| **Homonym Separation Rate** | Correctly separated homonym entities / total homonym pairs | ≥ 0.95 |
| **Canonical Quality** | Human rating of canonical form fidelity (1-5 scale) | ≥ 4.0 |
| **LLM Call Ratio** | Pairs requiring LLM adjudication / total pairs evaluated | ≤ 0.20 |
| **Latency (incremental)** | Time to process a single new atom against registry | ≤ 500ms |

### 7.2 Evaluation Dataset Construction

1. Take N source documents
2. Run K different extraction agents (vary prompts, models, temperature)
3. Manually annotate semantic equivalence classes across all K×N atoms
4. Measure recall/precision of the CMC pipeline against ground truth

### 7.3 Ablation Studies

- Stage 1 only (normalize + embed) → measure embedding-only dedup rate
- Stage 1+2 (add blocking/clustering) → measure with deterministic tier only
- Full pipeline → measure with LLM adjudication
- Vary: embedding model, similarity thresholds, LLM adjudication model

---

## 8. Implementation Roadmap

### Phase 1: Core Pipeline (2 weeks)
- [ ] RawAtom and NormalizedAtom data models
- [ ] Slot decomposition via LLM (prompted, not fine-tuned)
- [ ] Entity Sense Signature extraction (bundled with slot decomposition prompt)
- [ ] Entity Sense Registry with ambiguity scoring
- [ ] Slot key normalization with definition generation
- [ ] Embedding pipeline (composite + slot-level)
- [ ] Sense-aware blocking (entity-anchor + ANN)
- [ ] Tiered pairwise resolution with sense compatibility gate (deterministic + LLM)

### Phase 2: Consensus & Registry (1 week)
- [ ] Canonical slot key selection
- [ ] Value conflict resolution
- [ ] CanonicalAtom assembly
- [ ] Registry with upsert + provenance tracking
- [ ] Graph database integration (Neo4j or FalkorDB)

### Phase 3: Incremental & Production (1 week)
- [ ] Incremental ingestion pipeline
- [ ] Slot Key Registry with cached mappings
- [ ] Entropy-gated fuzzy matching
- [ ] Monitoring: LLM call ratio, merge rates, latency

### Phase 4: Evaluation & Tuning (ongoing)
- [ ] Evaluation dataset construction
- [ ] Threshold tuning
- [ ] Ablation studies
- [ ] Fine-tuning embedding model for slot-key similarity (optional)

---

## 9. Key Design Decisions & Tradeoffs

### 9.1 Why not just use LLMs for everything?

Cost and variance. Graphiti's experience shows that all-LLM deduplication creates "variance, retry loops, and token burn." The hybrid approach (deterministic first, LLM only for ambiguous cases) reduces LLM calls by ~80% while maintaining quality.

### 9.2 Why slot decomposition instead of triple comparison?

Triples impose a rigid (S, P, O) structure that doesn't capture the actual variance in how agents decompose facts. The typed-slot approach is more flexible:
- A triple `(Aaron, has_pet, Bear)` becomes slots that can be compared individually
- A different decomposition `(Aaron, owns, golden retriever)` + `(golden retriever, named, Bear)` produces overlapping slots that the system can align

### 9.3 Why definition-based slot key normalization?

This is the core insight from EDC. Comparing `"pet_name"` to `"dog_name"` at the string level gives low similarity. Comparing their *definitions* ("the given name of a domesticated animal" vs "the given name of a domesticated canine") gives high similarity because the semantic content converges even when the labels diverge.

### 9.4 Why abstraction chains?

Without them, "golden retriever" and "dog" look like different values. With abstraction chains, the system knows "golden retriever" is a specialization of "dog" and can handle subsumption correctly — preferring the more specific value when available rather than treating it as a conflict.

### 9.5 Why Entity Sense Signatures? (The Bear Problem)

Named entities frequently collide with common terms: "Bear" the dog, "bear" the animal, "Bear" the brand. This isn't an edge case — it occurs constantly with pet names, product names, place names, and people names. Without explicit sense disambiguation, the blocking stage groups all "Bear" atoms together, and the pairwise scorer sees high entity overlap between facts about completely different referents. The ESS adds ~80 tokens per atom at extraction time but eliminates entire categories of false merges that would otherwise require expensive LLM adjudication or, worse, silently corrupt the canonical registry. The key insight is that `scoped_to` (which entity "owns" this name) is the strongest disambiguator — "Bear" scoped to Aaron is unambiguously the dog, regardless of what other "Bear" entities exist.

---

## 10. Dependencies

| Component | Recommended | Alternatives |
|---|---|---|
| Embedding model | `text-embedding-3-large` | E5-mistral, BGE-m3 |
| ANN index | FAISS (IVF-PQ) | Annoy, ScaNN, pgvector |
| Graph database | Neo4j | FalkorDB, Memgraph |
| LLM (adjudication) | Claude Sonnet 4.5 | GPT-4o-mini, Gemini 2.0 Flash |
| LLM (slot decomposition) | Claude Haiku 4.5 | GPT-4o-mini |
| Vector store | pgvector | Pinecone, Weaviate |

---

## Appendix A: Comparison with Existing Systems

| Feature | CMC (this spec) | Graphiti/Zep | Mem0 | EDC |
|---|---|---|---|---|
| Multi-run consensus | ✅ Core feature | ❌ Single-stream | ❌ Single-stream | ❌ Single-pass |
| Slot-level decomposition | ✅ Typed slots | ❌ Entity+edge | ❌ Triples | ✅ Triples |
| Definition-based canonicalization | ✅ | ❌ | ❌ | ✅ |
| Abstraction chain handling | ✅ | ❌ | ❌ | ❌ |
| Homonym/polysemy disambiguation (ESS) | ✅ Sense signatures | ⚠️ LLM-only | ❌ | ❌ |
| Temporal awareness | ✅ (via provenance) | ✅ (bi-temporal) | ⚠️ Basic | ❌ |
| Incremental operation | ✅ | ✅ | ✅ | ❌ Batch only |
| Entropy-gated resolution | ✅ (borrowed) | ✅ | ❌ | ❌ |
| Confidence from agreement | ✅ | ❌ | ❌ | ❌ |
