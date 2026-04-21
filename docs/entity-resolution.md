# Entity Resolution — Phalanx (CMC-Lite)

## Background: CMC and Why "Lite"

**CMC** stands for **Consensus Memory Canonicalization** — a full pipeline for resolving entities across multiple agent extraction runs. The [full CMC spec](specs/cmc-spec-v0.1.md) draws from academic prior art (EDC/EMNLP 2024, Graphiti/Zep, SimpleMem, EMem-G) and defines a four-stage pipeline: Normalize & Embed, Cluster & Block, Consensus & Merge, Reify & Store. It targets >=85% recall on semantic duplicates with <=5% false merge rate.

The full pipeline requires embedding infrastructure (vec0), LLM calls in the clustering stage, and a multi-pass consensus voting system. That's the right architecture for large-scale knowledge graph construction, but it's heavy for applications that just need reliable entity matching.

**CMC-Lite** takes the three most impactful ideas from the full spec and implements them with zero new infrastructure — just SQLite and string matching:

1. **Entity Sense Signatures (ESS)** — from the "Bear Problem" analysis in the CMC spec. When multiple records mention "Bear," is it a person's name, a pet, or a brand? ESS resolves this by hashing the *defining fields* together (name + DOB + gender), not just the name string. Same defining fields = same entity, regardless of surface form.

2. **Tiered confidence resolution** — from the Graphiti/Zep pattern of escalating from deterministic matching to fuzzy to LLM-assisted. CMC-Lite implements T1 (exact ESS) through T4 (weak context) without requiring LLM calls.

3. **Multi-pass consensus** — from the overlapping-window extraction pattern. The extraction pipeline (see `examples/extract_memory.py`) uses overlapping text windows to ensure facts that span chunk boundaries are captured by at least two passes.

### Phalanx Branding

The overlapping-window approach was originally called **shingles** (overlapping roof tiles). It was renamed to **Phalanx** — overlapping shields in a formation — because the metaphor is stronger (mutual coverage, defensive strength) and avoids the medical connotation. The code in `extract_memory.py` still uses "shingle" in variable names for historical reasons, but the project name is Phalanx.

The **CanonicalRegistry** (the entity resolution engine documented below) is the runtime component of Phalanx. The extraction pipeline (`extract_memory.py`) is the ingestion component. Together they form the Phalanx system: extract structured atoms from text using overlapping windows, then resolve entities across those atoms using ESS + tiered matching.

## The CanonicalRegistry

A domain-agnostic entity resolution engine. It resolves entities across records using Entity Sense Signatures (ESS), tiered confidence scoring, and fuzzy matching. Applications supply schemas; spiritwriter provides the resolution logic.

## How It Works

1. **Compute ESS** — Hash the defining fields (name, DOB, etc.) into a content-addressed identity anchor.
2. **T1 (Exact)** — If the ESS matches an existing entity, it's the same entity. Confidence: 0.95.
3. **T2 (Strong)** — All fuzzy fields match above threshold + good ESS overlap. Confidence: 0.85.
4. **T3 (Fuzzy)** — Partial fuzzy match (score >= 0.65). Confidence: 0.70. Creates new entity + merge event.
5. **T4 (Weak)** — Context overlap + partial ESS. Confidence: 0.50. Flag only, no auto-merge.
6. **NO_MATCH** — New entity.

T1 and T2 auto-merge (add sighting to existing entity). T3+ create new entities with merge events for review.

## Quick Start

```python
from spiritwriter.fabric.canonicalize import (
    CanonicalRegistry, CanonicalSchema, ResolutionTier,
    canonicalize_batch, normalize_name, fuzzy_score,
)

# Define how your domain's entities are resolved
schema = CanonicalSchema(
    name="inmate",
    ess_fields=["last_name", "first_name", "dob"],
    fuzzy_fields={"last_name": 0.90, "first_name": 0.80},
    context_fields=["facility", "gender"],
    metadata_fields=["charges", "booking_date"],
    age_bucket_size=2,
)

# Create registry (SQLite, WAL mode)
registry = CanonicalRegistry("/tmp/inmates.db", schema)
```

## Resolving Entities

### Single Record

```python
candidate = {
    "last_name": "Martinez",
    "first_name": "Carlos",
    "dob": "1990-05-12",
    "facility": "Lyon County",
    "gender": "M",
}

# Resolve (read-only — doesn't modify registry)
result = registry.resolve(candidate)
print(result.tier)          # ResolutionTier.NO_MATCH (first time)
print(result.confidence)    # 0.0
print(result.canonical_id)  # None
print(result.field_matches) # {}

# Persist the entity
cid = registry.upsert(
    candidate, result,
    source_name="lyon_county_jail",
    source_id="booking-2024-1234",
    raw={"original_html": "..."},  # optional raw source data
)
print(cid)  # UUID hex, e.g. "a1b2c3d4..."
```

### Same Person, Different Source

```python
# Same person appears in a different roster with slight name variation
candidate2 = {
    "last_name": "MARTINEZ",
    "first_name": "CARLOS A",
    "dob": "1990-05-12",
    "facility": "NDOC",
    "gender": "M",
}

result2 = registry.resolve(candidate2)
print(result2.tier)          # ResolutionTier.T1_EXACT (same ESS)
print(result2.confidence)    # 0.95
print(result2.canonical_id)  # same cid as above

# Add as new sighting of existing entity
registry.upsert(
    candidate2, result2,
    source_name="ndoc",
    source_id="ndoc-56789",
)
```

### Fuzzy Matching

```python
# Name typo — "CARLITOS" instead of "CARLOS"
candidate3 = {
    "last_name": "Martinez",
    "first_name": "Carlitos",
    "dob": "1990-05-12",
    "gender": "M",
}

result3 = registry.resolve(candidate3)
print(result3.tier)          # T2_STRONG or T3_FUZZY depending on score
print(result3.confidence)    # 0.85 or 0.70
print(result3.field_matches) # {"last_name": True, "first_name": True/False}
```

### Batch Processing

```python
records = [
    {"last_name": "Smith", "first_name": "John", "dob": "1985-03-15",
     "source_id": "001"},
    {"last_name": "SMITH", "first_name": "JOHN A", "dob": "1985-03-15",
     "source_id": "002"},
    {"last_name": "Johnson", "first_name": "Jane", "dob": "1992-08-20",
     "source_id": "003"},
]

results = canonicalize_batch(
    records, registry,
    source_name="county_roster",
    source_id_field="source_id",
)

for record, result in results:
    print(f"{record['last_name']}: {result.tier.value} "
          f"(confidence={result.confidence})")
# Smith: no_match (confidence=0.0)       — first time
# SMITH: t1_exact (confidence=0.95)      — same ESS
# Johnson: no_match (confidence=0.0)     — new entity
```

## Entity Sense Signatures (ESS)

An ESS is a content-addressed identity anchor — a SHA-256 hash of normalized defining fields:

```python
from spiritwriter.fabric.canonicalize import EntitySenseSig

ess1 = EntitySenseSig.compute(
    last_name="Martinez",
    first_name="Carlos",
    dob="1990-05-12",
)

ess2 = EntitySenseSig.compute(
    last_name="MARTINEZ",     # normalization: lowered, stripped
    first_name="Carlos",
    dob="1990-05-12",
)

assert ess1 == ess2  # same digest — T1 match

# Check field overlap between ESS
ess3 = EntitySenseSig.compute(
    last_name="Martinez",
    first_name="Carlos",
    # dob missing
)
print(ess1.overlap(ess3))  # 1.0 (shared fields match)
print(ess1 == ess3)        # False (different digest — missing field)
```

### Age Bucketing

When DOB is unavailable but age is known, bucket ages for ESS compatibility:

```python
from spiritwriter.fabric.canonicalize import age_to_bucket

print(age_to_bucket(42, bucket_size=2))  # "42-43"
print(age_to_bucket(43, bucket_size=2))  # "42-43"  — same bucket

# Use in ESS
ess = EntitySenseSig.compute(
    last_name="Smith",
    first_name="John",
    age_bucket=age_to_bucket(34),  # instead of DOB
)
```

## Normalization Utilities

```python
from spiritwriter.fabric.canonicalize import normalize_name, normalize_date, fuzzy_score

# Name normalization
print(normalize_name("  martinez, carlos a.  "))  # "MARTINEZ CARLOS A"
print(normalize_name("DE LA CRUZ"))                # "DE LA CRUZ"

# Date normalization (various formats → ISO 8601)
print(normalize_date("05/12/1990"))   # "1990-05-12"
print(normalize_date("May 12, 1990")) # "1990-05-12"
print(normalize_date("1990-05-12"))   # "1990-05-12"
print(normalize_date("garbage"))      # None

# Fuzzy scoring (0.0–1.0)
print(fuzzy_score("Martinez", "MARTINEZ"))  # 1.0 (exact after normalization)
print(fuzzy_score("Carlos", "Carlitos"))    # ~0.7-0.8
print(fuzzy_score("Smith", "Smythe"))       # ~0.6
print(fuzzy_score("A", "ALEXANDER"))        # 0.0 (length ratio filter)
```

## Querying the Registry

```python
# Get entity with all sightings
entity = registry.get_entity(cid)
print(entity["ess_fields"])     # {"last_name": "Martinez", ...}
print(entity["source_count"])   # 2
print(entity["first_seen"])     # ISO timestamp
print(entity["last_seen"])      # ISO timestamp
print(len(entity["sightings"])) # 2

# Get sightings for an entity
sightings = registry.sightings(cid)
for s in sightings:
    print(f"  {s['source_name']}: {s['fields']}")

# Fuzzy search
results = registry.find_fuzzy(
    {"last_name": "Martinz", "first_name": "Carlo"},  # typos
    limit=5,
)
for r in results:
    print(f"  {r.tier.value}: {r.canonical_id} (conf={r.confidence})")

# Iterate all entities
for entity in registry.entities():
    print(f"{entity['canonical_id']}: {entity['ess_fields']}")

# Filter by recency
for entity in registry.entities(since="2026-04-01T00:00:00Z"):
    print(f"Recent: {entity['canonical_id']}")
```

## Manual Merge

When you know two entities are the same but automatic resolution didn't catch it:

```python
registry.merge(
    keep_id=cid_a,
    discard_id=cid_b,
    reason="Confirmed same person via booking photo comparison",
)
# All sightings from cid_b move to cid_a
# cid_b is removed from entities table
# Merge event recorded in merges table
```

## Statistics

```python
stats = registry.stats()
print(stats)
# {
#     "entities": 1247,
#     "sightings": 3891,
#     "merges": 45,
#     "sources": {
#         "lyon_county_jail": 890,
#         "ndoc": 1204,
#         "clark_county_ccdc": 1797,
#     },
# }
```

## Schema Design Guide

### Choosing ESS Fields

ESS fields are the **defining** fields — they determine entity identity. Choose fields that:
- Are present in most records
- Are relatively stable (don't change often)
- Together, uniquely identify an entity

Good ESS fields: `last_name`, `first_name`, `dob`, `ssn_last4`
Bad ESS fields: `address`, `phone` (change too often), `charges` (vary per booking)

### Choosing Fuzzy Fields

Fuzzy fields handle name variations, typos, and transliterations. Set thresholds based on how much variation you expect:

```python
fuzzy_fields={
    "last_name": 0.90,    # tight — last names vary less
    "first_name": 0.80,   # looser — nicknames, diminutives
}
```

### Choosing Context Fields

Context fields provide weak signals for T4 matching. They break ties but don't drive resolution:

```python
context_fields=["facility", "gender", "state"]
```

### Custom Schemas

The same engine handles any entity type:

```python
# Product catalog dedup
product_schema = CanonicalSchema(
    name="product",
    ess_fields=["manufacturer", "model_number"],
    fuzzy_fields={"product_name": 0.85},
    context_fields=["category", "price_range"],
)

# Memory entity dedup (agent knowledge)
memory_schema = CanonicalSchema(
    name="memory_entity",
    ess_fields=["entity_name", "scope"],
    fuzzy_fields={"entity_name": 0.90},
    context_fields=["project", "domain"],
)
```

## Storage

The registry uses SQLite with WAL mode for concurrent read/write:

```sql
-- Canonical entities
entities(canonical_id, ess_digest, ess_fields, first_seen, last_seen,
         source_count, merged_from)

-- Source-specific records
sightings(id, canonical_id, source_name, source_id, fields, raw, created_at)

-- Merge provenance
merges(id, keep_id, discard_id, tier, confidence, reason, merged_at)
```

Schema hash is stored in `_meta` — reopening a registry with a different schema raises `ValueError`.

## Resolution Tier Reference

| Tier | Confidence | Auto-Merge | Criteria |
|------|-----------|------------|----------|
| T1_EXACT | 0.95 | Yes | Identical ESS digest |
| T2_STRONG | 0.85 | Yes | All fuzzy fields pass threshold + score >= 0.85 |
| T3_FUZZY | 0.70 | No (merge event) | Combined fuzzy score >= 0.65 |
| T4_WEAK | 0.50 | No (flag only) | Context overlap + partial ESS |
| NO_MATCH | 0.0 | N/A | New entity |
