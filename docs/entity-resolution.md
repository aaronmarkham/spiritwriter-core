# Entity Resolution

Entity resolution in spiritwriter works by defining fields, not surface forms. The same person shows up in three rosters as "Martinez, Carlos", "MARTINEZ, CARLOS A", and "C. Martinez" — same DOB, same booking pattern. Are they the same entity? That's the problem this module solves.

The **`CanonicalRegistry`** is the runtime engine; **Phalanx** is the name of the system it implements (one engine, swappable schema, tier-based confidence). Domain-agnostic — supply a schema describing how your entities are identified, and the registry handles deduplication across sources using deterministic-then-fuzzy matching.

No embedding model, no LLM calls. SQLite, normalization, and tiered confidence scoring.

Where a structure carries a *declared symmetry* — a ring read from a different starting point, an edge list walked backwards, co-equal members in another order — there is no confidence to score: the answer is exact, and the counterpart primitive is [`canonical-forms.md`](canonical-forms.md). Similarity scoring cannot substitute for it. On structural variation the two classes are not separable at any threshold, because pairs that must stay apart score *higher* than pairs that must merge.

The registry resolves over `ShardAtom`s — the (`entity`, `key`, `value`) triples on `FACT` and `ENTITY` atoms are what `ess_fields` references. If you haven't read about atoms yet, start at [`atoms.md`](atoms.md) (especially the `ENTITY` and `FACT` use cases and [`examples/atoms/10_entity.py`](../examples/atoms/10_entity.py)) before diving into how the resolver consumes them.

For how atoms get *made* from long-form text in the first place — overlapping windows, multi-pass consensus voting, no fact lost at chunk boundaries — see [`shingled-extraction.md`](shingled-extraction.md). That's a separate primitive from the resolver, often used together.

For a worked example of the resolver consuming atoms end-to-end (paper → shingled chunking → atoms → memory shard → delegated job → Phalanx resolution), run [`examples/06_phalanx_flow/`](../examples/06_phalanx_flow/). It also demonstrates the normalization step every ingestion pipeline needs before resolution — `K. Yamamoto` and `Kazuhiko Yamamoto` only collapse to one canonical entity after the first-name initial pre-pass.

## The Tier System

Resolution returns a `ResolutionTier` with a confidence score:

| Tier | Confidence | Auto-merge | Triggers when |
|------|-----------|------------|---------------|
| `T1_EXACT` | 0.95 | Yes | ESS digests are identical (same defining fields) |
| `T2_STRONG` | 0.85 | Yes | All fuzzy fields pass threshold; high overall score |
| `T3_FUZZY` | 0.70 | No (creates merge event) | Combined fuzzy score ≥ 0.65 but below T2 |
| `T4_WEAK` | 0.50 | No (flag only) | Context overlap + partial ESS — too weak to act on |
| `NO_MATCH` | 0.0 | n/a | New entity — or a match *refused* on a discriminator field, when `split_on_conflict` is set; see [Refusing a merge](#refusing-a-merge) |

The split between auto-merge (T1, T2) and flag-only (T3, T4) is the safety valve. T1 is "definitely the same"; T2 is "almost certainly the same"; T3+ wants a human or a higher-confidence pass before merging records.

## Why CMC-Lite

The full Consensus Memory Canonicalization spec ([specs/cmc-spec-v0.1.md](specs/cmc-spec-v0.1.md)) draws on academic prior art (EDC/EMNLP 2024, Graphiti/Zep, SimpleMem, EMem-G) and defines a four-stage pipeline: **Normalize & Embed**, **Cluster & Block**, **Consensus & Merge**, **Reify & Store**. It targets ≥85% recall on semantic duplicates with ≤5% false-merge rate — the right architecture for large-scale knowledge graph construction.

That pipeline needs embedding infrastructure (vec0), LLM calls in the clustering stage, and multi-pass consensus voting — three pieces of dedicated infrastructure. CMC-Lite picks the three highest-impact ideas from the full spec and implements them with zero new dependencies:

1. **Entity Sense Signatures (ESS)** — from the "Bear Problem" analysis in the CMC spec. Multiple records mention "Bear": person, pet, or brand? ESS resolves it by hashing the *defining fields* together (name + DOB + gender), not the surface string. Same defining fields = same entity, regardless of how the source spelled it.
2. **Tiered confidence resolution** — from the Graphiti/Zep pattern of escalating from deterministic to fuzzy to LLM-assisted. CMC-Lite implements T1 (exact ESS) through T4 (weak context) without requiring LLM calls.
3. **Phalanx (overlapping windows)** — from the overlapping-window extraction pattern. The pipeline ([examples/extract_memory.py](examples/extract_memory.py)) uses overlapping text chunks so facts spanning chunk boundaries get captured by at least two passes. Originally called "shingles" (overlapping roof tiles); renamed Phalanx because the metaphor is stronger (overlapping shields, mutual coverage, defensive strength) and avoids the medical connotation. Variable names in `extract_memory.py` still say "shingle" for historical reasons.

The CanonicalRegistry documented below is the runtime half; `extract_memory.py` is the ingestion half. Together: extract atoms from text using overlapping windows, then resolve entities across atoms using ESS + tiered matching.

## Normalize before you resolve

**The registry does not auto-normalize candidate fields beyond the baseline `.strip().lower()` that ESS computation does internally.** Anything more — punctuation stripping, name-initial reduction, date format unification, abbreviation expansion — has to happen *before* you call `resolve()` / `upsert()`. This is by design: applications own what counts as "the same" for their domain. But it's a sharp corner, because the default behavior silently misattributes records when callers don't realize they need to pre-normalize.

The failure mode looks like this:

```python
# WRONG — no pre-normalization
short = {"first_name": "K.", "last_name": "Yamamoto"}
long  = {"first_name": "Kazuhiko", "last_name": "Yamamoto"}

registry.upsert(short, registry.resolve(short), "byline", "0")
result = registry.resolve(long)
result.tier   # ResolutionTier.T4_WEAK or NO_MATCH — NOT T1, because
              # ESS('K.', 'Yamamoto') ≠ ESS('Kazuhiko', 'Yamamoto').
              # Two canonical entities get created for the same person.
```

The fix: declare a per-field normalizer map and pipe candidates through `apply_normalizers()` before calling the registry. The helpers ship at the top of `spiritwriter.fabric.canonicalize`:

```python
from spiritwriter.fabric.canonicalize import (
    CanonicalRegistry, CanonicalSchema,
    # Pre-resolution normalization helpers — see "Normalize before you resolve"
    apply_normalizers, first_initial, strip_punctuation, pipeline,
)

# Declare once per schema; reuse for every candidate
NORMALIZERS = {
    "first_name": first_initial,                           # 'K.' / 'Kazuhiko' → 'K'
    "last_name":  pipeline(str.upper, strip_punctuation),  # 'O\'Brien' → 'OBRIEN'
}

def ingest(candidate):
    cand = apply_normalizers(candidate, NORMALIZERS)
    result = registry.resolve(cand)
    return registry.upsert(cand, result, source_name="...", source_id="...",
                           raw=candidate)  # preserve the original surface form
```

Helpers shipped with the module:

| Helper | What it does | Use for |
|---|---|---|
| `first_initial(s)` | First letter, uppercased | Collapsing `'K.'` / `'Kazuhiko'` / `'K'` to one form |
| `strip_punctuation(s)` | Strip ASCII punctuation (hyphens preserved) | Names with apostrophes, periods, commas |
| `normalize_name(s)` | Uppercase + strip + collapse whitespace + strip punctuation | General-purpose name field |
| `normalize_date(s)` | Parse various date formats → ISO 8601 | DOB / event date fields that arrive in mixed formats |
| `apply_normalizers(cand, map)` | Apply per-field normalizers; fields without a normalizer pass through | The composer; what you actually call |
| `pipeline(*fns)` | Compose normalizers left-to-right | When a field needs multiple transformations |

For more, see the demo at [`examples/06_phalanx_flow/`](../examples/06_phalanx_flow/) — `normalize_author()` there is the worked equivalent.

## Quick Start

```python
from spiritwriter.fabric.canonicalize import (
    CanonicalRegistry, CanonicalSchema, ResolutionTier,
    apply_normalizers, first_initial, normalize_name, normalize_date,
)

schema = CanonicalSchema(
    name="inmate",
    ess_fields=["last_name", "first_name", "dob"],   # defining identity
    fuzzy_fields={"last_name": 0.90, "first_name": 0.80},  # per-field thresholds
    context_fields=["facility", "gender"],            # weak signals (T4)
    metadata_fields=["charges", "booking_date"],      # stored, not used in resolution
    age_bucket_size=2,
)

# Declare per-field normalizers once; reuse for every candidate.
NORMALIZERS = {
    "last_name":  normalize_name,    # uppercase + strip + collapse + strip punct
    "first_name": first_initial,     # 'Carlos' / 'C.' / 'Carlos A' → 'C'
    "dob":        normalize_date,    # any common date format → ISO 8601
}

registry = CanonicalRegistry("/tmp/inmates.db", schema)
```

The registry opens a SQLite database in WAL mode. The schema is hashed and stored on first open — reopening with a different schema raises `ValueError`, so you can't accidentally feed records to a registry that disagrees about identity.

The `NORMALIZERS` dict is your contract with the schema. If you change it, you change what "same entity" means — and any registry built against the old normalizers will silently collide or diverge with the new ones (the same misattribution failure mode this whole section exists to prevent, shifted up a level). Treat it as part of the schema definition; version it alongside.

**The registry's `schema_hash()` guard does NOT extend to normalizers.** Reopening a registry with a different `CanonicalSchema` raises `ValueError`; reopening with a different `NORMALIZERS` map proceeds silently and starts producing different ESS digests for the same source records. By deliberate design — apps own normalization — but worth eyes-open. If your app re-deploys with normalizer changes, consider hashing your normalizer set as part of your release metadata and asserting it on registry open. Anything past `.strip().lower()` is your contract to keep stable.

## Resolving Records

`resolve()` is read-only. It returns a `ResolutionResult` you inspect before deciding to persist. `upsert()` writes the resolution to the registry. Both expect a candidate that's **already been through your normalizers** — see [Normalize before you resolve](#normalize-before-you-resolve) for why.

```python
raw_candidate = {
    "last_name": "Martinez",
    "first_name": "Carlos",
    "dob": "1990-05-12",
    "facility": "Lyon County",
    "gender": "M",
}

candidate = apply_normalizers(raw_candidate, NORMALIZERS)

result = registry.resolve(candidate)
result.tier            # ResolutionTier.NO_MATCH (first time)
result.confidence      # 0.0
result.canonical_id    # None
result.field_matches   # {}

# Persist as a new entity. Pass the *original* raw record as `raw=`
# so the surface form survives for audit even though we resolved on
# the normalized version.
cid = registry.upsert(
    candidate, result,
    source_name="lyon_county_jail",
    source_id="booking-2024-1234",
    raw=raw_candidate,
)
```

> **Note on the tier examples below.** They use raw candidate dicts to illustrate how the registry behaves *internally* — what `.strip().lower()` collapses, where the fuzzy fallback kicks in, when T3 fires instead of T2. Production callers should pre-normalize via `apply_normalizers()` first (see [Normalize before you resolve](#normalize-before-you-resolve)). With first-initial normalization in place, the "middle initial" case below becomes T1, not T3 — but you only get that collapsing by declaring the normalizer.

### T1: Same Person, Different Source

The defining fields are identical after normalization (lowercased, stripped). Different `last_name` casing doesn't matter — ESS computation lowercases everything before hashing.

```python
candidate2 = {
    "last_name": "MARTINEZ",       # normalizes to "martinez"
    "first_name": "Carlos",        # exact match after normalization
    "dob": "1990-05-12",
    "facility": "NDOC",
    "gender": "M",
}

result2 = registry.resolve(candidate2)
result2.tier           # ResolutionTier.T1_EXACT — same ESS digest
result2.confidence     # 0.95
result2.canonical_id   # same cid as candidate1

registry.upsert(candidate2, result2, source_name="ndoc", source_id="ndoc-56789")
```

### T3: Same Person, Middle Initial

Now the first name has a middle initial — `"CARLOS A"` vs `"Carlos"`. ESS uses `.strip().lower()` only, *not* full name normalization, so the ESS digests are different and T1 misses. Fuzzy resolution picks it up but lands at T3, not T2 — and the reason teaches the tier system.

```python
candidate3 = {
    "last_name": "Martinez",
    "first_name": "CARLOS A",      # middle initial — different ESS, fuzzy bridge
    "dob": "1990-05-12",
    "gender": "M",
}

result3 = registry.resolve(candidate3)
result3.tier           # ResolutionTier.T3_FUZZY
result3.confidence     # 0.70
result3.field_matches  # {"last_name": True, "first_name": True}
# T3 does NOT auto-merge — registry.upsert() creates a new entity
# and records a merge event in the `merges` table for review.
```

**Why T3 and not T2?** Both fuzzy fields pass their thresholds (last_name 1.0 ≥ 0.90, first_name ~0.85 ≥ 0.80), so per-field the match is strong. The combined score is the average of fuzzy quality (~0.93) and ESS field overlap. ESS overlap drops because `first_name` *digests* differ — `"carlos"` vs `"carlos a"` are different strings — leaving only 2 of 3 ESS fields matching for an overlap of 0.67. Combined score: `(0.93 + 0.67) / 2 ≈ 0.80`. That's ≥ 0.65 (T3 threshold) but < 0.85 (T2 threshold).

The takeaway: T2 needs both *high fuzzy quality* and *high ESS overlap*. A middle-initial divergence drops the second one even when the first is solid. To T2-merge this, the candidate would need to either match ESS exactly (no divergent field) or share more fields. T3 is the right answer here — strong-but-not-certain — and it correctly punts to manual review.

### T2: Catching the Strong Case

For T2 you need fuzzy variations that *don't* break ESS overlap — typically because the variation is in a fuzzy-only field that isn't part of ESS, or because both records share an extra ESS field that pulls overlap up. In the inmate schema, T2 typically fires on case differences plus minor first-name variation when DOB matches exactly.

### T3: Typo, Low Confidence

```python
candidate4 = {
    "last_name": "Martinez",
    "first_name": "Carlitos",      # could be typo, could be different person
    "dob": "1990-05-12",
    "gender": "M",
}

result4 = registry.resolve(candidate4)
result4.tier           # ResolutionTier.T3_FUZZY
result4.confidence     # 0.70
result4.field_matches  # {"last_name": True, "first_name": True/False — depends on score}
```

T3 doesn't auto-merge. The merge event lands in the `merges` table for review — call `registry.merge()` manually after a human or higher-confidence signal confirms.

**ESS by design.** ESS is *exact-match-after-light-normalization*. Fuzzy matching handles the long tail of typos, middle initials, and transliterations without weakening the T1 guarantee. The trade-off: anything that breaks ESS overlap costs you a tier, even when fuzzy says "looks like a match."

### Batch Processing

```python
from spiritwriter.fabric.canonicalize import canonicalize_batch

records = [
    {"last_name": "Smith",  "first_name": "John",   "dob": "1985-03-15", "source_id": "001"},
    {"last_name": "SMITH",  "first_name": "JOHN A", "dob": "1985-03-15", "source_id": "002"},
    {"last_name": "Johnson","first_name": "Jane",   "dob": "1992-08-20", "source_id": "003"},
]

# Note: canonicalize_batch does NOT pre-normalize candidates — same rule
# as resolve()/upsert(). For the merge pattern modeled in
# "Normalize before you resolve", run records through apply_normalizers()
# (or a list comprehension) before passing them to the batch call.

results = canonicalize_batch(
    records, registry,
    source_name="county_roster",
    source_id_field="source_id",
)

for record, result in results:
    print(f"{record['last_name']}: {result.tier.value} (conf={result.confidence})")
# Smith:   no_match  (0.0)   — new entity
# SMITH:   t3_fuzzy  (0.70)  — fuzzy match on "JOHN A" vs "John" (T3, not T2 —
#                              ESS overlap drops because first_name digest differs;
#                              see "Why T3 and not T2?" above)
# Johnson: no_match  (0.0)   — different person
```

## Folding: what happens after a match

Resolution decides *which* entity a record belongs to. Folding decides what
that entity's stored fields look like afterwards.

Without folding, a canonical record is frozen at first sight: `upsert()` bumps
`last_seen` and `source_count`, and the field blob keeps whatever the first
sighting happened to carry. Nothing is lost — `get_entity()` still returns
every sighting — but a later, richer record contributes nothing to the
canonical view, and a later contradicting one raises no signal at all.

`fold_entity_fields()` closes that gap. It is pure: it mutates neither
argument and returns a fresh dict, so the same inputs always produce the same
output.

```python
from spiritwriter.fabric.canonicalize import fold_entity_fields

result = fold_entity_fields(
    {"city": "Reno"},                      # stored
    {"city": "Reno", "employer": "ACME"},  # incoming
    schema,
)
result.fields    # {"city": "Reno", "employer": "ACME"}
result.filled    # ("employer",)
result.conflicts # ()
```

Rules, in order:

1. **Identity fields are never rewritten.** The entity's stored `ess_digest`
   is computed from `schema.ess_fields`, so rewriting one would desynchronize
   the digest from the fields it hashes. A disagreement is reported as a
   conflict with `reason="identity"` and otherwise left alone — it usually
   means your normalizers let two different people match.
2. **An empty field takes the incoming value** (`policy.fill_empty`, on by
   default). This cannot overwrite anything, so it is strictly enrichment —
   though it is not inert for *future* resolution: `_context_resolve` reads
   stored context fields and `_fuzzy_resolve` reads stored fuzzy fields, so a
   field that was empty (and therefore skipped) starts participating once
   filled. ESS computation is unaffected — both paths filter the stored blob
   to `schema.ess_fields` before hashing, so folded values and the
   `__conflicts__` key can never perturb a digest or an overlap.
3. **Equivalent values are not a conflict.** `scalars_equivalent()` ignores
   case and whitespace runs — deliberately looser than ESS equality, because
   it compares stored text rather than identity.
4. **`policy.combine_fields` merge by sentence-level dedup** instead of
   first-wins.
5. **Anything left is a conflict**, resolved by `policy.precedence` and
   recorded either way.

### Policy

`ResolutionPolicy` is the single place the fold's behavior is decided. Every
option is a total order or a pure predicate, so two runs over the same records
in the same order produce byte-identical stored fields.

```python
from spiritwriter.fabric.canonicalize import ResolutionPolicy, CanonicalRegistry

policy = ResolutionPolicy(
    precedence="richest",          # or "keep-first" (default)
    conflicts="keep-all",          # or "keep-first" (default)
    combine_fields={"notes"},
)
registry = CanonicalRegistry(path, schema, policy=policy)
```

The **default policy is conservative on purpose**: it fills fields the entity
has empty and keeps the stored value on a genuine conflict. No value that
exists today is ever overwritten — the only behavior change from the
pre-folding engine is that empty fields get populated and conflicts get
reported instead of silently absorbed.

`precedence="richest"` lets the more populated record win a conflict, counting
non-empty non-meta fields, with ties falling back to `keep-first`. That
fallback is what makes it a total order rather than a coin flip.

`conflicts="keep-all"` additionally stores suppressed values under a
`__conflicts__` key in the field blob, as `{field: [dropped, ...]}`.

### Conflict variants

`conflicts="variants"` leaves the canonical blob byte-identical to
`keep-first` and instead reifies each contributing sighting's suppressed
values as a row in the `variants` table:

```python
registry = CanonicalRegistry(path, schema,
                             policy=ResolutionPolicy(conflicts="variants"))
...
registry.variants(canonical_id)
# [{"variant_id": "...", "source_name": "b", "source_id": "2",
#   "fields": {"city": "Sparks"}, ...}]
```

The `variant_id` is a content hash over the canonical id, the suppressed
fields, **and the source that contributed them**. Mixing the source in is
load-bearing: without it, two sources dropping the same value collapse to one
row and the fact that *two* independent sources disagreed is lost. Re-ingesting
the same sighting is idempotent (`INSERT OR REPLACE` on the same id), so the
table records distinct disagreements rather than repeat deliveries.

Compared with `keep-all`, this is queryable — "which sources disagreed about
`city`, and what did each say" is a `SELECT`, not a JSON scan.

### Refusing a merge

Every rule so far assumes the match was right and only the fields need
reconciling. `split_on_conflict` covers the case where the *match* is wrong:

```python
policy = ResolutionPolicy(split_on_conflict={"ssn"})
```

A field listed there is a **discriminator** — one a single entity cannot
legitimately hold two of. If a T1 match would conflict on one, the merge is
refused: `resolve()` returns `NO_MATCH` with `split_from` naming the entity it
declined to fold into, `upsert()` mints a separate entity, and the refusal
lands in the `splits` table.

```python
result = registry.resolve(record)
result.tier             # NO_MATCH
result.split_from       # canonical id it refused to merge into
result.split_conflicts  # (FieldConflict(field="ssn", ..., reason="discriminator"),)

registry.splits()       # [{"kept_id": ..., "split_id": ..., "field": "ssn", ...}]
```

This is the one thing a merge-only resolver cannot do. Without it, a thin
`ess_fields` set silently collapses unrelated records onto one digest — and
the thinner the schema, the more damage, quietly, at scale. Absence is not
disagreement: a record missing the discriminator still matches normally.

**How it survives re-ingest.** `entities` carries `UNIQUE(ess_digest)`, so two
records with the same ESS cannot both be stored under it. A split entity gets a
`skolem_digest()` — derived from the base digest *plus* the discriminator
values that proved them different — while `ess_base` keeps the digest its
fields actually hash to, so `find_by_ess()` still finds it.

Mixing the discriminators into that derivation is what makes the split
durable. Without them, re-resolving the split record would recompute the base
digest, match the entity it was deliberately split from, and silently re-fuse
the two. With them, the same record recomputes the same skolemized digest and
lands back on its own entity. Contagion needs no extra bookkeeping: the split
entity keeps its discriminator values, so later records route to whichever
entity they agree with.

Registries created before this existed have no `ess_base` column;
`CanonicalRegistry` adds and backfills it on open (`ess_base = ess_digest`,
which is correct by definition for an unsplit entity).

### Dry run

`field_conflicts()` is the non-mutating twin of `fold_entity_fields()`, and
`registry.plan()` is the same thing at registry level: it returns exactly the
`FoldResult` that `upsert()` will apply, without writing.

```python
resolution = registry.resolve(record)
planned = registry.plan(record, resolution)   # None if it would create a new entity
if planned and planned.conflicts:
    review(planned.conflicts)
else:
    registry.upsert(record, resolution, "source", record_id)
```

Both paths call the same pure function, so a plan cannot drift from the write
it predicts. That is the point of keeping the predicate separate rather than
reimplementing "what would happen" alongside "what happens".

For a whole batch, pass `dry_run=True` and a report:

```python
from spiritwriter.fabric.canonicalize import canonicalize_batch, ResolutionReport

report = ResolutionReport()
canonicalize_batch(records, registry, "county_roster", dry_run=True, report=report)

report.tiers                # {"t1_exact": 12, "t3_fuzzy": 2, "no_match": 40}
report.fields_filled        # {"employer": 9}
report.conflicts            # [{"field": "city", "kept": ..., "dropped": ..., ...}]
report.identity_conflicts   # ess_fields disagreements — always worth a look
```

A dry run resolves each record against the registry **as it stands now**; it
does not model records within the same batch resolving against each other,
since none of them are committed.

`ResolutionReport` is deliberately timestamp-free, so `report.to_dict()` is
byte-identical across runs over the same state. That makes two reports
diffable — which is how you notice that a normalizer change quietly moved
forty records from T2 to T4.

## Entity Sense Signatures

An ESS is a content-addressed identity anchor — SHA-256 over a sorted list of `(field, normalized_value)` pairs. Two records with the same ESS are the same entity by construction.

```python
from spiritwriter.fabric.canonicalize import EntitySenseSig

ess1 = EntitySenseSig.compute(
    last_name="Martinez", first_name="Carlos", dob="1990-05-12",
)
ess2 = EntitySenseSig.compute(
    last_name="MARTINEZ", first_name="Carlos", dob="1990-05-12",
)
assert ess1 == ess2   # same digest after .strip().lower() — T1 match

# Partial record — different digest
ess3 = EntitySenseSig.compute(last_name="Martinez", first_name="Carlos")
ess1.overlap(ess3)    # 1.0 — shared fields all match
ess1 == ess3          # False — different field set, different digest
```

**Watch out:** `EntitySenseSig.compute()`'s built-in normalization is `.strip().lower()` only, *not* `normalize_name()`. That means `"Carlos"` and `"CARLOS A"` produce *different* digests at this low level. At the registry-via-`resolve()`-and-`upsert()` level, app-side normalization via `apply_normalizers()` IS the recommended pattern — see [Normalize before you resolve](#normalize-before-you-resolve). The fuzzy fallback bridges the gap for whatever your normalizers don't collapse.

### Age Bucketing

When DOB is missing but age is known, bucket ages so close ages share an ESS field:

```python
from spiritwriter.fabric.canonicalize import age_to_bucket

age_to_bucket(42, bucket_size=2)   # "42-43"
age_to_bucket(43, bucket_size=2)   # "42-43" — same bucket

ess = EntitySenseSig.compute(
    last_name="Smith", first_name="John",
    age_bucket=age_to_bucket(34),  # "34-35"
)
```

Bucket size is a precision/recall trade-off. Wider buckets catch more matches but merge people who happen to be close in age. The default of 2 years works for most rosters.

## Normalization Utilities

Exposed for cases where you want to inspect or pre-process before resolution:

```python
from spiritwriter.fabric.canonicalize import normalize_name, normalize_date, fuzzy_score

normalize_name("  martinez, carlos a.  ")   # "MARTINEZ CARLOS A"
normalize_name("DE LA CRUZ")                 # "DE LA CRUZ"

normalize_date("05/12/1990")    # "1990-05-12"
normalize_date("May 12, 1990")  # "1990-05-12"
normalize_date("garbage")       # None

fuzzy_score("Martinez", "MARTINEZ")   # 1.0   (exact after normalization)
fuzzy_score("Carlos",   "Carlitos")   # ~0.86 (prefix-match boost — Carlos is a 6-char prefix of Carlitos)
fuzzy_score("Smith",    "Smythe")     # ~0.73 (no prefix — diverges at char 3 — plain SequenceMatcher ratio)
fuzzy_score("A",        "ALEXANDER")  # 0.0   (length-ratio filter rejects: 1/9 < 0.5)
```

`fuzzy_score` is `SequenceMatcher.ratio()` with `normalize_name` pre-applied, plus a **prefix-match boost** (one string is a ≥3-char prefix of the other → score floored at 0.85) and a **length-ratio filter** (length ratio ≤0.5 or ≥2.0 → score is 0.0). The filter is what stops "A" from fuzzy-matching every name starting with A. The boost is what makes middle-initial cases like "Carlos" / "Carlos A" produce solidly-matching first-name scores even though `SequenceMatcher.ratio()` alone would land lower.

## Querying the Registry

```python
entity = registry.get_entity(cid)
entity["ess_fields"]      # {"last_name": "martinez", ...}
entity["source_count"]    # 3
entity["first_seen"]      # ISO timestamp
entity["last_seen"]       # ISO timestamp
entity["sightings"]       # list of source records

sightings = registry.sightings(cid)   # detail view of every record merged into this entity

# Fuzzy search across the whole registry — slower than resolve()
matches = registry.find_fuzzy(
    {"last_name": "Martinz", "first_name": "Carlo"},
    limit=5,
)

# Iterate
for entity in registry.entities():
    print(entity["canonical_id"], entity["ess_fields"])

for entity in registry.entities(since="2026-04-01T00:00:00Z"):
    print("Recent:", entity["canonical_id"])
```

## Manual Merge

When two entities are clearly the same but resolution didn't catch it:

```python
registry.merge(
    keep_id=cid_a,
    discard_id=cid_b,
    reason="Confirmed same person via booking photo comparison",
)
```

All sightings move from `cid_b` to `cid_a`. `cid_b` is deleted from `entities`. The merge is recorded in the `merges` table with the reason — provenance is preserved, so you can always answer "why are these merged?"

## Statistics

```python
registry.stats()
# {
#     "entities": 1247,
#     "sightings": 3891,
#     "merges": 45,
#     "sources": {
#         "lyon_county_jail":  890,
#         "ndoc":             1204,
#         "clark_county_ccdc": 1797,
#     },
# }
```

## Schema Design

The schema is the contract between your domain and the registry. Choose fields carefully — once the registry has data, the schema hash check prevents you from changing it.

### ESS Fields

ESS fields are what makes two records "the same entity." Pick fields that are:

- **Present in most records.** Missing fields don't contribute to the ESS — they don't fail resolution, but they weaken it.
- **Stable.** Address and phone change too often. Last name, DOB, SSN-last-4 don't.
- **Together unique.** `last_name` alone is too broad. `last_name + first_name + dob` is usually enough for people.

| Good | Bad | Why |
|------|-----|-----|
| `last_name`, `first_name`, `dob`, `ssn_last4` | `address`, `phone` | Stable across time |
| `manufacturer`, `model_number` (products) | `current_price`, `stock_status` | Defining vs incidental |
| `entity_name`, `scope` (memory entities) | `last_seen`, `confidence` | Identity vs metadata |

### Fuzzy Fields

Fuzzy fields handle name variations, typos, transliterations. The threshold determines how much variation T2/T3 will accept:

```python
fuzzy_fields={
    "last_name":  0.90,   # tight — last names vary less
    "first_name": 0.80,   # looser — nicknames, middle initials
}
```

Tighter thresholds reduce false merges. Looser thresholds catch more variants. Calibrate against your actual data — if you have ground-truth pairs, sweep the threshold and pick the elbow.

### Context Fields

Context fields drive T4 only. They never *cause* a match; they confirm a weak one:

```python
context_fields=["facility", "gender", "state"]
```

If your domain doesn't have weak-but-corroborating signals, leave this empty. T4 is opt-in.

### Custom Schemas

The same engine handles any entity type:

```python
product_schema = CanonicalSchema(
    name="product",
    ess_fields=["manufacturer", "model_number"],
    fuzzy_fields={"product_name": 0.85},
    context_fields=["category", "price_range"],
)

memory_schema = CanonicalSchema(
    name="memory_entity",
    ess_fields=["entity_name", "scope"],
    fuzzy_fields={"entity_name": 0.90},
    context_fields=["project", "domain"],
)
```

## Storage

SQLite, WAL mode, three tables plus `_meta`:

```sql
entities(canonical_id, ess_digest, ess_fields, first_seen, last_seen,
         source_count, merged_from)

sightings(id, canonical_id, source_name, source_id, fields, raw, created_at)

merges(id, keep_id, discard_id, tier, confidence, reason, merged_at)
```

The `_meta` table holds the schema hash. Reopening with a different schema raises `ValueError` rather than silently producing wrong results. WAL mode means concurrent readers don't block the writer (and vice versa) — but there's still only one writer per database file at a time.

## What Phalanx Is Not

- **Not embedding-based.** No vec0, no FAISS, no LLM scoring. The full CMC spec covers that path; CMC-Lite stops at deterministic + fuzzy on purpose.
- **Not multi-domain in one DB.** One schema per registry. Want to dedupe inmates *and* products? Two registries.
- **Not concurrency-safe for multi-writer.** WAL mode handles concurrent reads, but parallel writes against one DB file will serialize. Shard by domain or by source if you need throughput.
- **Not a graph database.** Sightings link to one canonical entity; merges record provenance. There's no relationship layer between entities.

For richer matching (semantic similarity, multi-modal entities, LLM-assisted disambiguation), the [full CMC pipeline](specs/cmc-spec-v0.1.md) is the upgrade path.
