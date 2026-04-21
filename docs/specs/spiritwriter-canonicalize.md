# spiritwriter-core: `canonicalize` Module Spec

**Status:** Draft
**Date:** 2026-02-25
**Location:** `spiritwriter/fabric/canonicalize.py`
**First consumer:** Frio inmate record normalization

---

## Motivation

CMC-Lite proved three ideas in practice:
1. Definition-based matching beats string similarity (~33% → 80-100% consistency)
2. Entity Sense Signatures (ESS) solve the "same name, different thing" problem
3. Tiered resolution with confidence scoring prevents false merges

These are **domain-agnostic**. An inmate record, a memory atom, a product listing — the resolution logic is the same. The domain-specific parts are: what fields go into the ESS, and what the canonical record looks like.

This module provides the generic canonicalization engine. Applications supply schemas and extractors.

---

## Public API

### `EntitySenseSig`

Computes and compares entity sense signatures.

```python
@dataclass(frozen=True)
class EntitySenseSig:
    """Content-addressed identity anchor.

    An ESS is a hash over a set of defining fields. Two records with
    the same ESS are considered the same entity (T1 match).
    Fields are normalized before hashing: lowered, stripped, sorted.
    """
    fields: tuple[tuple[str, str], ...]  # sorted (key, normalized_value) pairs
    digest: str                          # SHA-256 hex of canonical field representation

    @classmethod
    def compute(cls, **fields: str | None) -> "EntitySenseSig":
        """Build ESS from keyword fields. None values are excluded.

        >>> EntitySenseSig.compute(last="Smith", first="John", dob="1984-03-15", gender="M")
        EntitySenseSig(fields=(...), digest="ab3f...")
        """

    def overlap(self, other: "EntitySenseSig") -> float:
        """Field overlap ratio (0.0-1.0). Used for T2/T3 tier scoring.

        Compares shared field keys. Returns ratio of matching values
        among shared keys. Fields present in one but not the other
        are ignored (partial records shouldn't penalize).
        """

    def __eq__(self, other) -> bool:
        """Exact ESS match = T1 resolution."""
        return self.digest == other.digest
```

**Design choice:** ESS is a frozen dataclass, not just a hash string. Preserving the fields allows `overlap()` for partial matching without recomputing.

### `ResolutionTier`

```python
class ResolutionTier(str, Enum):
    """Confidence tier for entity resolution."""
    T1_EXACT = "t1_exact"        # ESS match — same entity, no question
    T2_STRONG = "t2_strong"      # Most fields match, high confidence
    T3_FUZZY = "t3_fuzzy"        # Fuzzy match on key fields + demographic overlap
    T4_WEAK = "t4_weak"          # Partial signal — flag, don't auto-merge
    NO_MATCH = "no_match"

    @property
    def auto_merge(self) -> bool:
        return self in (ResolutionTier.T1_EXACT, ResolutionTier.T2_STRONG)

    @property
    def confidence(self) -> float:
        return {
            ResolutionTier.T1_EXACT: 0.95,
            ResolutionTier.T2_STRONG: 0.85,
            ResolutionTier.T3_FUZZY: 0.70,
            ResolutionTier.T4_WEAK: 0.50,
            ResolutionTier.NO_MATCH: 0.0,
        }[self]
```

### `ResolutionResult`

```python
@dataclass
class ResolutionResult:
    """Outcome of resolving a candidate against a known entity."""
    tier: ResolutionTier
    confidence: float              # may be adjusted beyond tier default
    canonical_id: str | None       # existing entity ID if matched, None if new
    field_matches: dict[str, bool] # per-field match breakdown for debugging
    notes: str = ""                # human-readable explanation
```

### `CanonicalSchema`

Application-defined schema that tells the engine which fields matter for resolution.

```python
@dataclass
class CanonicalSchema:
    """Defines how a domain's entities are resolved.

    Applications create one of these to configure the engine.
    """
    name: str                                # e.g. "inmate", "memory_entity", "product"

    # Fields used in ESS computation (order matters for hash stability)
    ess_fields: list[str]                    # e.g. ["last_name", "first_name", "dob", "gender"]

    # Fields that support fuzzy matching (T2/T3 tiers)
    fuzzy_fields: dict[str, float]           # field_name → minimum similarity threshold
                                             # e.g. {"last_name": 0.90, "first_name": 0.85}

    # Fields used for contextual resolution (T4 / tiebreaking)
    context_fields: list[str]                # e.g. ["facility", "booking_date"]

    # Optional: age bucketing for when DOB is unavailable
    age_bucket_size: int = 2                 # ±N years counts as same bucket

    # Optional: temporal proximity window for context matching
    temporal_window_days: int = 7            # records within N days are "same window"
```

**Example — Frio:**
```python
INMATE_SCHEMA = CanonicalSchema(
    name="inmate",
    ess_fields=["last_name", "first_name", "dob", "gender"],
    fuzzy_fields={"last_name": 0.90, "first_name": 0.85},
    context_fields=["facility", "booking_date"],
    age_bucket_size=2,
    temporal_window_days=7,
)
```

**Example — Agent Memory:**
```python
MEMORY_SCHEMA = CanonicalSchema(
    name="memory_entity",
    ess_fields=["entity_name", "entity_type", "defining_context"],
    fuzzy_fields={"entity_name": 0.85},
    context_fields=["source_scope"],
)
```

### `CanonicalRegistry`

SQLite-backed entity registry with resolution logic.

```python
class CanonicalRegistry:
    """Persistent entity registry with tier-based resolution.

    Stores canonical entities and their sightings (source-specific records).
    Resolution is schema-driven — the same engine handles inmates, memory
    entities, products, etc.

    Storage: SQLite (WAL mode), one DB per registry.
    Optional: vec0 column for embedding-backed fuzzy search (T3/T4).
    """

    def __init__(self, db_path: str | Path, schema: CanonicalSchema):
        """Open or create a registry.

        Creates tables on first run. Schema is stored in DB metadata
        so mismatches are caught early.
        """

    def resolve(self, candidate: dict[str, Any]) -> ResolutionResult:
        """Resolve a candidate record against the registry.

        1. Compute ESS from candidate's ess_fields
        2. Exact ESS lookup → T1
        3. Fuzzy field scan on fuzzy_fields → T2/T3
        4. Context field check → T4 or NO_MATCH

        Does NOT modify the registry. Call upsert() to persist.
        """

    def upsert(self, candidate: dict[str, Any], resolution: ResolutionResult,
               source_name: str, source_id: str, raw: dict | None = None) -> str:
        """Insert or update an entity based on resolution.

        - T1/T2: Add sighting to existing canonical entity, update last_seen
        - T3: Merge with logging (records the merge event)
        - T4: Create new entity (don't auto-merge)
        - NO_MATCH: Create new entity

        Returns canonical_id.
        """

    def merge(self, keep_id: str, discard_id: str, reason: str = "") -> None:
        """Manually merge two canonical entities.

        Moves all sightings from discard to keep. Records merge provenance.
        """

    def get_entity(self, canonical_id: str) -> dict[str, Any] | None:
        """Fetch canonical entity with all sightings."""

    def find_by_ess(self, ess: EntitySenseSig) -> list[dict[str, Any]]:
        """Exact ESS lookup."""

    def find_fuzzy(self, fields: dict[str, str], limit: int = 10) -> list[ResolutionResult]:
        """Fuzzy search across fuzzy_fields. Uses string similarity
        or vec0 embeddings if available."""

    def entities(self, since: str | None = None) -> Iterator[dict[str, Any]]:
        """Iterate canonical entities, optionally filtered by last_seen."""

    def sightings(self, canonical_id: str) -> list[dict[str, Any]]:
        """All source sightings for an entity."""

    def stats(self) -> dict[str, int]:
        """Registry statistics: total entities, sightings, merges, source breakdown."""
```

### `canonicalize_batch()`

Convenience function for processing a batch of records in one call.

```python
def canonicalize_batch(
    records: list[dict[str, Any]],
    registry: CanonicalRegistry,
    source_name: str,
    source_id_field: str = "source_id",
) -> list[tuple[dict[str, Any], ResolutionResult]]:
    """Resolve and upsert a batch of records.

    Returns list of (record, resolution) tuples for the caller
    to act on (e.g., build alerts, update state).
    """
```

---

## SQLite Schema

```sql
-- Registry metadata
CREATE TABLE _meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- Stores: schema_name, schema_hash, created_at, version

-- Canonical entities
CREATE TABLE entities (
    canonical_id TEXT PRIMARY KEY,
    ess_digest TEXT NOT NULL,
    ess_fields TEXT NOT NULL,          -- JSON: {field: value} for recomputation
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    source_count INTEGER DEFAULT 1,
    merged_from TEXT,                  -- JSON array of absorbed canonical_ids
    UNIQUE(ess_digest)                 -- one entity per exact ESS
);

-- Source sightings
CREATE TABLE sightings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_id TEXT NOT NULL REFERENCES entities(canonical_id),
    source_name TEXT NOT NULL,
    source_id TEXT NOT NULL,
    fields TEXT NOT NULL,              -- JSON: all candidate fields
    raw TEXT,                          -- JSON: original record (optional)
    created_at TEXT NOT NULL,
    UNIQUE(source_name, source_id)
);

-- Merge history (audit trail)
CREATE TABLE merges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keep_id TEXT NOT NULL,
    discard_id TEXT NOT NULL,
    tier TEXT NOT NULL,
    confidence REAL NOT NULL,
    reason TEXT,
    merged_at TEXT NOT NULL
);

CREATE INDEX idx_ess ON entities(ess_digest);
CREATE INDEX idx_sightings_entity ON sightings(canonical_id);
CREATE INDEX idx_sightings_source ON sightings(source_name, source_id);
```

---

## String Normalization

Shared utilities used by ESS computation and fuzzy matching:

```python
def normalize_name(s: str) -> str:
    """Normalize a name for comparison.
    Uppercase, strip, collapse whitespace, remove punctuation except hyphen.
    """

def normalize_date(s: str) -> str | None:
    """Parse various date formats → ISO 8601 (YYYY-MM-DD) or None."""

def age_to_bucket(age: int, bucket_size: int = 2) -> str:
    """Convert age to bucket string for ESS when DOB unavailable.
    age_to_bucket(42, 2) → '42-43'  (even bucket floor)
    """

def fuzzy_score(a: str, b: str) -> float:
    """String similarity score (0.0-1.0). Uses SequenceMatcher
    but with normalization pre-applied. Considers prefix matching
    and length ratio filtering (from Frio's existing logic).
    """
```

---

## Integration Points

### With existing `spiritwriter.fabric`

- **`ShardAtom`**: A canonical entity can be serialized as a `ShardAtom` (kind=ENTITY) for distribution via the shard system. The `entity` field maps to `canonical_id`, `key` to the primary name, `value` to ESS digest.
- **`ShardStore`**: Registries can optionally be backed by shards for distribution. A registry snapshot becomes a shard; resolution results become atoms.
- **`TraceEmitter`**: Resolution events (merge, new entity, tier match) emit trace events for provenance.

### With Frio

Frio imports:
```python
from spiritwriter.fabric.canonicalize import (
    CanonicalSchema, CanonicalRegistry, EntitySenseSig,
    ResolutionTier, canonicalize_batch,
)

INMATE_SCHEMA = CanonicalSchema(
    name="inmate",
    ess_fields=["last_name", "first_name", "dob", "gender"],
    fuzzy_fields={"last_name": 0.90, "first_name": 0.85},
    context_fields=["facility", "booking_date"],
)

registry = CanonicalRegistry("~/.frio/canonical.db", INMATE_SCHEMA)
```

Frio's `frio_canonicalize.py` only handles:
- `CanonicalInmate` dataclass (domain-specific)
- Per-source extractors (OCV HTML parsing, Clark County, SAVE NV)
- Converting `CanonicalInmate` → `dict[str, Any]` for the generic engine

---

## What Stays in `extract.py`

The existing `extract.py` (regex-based conversation extraction) stays as-is. It serves a different purpose: extracting atoms from conversation text. The canonicalize module is for **resolving entities across records**, not extracting them from prose.

Future: `extract.py` could feed atoms into a `CanonicalRegistry` for cross-session memory dedup — but that's a separate integration, not a refactor.

---

## Module Exports

Added to `spiritwriter/fabric/__init__.py`:

```python
from spiritwriter.fabric.canonicalize import (
    EntitySenseSig,
    ResolutionTier,
    ResolutionResult,
    CanonicalSchema,
    CanonicalRegistry,
    canonicalize_batch,
    normalize_name,
    fuzzy_score,
)
```

---

## Implementation Phases

### P1: Core types + ESS + resolution logic (no SQLite)
- `EntitySenseSig`, `ResolutionTier`, `ResolutionResult`, `CanonicalSchema`
- `normalize_name()`, `normalize_date()`, `age_to_bucket()`, `fuzzy_score()`
- In-memory resolution (dict-based) for testing
- Unit tests: ESS computation, tier classification, fuzzy scoring

### P2: SQLite registry
- `CanonicalRegistry` with full CRUD
- Schema creation, migration, metadata validation
- `resolve()`, `upsert()`, `merge()`
- `canonicalize_batch()`
- Tests with temp DBs

### P3: TraceEmitter integration
- Resolution events emitted as trace events
- Merge provenance in hash chain

### P4: vec0 fuzzy search (optional)
- Embed entity names via vec0 for T3/T4 tier matching
- Fallback to `fuzzy_score()` when vec0 unavailable

---

## Test Plan

```
tests/test_canonicalize.py

- test_ess_compute_deterministic        # same fields → same digest
- test_ess_compute_ignores_none         # None fields excluded
- test_ess_compute_normalized           # case/whitespace insensitive
- test_ess_overlap_full                 # all fields match → 1.0
- test_ess_overlap_partial              # some fields match
- test_ess_overlap_disjoint             # no shared keys → 0.0
- test_resolution_t1_exact              # exact ESS → T1
- test_resolution_t2_strong             # most fields match
- test_resolution_t3_fuzzy              # fuzzy name + demographics
- test_resolution_t4_weak               # partial signal only
- test_resolution_no_match              # nothing matches
- test_registry_upsert_new              # creates entity
- test_registry_upsert_existing         # adds sighting
- test_registry_merge                   # manual merge
- test_registry_merge_provenance        # merge history recorded
- test_batch_mixed                      # batch with T1 + new entities
- test_schema_mismatch_raises           # wrong schema on open
- test_age_bucket                       # age bucketing logic
- test_normalize_name                   # various name formats
- test_normalize_date                   # date parsing
- test_bear_problem                     # "Bear" the dog vs "bear" the animal
                                        #  → different ESS due to entity_type
```

---

## Open Questions

1. **vec0 availability** — Should the module hard-fail if vec0 isn't available, or gracefully degrade to string-only matching for T3/T4? (Recommend: graceful degradation.)
2. **Schema evolution** — If a `CanonicalSchema` changes (e.g., add a field to `ess_fields`), existing ESS digests are invalidated. Support re-indexing? Or version schemas and keep old digests?
3. **Shard serialization** — Should `CanonicalRegistry.export_shard()` be a method, or left to the consumer? (Recommend: method, since the mapping from entities → atoms is mechanical.)
