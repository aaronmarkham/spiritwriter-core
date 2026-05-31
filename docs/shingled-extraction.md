# Shingled Extraction

Turning long-form text into atoms without losing facts at chunk boundaries.

If you have a 50-page PDF, a 200-message chat transcript, or any input that won't fit in a single LLM context window, you can't just chunk-and-extract — facts that straddle a chunk boundary get split (and often dropped) by the extractor on each side. Shingled extraction solves this with two ideas borrowed from roofing: **overlapping windows** so every fact sits inside at least one complete chunk, and **multi-pass consensus voting** so only atoms that survive across passes get stored.

This is a separate primitive from [entity resolution](entity-resolution.md). Resolution decides whether two atoms refer to the same entity; extraction decides which atoms exist in the first place. Together they form the extract → resolve loop: shingled extraction makes atoms from text, the resolver dedupes the entities those atoms talk about.

## How it works

```
                 ┌─────────── input text ────────────┐
                 │                                   │
                 │      ████████████                 │  chunk 1
                 │            ████████████           │  chunk 2  (overlaps chunk 1)
                 │                  ████████████     │  chunk 3  (overlaps chunk 2)
                 └───────────────────────────────────┘

       Each chunk extracted independently (pass A, pass B, …)
       Atoms compared across passes via normalized signature
       Survivors (≥N matches) become canonical; orphans drop
```

Concretely (using `examples/extract_memory.py` as the reference implementation):

1. **Chunk** the input with overlap — default `CHUNK_TARGET_CHARS=2000` and `CHUNK_OVERLAP_CHARS=400`. The overlap is the shingle window: any fact ≤400 chars long sits fully inside at least two chunks.
2. **Extract atoms from each chunk independently**, repeating for N passes (default 2, configurable via `--passes`). Each pass uses the same prompt but is a separate LLM call — non-determinism becomes a feature, since atoms that survive across passes are the ones the model agrees on.
3. **Signature each atom** as `kind|entity|key` (normalized: lowercase, whitespace-collapsed). Fuzzy match on the entity and key tokens (Jaccard) so "dog name" and "Bear's name" can match without being string-identical.
4. **Keep atoms that appear in ≥N passes**; drop orphans. N defaults to 2 (the n-of-k voting threshold).
5. **Checkpoint per pass.** Raw atoms from each pass are written to disk before consensus runs — so a crash mid-extraction doesn't lose work. Resume picks up where the last checkpoint landed.

The atoms that survive become `ShardAtom`s in a `MemoryShard`; from there they flow into the [shard store](memory-shards.md) and (if you've configured it) the [entity resolver](entity-resolution.md).

## Why "shingled"

Roofing shingles overlap on purpose — the overlap is the waterproofing. The same idea applies to text: the overlap is what prevents fact-loss at chunk boundaries. "Shingled" describes the process (overlapping coverage); the standalone noun "shingles" is retired (medical connotation, and it implied a thing rather than a method).

The variable names in `examples/extract_memory.py` still say "shingle" (e.g. `CHUNK_OVERLAP_CHARS` is commented as `~100 token overlap (shingle window)`). That's historical and will get renamed in a future cleanup; the documented term going forward is "shingled extraction" or "shingle window".

## When to use it

- **Long inputs.** Anything that won't fit in a single LLM context. PDFs, long transcripts, knowledge dumps.
- **High-cost extraction where loss matters.** Multi-pass consensus catches the model's bad days; one pass alone often misses ~5–10% of extractable atoms on noisy input.
- **Inputs you might re-extract later.** The checkpoint-per-pass design means you can stop mid-run, change the prompt, and resume from the last good checkpoint.

## When not to use it

- **Short inputs that fit in one prompt.** No boundary problem to solve; one pass is fine, multi-pass is just extra cost.
- **Realtime / per-message extraction.** Multi-pass means multi-cost and multi-latency. For one-shot extraction (e.g. on every incoming chat message), use a single pass and accept the recall loss.
- **Already-structured input.** If the source is JSON, CSV, or anything machine-parseable, just parse it. Shingled extraction is for prose where the LLM is doing the structuring.

## Cost characteristics

The reference implementation costs about **$0.03 per pass** over a memory-folder-sized corpus (a few dozen markdown files, ~5–10K tokens total). Three passes = ~$0.09. Cheap enough to make the consensus pattern the default even for one-off extractions.

The dominant cost is the LLM calls per chunk, not the overlap — a 400-char overlap on a 2000-char chunk only re-extracts ~20% of input bytes across boundary chunks, and most of those are filler tokens that the model breezes through.

## The shape of an extracted atom

Each atom the extractor produces is a [`ShardAtom`](atoms.md) with the full `(entity, key, value)` triple filled in, plus a `confidence` score from the LLM. Decay class is classified per-atom (`PERMANENT`, `STABLE`, `ACTIVE`, `SESSION`) so storage layers can prune appropriately. The consensus-passing atoms become a `MemoryShard`; orphans get logged to a per-pass raw-atoms file in case you want to audit what was dropped.

For the atom shapes themselves and the kinds (`FACT`, `DECISION`, `CONVENTION`, …), see [`atoms.md`](atoms.md). For how the resulting shards get stored and hydrated back into prompts, see [`memory-shards.md`](memory-shards.md). For how the entities those atoms describe get deduped at query time, see [`entity-resolution.md`](entity-resolution.md).

## Reference implementation

[`examples/extract_memory.py`](../examples/extract_memory.py) is the working implementation. It targets markdown extraction (single file or directory of files), but the chunking + multi-pass + consensus logic is generic — point it at anything text-shaped.

Key knobs:

```python
CHUNK_TARGET_CHARS = 2000   # ~500 tokens per chunk
CHUNK_OVERLAP_CHARS = 400   # the shingle window — atoms at chunk
                            # boundaries appear in ≥2 chunks
MAX_TOKENS_PER_CALL = 4000  # LLM max output tokens per chunk
```

CLI:

```bash
# Required: --input (file or dir) and --store (where the shard lands)
python examples/extract_memory.py --input ./notes/ --store ./shards/
python examples/extract_memory.py --input ./MEMORY.md --store ./shards/

# Common options
python examples/extract_memory.py --input ./notes/ --store ./shards/ --passes 3
python examples/extract_memory.py --input ./notes/ --store ./shards/ --dry-run
python examples/extract_memory.py --input ./notes/ --store ./shards/ --force
python examples/extract_memory.py --input ./notes/ --store ./shards/ --regex
python examples/extract_memory.py --input ./notes/ --store ./shards/ \
    --model claude-sonnet-4-6 --price-in 3.0 --price-out 15.0
```

The LLM path uses Anthropic Claude via the `anthropic` SDK and
`spiritwriter.secrets.get_api_key("ANTHROPIC_API_KEY")` (keychain first,
then env-var fallback). Default model is `claude-haiku-4-5` for cost;
override with `--model` plus matching `--price-in` / `--price-out` to
keep the cost report accurate. `--regex` runs a free offline fallback
via `spiritwriter.fabric.extract.extract_atoms` — noisier, but no API
key required.

## End-to-end worked example

[`examples/06_phalanx_flow/`](../examples/06_phalanx_flow/) runs the
full pipeline: a synthetic paper is shingled-chunked, atomized,
bundled into a memory shard, handed to a sub-agent via a delegated
job, and the entities mentioned in it are resolved through Phalanx —
all under a single trace whose chain verifies end-to-end. Deterministic
(no LLM, no network) so it's safe to run anywhere.

## Related reading

- [`atoms.md`](atoms.md) — the `ShardAtom` primitive and the AtomKind enum that extraction populates
- [`memory-shards.md`](memory-shards.md) — how extracted atoms get bundled, content-addressed, and persisted
- [`entity-resolution.md`](entity-resolution.md) — the resolver that turns extracted atoms into canonical entities
- [`benchmarks/runs-log.md`](benchmarks/runs-log.md) — measured precision/recall across the corpora the resolver consumes
- [`specs/cmc-lite-v0.1.md`](specs/cmc-lite-v0.1.md) — the spec that bundles multi-pass consensus with ESS + tiered resolution as the CMC-Lite pipeline
