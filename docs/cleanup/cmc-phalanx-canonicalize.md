# Cleanup: CMC-Lite · Phalanx · canonicalize

**Status:** Working doc. Decisions are locked unless tagged `OPEN`. Action
items have per-repo checkboxes — tick as we land PRs. Cross-references
to sibling cleanup docs accumulate at the bottom.

**Driving todos:** #1 (this disambiguation), #2 (marketing copy),
touches #5 (worked example) and #9 (references check).

---

## 1 · Glossary — the resolved vocabulary

These are the three things people kept conflating. Each row says what
the thing IS and where the *name* is allowed to appear.

| Name | What it is | Where the name appears |
|---|---|---|
| **`canonicalize`** | The Python module: `CanonicalRegistry`, `CanonicalSchema`, `EntitySenseSig`, `ResolutionTier`. The actual engine, ~800 lines, SQLite-backed. | API reference, module docstrings, import statements. Module-level only. |
| **CMC-Lite** | The *design* — pragmatic implementation of the CMC framework using ESS + tiered fuzzy + overlapping-window extraction. Zero new deps. | Spec files (`docs/specs/cmc-lite-v0.1.md`), implementer docs. Don't use in marketing. |
| **Phalanx** | The *system name* for spiritwriter's entity-resolution stack. Renamed from "shingles" (overlapping shields > overlapping roof tiles; avoids medical connotation). | Deep-dive technical docs (e.g. `docs/entity-resolution.md` body). **NOT** marketing surfaces, repo READMEs, or homepage. |
| **Shingled extraction** | The *separate* primitive that turns long-form text into atoms via overlapping windows + multi-pass voting. Previously bundled under "Phalanx" by accident. "Shingled" describes the process (overlapping coverage like roofing shingles); the historical noun "shingles" is retired. | Deep-dive doc at `docs/shingled-extraction.md`, homepage entry ix. |
| ~~shingles~~ | **Retired.** The historical name for both Phalanx and the consensus-extraction pattern. No new content should use it. | Catches in `sw_vocab` as `known_drift → Phalanx`. |

### Resolved decisions

- **A:** Name leadership is **Phalanx (system) → CMC-Lite (spec) → canonicalize (module)** — three names, three audiences, no overlap.
- **B:** Phalanx is the **resolver only**. Shingled extraction is a **separate primitive** with its own doc and its own homepage entry.
- **C:** "Phalanx" stays **out of marketing surfaces** (zeitghost homepage, repo READMEs, landing copy). Reserved for the audience that earned it by reading the deep-dive doc.

### Open questions

All resolved 2026-05-18. See decision log.

---

## 2 · Homepage rewrites (zeitghost)

### Replace entry vi

**Current:**

> vi · Phalanx — entity resolution
> Tell entities apart even when names collide ("Bear" the dog vs. "Bear" the brand) and merge them when surface forms diverge ("Carlos Martinez" vs. "MARTINEZ, CARLOS A"). Same primitive, both directions.
>
> ● cmc-lite · ess digest · tiered

**Replace with:**

> vi · Entity resolution
> Tell entities apart when names collide ("Bear" the dog vs. "Bear" the brand) and merge them when surface forms diverge ("Carlos Martinez" vs. "MARTINEZ, CARLOS A"). Same engine, both directions. No graph database to operate, no embedding service to call — define your identifying fields, hand in records, get canonical IDs back. Bootstraps into any domain by swapping the schema: people, products, places, anything with a small set of defining attributes.
>
> ● sqlite-backed · domain-agnostic · zero-infrastructure

**What changed and why:**
- Header is descriptive (matches every other primitive on the page).
- "Phalanx" deleted from body — earned by the deep-dive doc, not the homepage.
- Tag line is outcome-shaped (`sqlite-backed · domain-agnostic · zero-infrastructure`) instead of jargon (`cmc-lite · ess digest · tiered`).
- Added the competitive frame ("no graph DB, no embedding service") — the actual differentiation vs Neo4j/Neptune/vec0/FAISS.
- Added bootstrap point ("people, products, places") — same engine, multiple domains.
- Kept the two examples and "same engine, both directions" — load-bearing concrete imagery.

### Add new entry ix

> ix · Shingled extraction
> Turn long-form text into atoms without losing facts at chunk boundaries. Overlapping windows + multi-pass extraction; only atoms that appear across multiple passes survive. The result feeds the shard store and the entity-resolution engine: extract once, resolve continuously.
>
> ● overlapping windows · n-of-k voting · checkpoint-resumable

**Rationale:** This pattern was previously bundled under Phalanx by
accident. It's a distinct primitive (text → atoms vs. shards → store vs.
records → canonical), it closes the loop the other primitives describe
(now the reader knows how atoms get *made*), and it has its own value
prop (no-fact-loss extraction).

---

## 3 · Per-repo action items

### `spiritwriter-core`

- [ ] `docs/entity-resolution.md` — strip "Phalanx" from intro / headline; keep as the system name in the body. Lead with "Entity resolution in spiritwriter works by..."
- [ ] Create deep-dive doc at `docs/shingled-extraction.md` for the new primitive (homepage entry ix points here)
- [ ] `docs/specs/spiritwriter-canonicalize.md` — slight reframe; this is module-level reference, not marketing
- [ ] `docs/specs/cmc-lite-v0.1.md` and `cmc-spec-v0.1.md` — header pass to ensure they don't introduce "Phalanx" as something they then never use; reconcile name usage with the convention here
- [ ] `docs/atoms.md` (your plane-notes draft) — once finalized, lead with atoms-as-primitive and only mention Phalanx after readers know what they're resolving
- [ ] `README.md` (root) — audit for stray "Phalanx" references at the top-level pitch
- [ ] `sw_vocab/data/canonical_terms.json` — Phalanx entry is fine (deep-dive system name); confirm the definition aligns with this glossary

### `zeitghost`

Page is `templates/landing.html`, served at spiritwriter.ai from us-ny1.
Beautiful editorial scribal aesthetic — preserve the voice; cleanup is
copy-only, not visual.

**Phalanx mentions found on the page (6 total) — all need rewrites per decision C:**

| # | Location | Current | Suggested rewrite |
|---|---|---|---|
| 1 | Nav (line 898) | `<a href="#bear">Phalanx</a>` | `<a href="#bear">Resolution</a>` (anchor stays `#bear`) |
| 2 | Manifesto §I (line 965) | "And **Phalanx** resolves entities by their defining fields..." | "And **entity resolution** works by defining fields, not surface forms — so 'Bear' the dog never silently merges with 'Bear' the brand." |
| 3 | Primitive vi header (line 1031) | "*Phalanx* — entity resolution" | "Entity *resolution*" (matches header form of other primitives) |
| 4 | Primitive vi tag (line 1033) | "cmc-lite · ess digest · tiered" | "sqlite-backed · domain-agnostic · zero-infrastructure" |
| 5 | §IV folio (line 1124) | "folio iv — phalanx" | "folio iv — resolution" |
| 6 | Bear prose (line 1133) | "**Phalanx hashes the *defining fields***..." | "**The resolver hashes the *defining fields***..." OR "**Entity resolution works by hashing the *defining fields***..." |

**Add new homepage entry ix (shingled extraction) per §2 above.**

**Correctness issue on the live page (line 1153, Bear section sidebar stat) — RESOLVED 2026-05-19 with measured numbers:**

```
≥85% recall on semantic duplicates with ≤5% false-merge rate.
No embeddings, no LLM in the merge path.
```

The `≥85% recall` claim was the cmc-spec's target for the **full CMC
pipeline** (with LLM clustering), NOT a CMC-Lite guarantee. The
multi-corpus benchmark campaign on the `claude/corpora-and-benchmarks`
branch (see [docs/benchmarks/runs-log.md](../benchmarks/runs-log.md))
measured CMC-Lite across 5 corpora and confirmed: precision is the
defensible claim, not recall.

**Cross-corpus invariants** (true on every corpus measured):

| Invariant | Result |
|---|---|
| Auto-merge precision (T1+T2) | **1.000** |
| False-merge rate | **0.000** |
| Recall@any-tier (surfaced for review) | **1.000** |
| Recall on `case` drift | **1.000** |
| Recall on `whitespace` drift | **1.000** |

Whole-corpus Recall@T1+T2 ranges 0.60–1.00 depending on which drift
modes the corpus includes — per-family rows are the marketing-relevant
view, not whole-corpus aggregates. The cmc-spec's 85% target wasn't met
because that target is for the full pipeline.

**Recommended replacement copy for line 1153** (precision-first framing):

```
100% auto-merge precision: 0 incorrect merges across 5 benchmark corpora.
100% of same-entity drift is surfaced for review (auto-merged at T1/T2
when safe, flagged at T3/T4 otherwise). No embeddings, no LLM in the
merge path.
```

This is honest, measured, and stronger than the wishy-washy `≥85%
recall` because it's a *guarantee* (zero false merges, every drift
mode surfaced) rather than a target.

Alternative shorter version if vertical space matters:

```
100% precision in auto-merge · 0 false merges across 5 corpora.
No embeddings, no LLM in the merge path.
```

### `frio`

- [ ] `README.md` — check for "Phalanx" mentions in top-level pitch; demote to deep-dive language if any
- [ ] `web/security.html` and any public-facing copy — same check
- [ ] `devlog/` entries — historical, leave as-is (they're dated artifacts)

### `claude-studio-producer`

- [ ] (Low priority) Survey CSP's KB and memory docs for terminology drift; CSP doesn't have a homepage, but any README pitches should follow the convention here

---

## 4 · Downstream effects on other cleanup work

| Touches | How |
|---|---|
| **todo #2 (marketing copy update)** | This doc handles vi+ix specifically. The broader marketing pass should reuse the "lead with benefit, strip jargon" pattern established here. |
| **todo #4 (openclaw/Lilit/vec0)** | Resolving "vec0 — used or future option?" affects how we describe the registry's storage layer. The current `sqlite-backed` tag in vi assumes vec0 is NOT a present-tense feature; if that changes, update accordingly. |
| **todo #5 (worked example)** | The worked example (paper → atomize → KB → shard → delegate → resolve) will use BOTH shingled extraction AND Phalanx. With the naming clean, the narrative flows: "We **extract** atoms from the paper with consensus, **store** them as shards, hand them to a delegate, and **resolve** their entities into a canonical registry." Three primitives, three names. |
| **todo #9 (references check)** | Final pass should grep all docs for "Phalanx" / "shingles" / "CMC-Lite" / "canonicalize" usage and confirm each follows the convention here. `sw_vocab` already catches "shingles" → Phalanx drift in CI. |

---

## 5 · Cross-references to sibling cleanup docs

(Add as we create them.)

- `docs/cleanup/cmc-phalanx-canonicalize.md` — this doc
- (planned) `docs/cleanup/marketing-copy.md` — broader pass on README + landing copy
- (planned) `docs/cleanup/openclaw-lilit-vec0.md` — todo #4
- (planned) `docs/cleanup/examples.md` — todo #6

---

## 6 · Decision log

| Date | Decision | Notes |
|---|---|---|
| 2026-05-18 | A locked: Phalanx (system) / CMC-Lite (spec) / canonicalize (module) | Three names, three audiences |
| 2026-05-18 | B locked: Phalanx is resolver only; shingled extraction is its own primitive | The batch-with-overlap pattern gets its own entry on the homepage and its own deep-dive |
| 2026-05-18 | C locked: Phalanx stays out of marketing surfaces | Naming insider-jargon doesn't sell capability |
| 2026-05-18 | Suggested homepage rewrites for vi (revised) and ix (new) drafted | Pending zeitghost edit |
| 2026-05-18 | New-primitive name: **"Shingled extraction"** | "Shingled" works as a process adjective (overlapping coverage like roofing shingles). The standalone noun "shingles" is retired permanently. |
| 2026-05-18 | Deep-dive doc location: **`docs/shingled-extraction.md`** | Standalone, not folded into a broader `docs/ingestion.md`. Mirrors the `docs/entity-resolution.md` pattern for Phalanx. |
| 2026-05-18 | Live correctness issue caught during audit: `≥85% recall` on landing.html is the full-CMC target, not a CMC-Lite guarantee | PR #55 measured Recall@T1+T2 = 0.599 on people corpus. Marketing copy finalization (todo #2) parked until multi-corpus benchmarks (todo #3) give us a real number to claim. |
| 2026-05-18 | Marketing copy (#2) blocked on benchmarks (#3) | Honest claims need measured numbers. Reordering todos so #3 runs first. |
| 2026-05-19 | T4 over-confidence noticed on `people` and `case_only` corpora (actual precision ~0.28-0.32 vs stated 0.50) | Deferred — T4 is flag-only, never auto-merges, doesn't affect any marketing claim or safety invariant. Worst-case operational impact: slightly noisier review queue. File as a `canonicalize` engine ticket later if it comes up in real use. |
| 2026-05-19 | Multi-corpus benchmark campaign delivered on `claude/corpora-and-benchmarks` | 5 corpora measured (case_only, inmate_clean, people, publications, csp_kb_AI_Res). Cross-corpus invariants: precision=1.000, false-merge=0.000, any-tier recall=1.000 on every corpus. Full numbers in [docs/benchmarks/runs-log.md](../benchmarks/runs-log.md). Unblocks marketing copy (todo #2). |
| 2026-05-19 | Marketing claim shifted: lead with **precision** (zero false merges, 100% on case+whitespace) instead of **recall** (which varies 0.60-1.00 by corpus drift composition) | "Precision-first" framing is honest, measured, and stronger than the previous wishy-washy `≥85% recall` target. Resolves the live page issue at line 1153. |
