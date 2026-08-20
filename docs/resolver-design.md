# Resolver Design Notes

[`entity-resolution.md`](entity-resolution.md) documents *how* to use the resolver. This documents *why* it is shaped the way it is, and what was deliberately not built.

The comparisons here are with [docling-graph](https://github.com/docling-project/docling-graph)'s `core/merge/`, which solves the same problem — collapsing records that name one real thing — from the batch graph-fusion side rather than the online resolver side. Two teams reached similar primitives independently, and the places they diverge are the places worth explaining.

## The decision everything else follows from

An Entity Sense Signature **retains its fields**. It is not only a hash:

```python
@dataclass(frozen=True)
class EntitySenseSig:
    fields: tuple[tuple[str, str], ...]  # sorted (key, normalized_value) pairs
    digest: str                          # SHA-256 over those
```

The alternative — collapse identity to an opaque scalar — is what docling-graph does, and it is a perfectly reasonable choice there: their fingerprint becomes a graph node id, gets exported to Cypher and CSV, and needs to be short and stable.

```python
return f"{class_name}_{fingerprint}"   # blake2b[:16]
```

But an opaque id supports exactly one question: *equal or not*. Keeping the pairs supports that **and** a graded one — `overlap()` walks the shared field keys and returns the match ratio, ignoring keys present on only one side so partial records are not penalised for being partial.

Three things fall out of that, and they are the substance of the difference.

### 1. Fuzzy matching can be thresholded instead of escalated

Because `overlap()` produces a number, the resolver can decide. `ResolutionTier` carries both a confidence and an explicit auto-merge boundary:

| Tier | Confidence | Auto-merge |
|---|---|---|
| `T1_EXACT` | 0.95 | yes |
| `T2_STRONG` | 0.85 | yes |
| `T3_FUZZY` | 0.70 | no — records a merge event |
| `T4_WEAK` | 0.50 | no — flag only |
| `NO_MATCH` | 0.00 | n/a |

docling-graph cannot threshold, because there is nothing to compare but the rendered string. So an ambiguous alias must go to a confirmer, and their own module docstring says so:

> lets an id-space LLM call confirm or reject each one. **Confirmation is mandatory for merging** … Without an LLM callable the pass is propose-only — candidates are logged, nothing is merged.

That is not a preference for humans-in-the-loop, it is a consequence of the identity model.

### 2. No LLM in the resolution path

`fabric/canonicalize.py` contains zero LLM references. Resolution is SQLite, normalization, and arithmetic. It is deterministic, free, auditable by re-running, and works offline and in a tight loop — which is what an unattended daemon needs.

Both designs are recognisably Fellegi-Sunter's three-way split (match / non-match / clerical review). The difference is where the automation boundary sits: this resolver automates the review band above a threshold and degrades gracefully when nobody is watching; theirs keeps a confirmer in it.

### 3. Discriminating power is general rather than per-datatype

Lacking field comparison, docling-graph hand-rolled `digit_signature()` — a regex extracting ordered digit runs — so that `LFP_20vol` does not swallow `LFP_40vol`. A special case for one datatype. With ESS the digits live in a declared field and disagreement falls out of the ratio.

Their fallback signal is documented as insufficient in their own docstring: *"containment alone cannot tell an alias from a product tier (`CONFORT PLUS` is NOT `CONFORT`)"*. That is exactly the case `overlap()` catches structurally. The magic constants that surround it (`_MIN_CONTAINMENT_LEN = 4`; skip when a superset has more than one candidate base) are what a string-only signal requires. "Ignore keys present in one record but not the other" is the principled version of the same instinct.

## Accumulate, do not re-derive

`entities` carries `first_seen`, `last_seen` and `source_count`; `sightings` retains every contributing record. The registry is stateful and gets better as data arrives.

docling-graph's `MergeReport` is *"deliberately timestamp-free"* so that re-running a merge produces byte-identical artifacts. Right for reproducible batch fusion; wrong for an online resolver, where a confirmed decision that only survives as a JSON file someone remembers to pass again is a decision you will lose.

Determinism is still wanted, just at a different level: `ResolutionPolicy` makes every fold option a total order or a pure predicate, so repeated runs over the same records produce byte-identical *stored fields*. `precedence="richest"` degrades to `keep-first` on a tie rather than choosing arbitrarily — that fallback is what makes it a total order rather than a coin flip.

## Normalization belongs to the caller

The registry does not auto-normalize beyond the `.strip().lower()` that ESS computation does internally. Applications own what counts as "the same" for their domain, so the helpers (`normalize_name`, `first_initial`, `strip_punctuation`, `strip_accents`, composed with `pipeline`) are offered rather than applied.

This is a sharp corner and is documented as one, because the default silently misattributes records when a caller does not realise pre-processing is needed. Two failure modes are worth knowing:

- **`K.` vs `Kazuhiko`** — visibly different strings, easy to anticipate.
- **`GARCÍA` vs `GARCIA`** — the same failure wearing a disguise. `normalize_name` does not strip diacritics (`[^\w\s-]` is Unicode-aware, so `Í` survives `.upper()`), and fuzzy scoring does not rescue it either: on a short surname one substitution is a large fraction of the string, so the pair scores 0.833 against a 0.90 threshold. Two sources that disagree about accents produce two entities, silently.

`strip_accents` exists for the second. It is deliberately **not** folded into `normalize_name`: doing so would change the digest of every accented entity already stored, orphaning it from records normalized the new way. That is a re-key with a migration story, not a patch.

## Identity fields are immutable once stored

Attribute folding reconciles a matched record's fields into the entity's stored blob, but never rewrites an identity field. The stored `ess_digest` is computed from `schema.ess_fields`; rewriting one would desynchronize the digest from the fields it hashes. Disagreement there is reported with `reason="identity"` and otherwise left alone — it usually means the normalizers let two different people match, which is worth surfacing rather than absorbing.

`CanonicalSchema.schema_hash()` guards the same invariant across time: it is stored in `_meta` on first run and validated on every open, so a registry refuses a schema that has drifted from the data already in it. docling-graph's `strict_template_check` does the analogous check *within* one merge, comparing inputs to each other. Both are worth having; they guard different axes.

## A plan must not be able to drift from the write

`field_conflicts()` and `CanonicalRegistry.plan()` are non-mutating twins of the fold, and they work by calling the same pure function the write path calls — not by reimplementing "what would happen" alongside "what happens". A dry run is therefore exact rather than an approximation that rots.

This one is lifted directly. docling-graph's `conflicting_scalar_fields` is documented as *"the non-mutating dry-run twin of `fold_node_attrs`"*, and the discipline is theirs.

## Cost of entry

`canonicalize.py` imports `sqlite3`, `re`, `hashlib`, `difflib`, `unicodedata`, `json`, `uuid` — all standard library.

Worth noting as a contrast rather than a criticism: `core/merge/` is itself clean (stdlib, networkx, pydantic), but it cannot be installed without `docling-graph`, whose base dependencies pull `torch`, `torchvision`, `triton`, `transformers` and fifteen `nvidia-*` packages — roughly 4.5 GB of a 5.9 GB environment. That is the price of the document-understanding pipeline the merge module lives beside, not of the merge module.

## What was adopted from docling-graph

Named plainly, because the ideas are theirs:

- **Attribute folding** — the shape of `node_folder.py`: non-empty wins, formatting-insensitive equality, designated text fields combined by sentence-level dedup rather than first-wins.
- **A policy object of total orders** — `MergePolicy`, where CLI flags map one-to-one onto fields and each option is documented as deterministic.
- **The dry-run twin discipline** described above.

Nothing was copied verbatim and no dependency was added.

## Where docling-graph is ahead

- **Provenance grounding.** Bounding-box geometry tying an extracted value back to its position in the source document. A different problem, solved properly.
- **Surface forms carry information a schema does not model.** ESS sees only declared fields, so two records that agree on everything declared but differ in reality resolve T1 and merge. Containment on the *rendered* name sometimes catches what the schema missed. This is the strongest argument for adding their containment check as a **veto** on a fuzzy match rather than as a merge signal.
- **Conflict variants and cross-document splits** — see below.

## Deliberately not built

Conflict variants (reifying suppressed values as queryable rows) and the anti-merge (`split_on_conflict`, refusing a match that conflicts on a discriminator field) were built, reviewed, and then cut. They require a schema change — a new column, two new tables, and a migration that writes on registry open — and no consumer needed them.

The measurement that settled it: over a 360-record corpus with 45% context-field dropout and ground truth attached, folding changed resolution quality not at all — identical tier histogram, identical cluster count, zero false merges and zero split entities. The gain was data completeness on the canonical record, not matching. A migration was not worth that.

The design, the risks, and the review findings are preserved in
[spiritwriter-core#94](https://github.com/aaronmarkham/spiritwriter-core/issues/94), with the code on the `deferred/canonicalize-variants-splits` branch. The bar for reviving it is a consumer that needs it, not the ideas being good.
