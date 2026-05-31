#!/usr/bin/env python3
"""Demo 6: Phalanx flow — paper → atomize → shard → delegate → resolve.

End-to-end walk of the three named primitives composed together:

  Shingled extraction (text → overlapping chunks → atoms)
            │
            ▼
  Memory shards (atoms → content-addressed bundles → ShardStore)
            │
            ├─────▶ Delegated job (atoms + JobSpec → encrypted package →
            │                       sub-agent hydrates → result shard)
            │
            └─────▶ Phalanx / CanonicalRegistry (entity-shaped atoms →
                                     canonical IDs, dedup across surface forms)

The whole pipeline runs under a single TraceEmitter so a downstream
auditor can reconstruct exactly what happened, who did what, and verify
the chain end-to-end.

Determinism: no LLM, no network. Stage 1 calls the real shingled
chunker (overlapping windows) on the synthetic paper text below. Stage
2 hand-curates the atoms that extraction would produce — this keeps
the demo repeatable without an API key. Stages 3 and 4 are the actual
fabric primitives doing real work on those atoms.

Usage:
    python examples/06_phalanx_flow/run.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from spiritwriter.fabric.canonicalize import (
    CanonicalRegistry, CanonicalSchema,
    # Pre-resolution normalization helpers — see
    # docs/entity-resolution.md § "Normalize before you resolve".
    apply_normalizers, first_initial, strip_punctuation, pipeline,
)
from spiritwriter.fabric.emitter import TraceEmitter, verify_chain
from spiritwriter.fabric.jobs import JobSpec, package_job
from spiritwriter.fabric.runner import hydrate_job, create_result_shard
from spiritwriter.fabric.shard import (
    AtomKind, DecayClass, MemoryShard, ShardAtom,
)
from spiritwriter.fabric.store import ShardStore


# ── The synthetic paper ─────────────────────────────────────────────
#
# Stand-in for "a real paper". Three things we want this text to have:
#   - Enough length (>2000 chars) that the shingled chunker actually
#     produces overlapping windows
#   - Entities mentioned in multiple surface forms (K. Yamamoto vs
#     Kazuhiko Yamamoto) so Phalanx resolution has dedup work to do
#   - Multiple kinds of claims (facts, decisions, conventions) so the
#     curated atom output is varied


PAPER_TEXT = """\
# Fugaku at TOP500: A 2020–2022 Retrospective

Authors: A. Tanaka, K. Yamamoto, S. Hashimoto

## Abstract

The Fugaku supercomputer, based at the RIKEN Center for Computational
Science (R-CCS) in Kobe, Japan, held the #1 position on the TOP500 list
from June 2020 through May 2022. This paper revisits the architectural
choices, the operational decisions, and the team conventions that
sustained that lead for two years across four consecutive TOP500
rankings.

## 1. Introduction

Fugaku is a Japanese supercomputer. Its name derives from Mount Fuji, a
nod to the system's scale ambitions. The full system spans 158,976 nodes
across 432 racks. Tanaka et al. (2019) described the early design
constraints; Yamamoto et al. (2020) reported on the A64FX processor that
powers each node. We chose ARM v8.2-A SVE over x86 for power efficiency,
and the decision held up under sustained load — the system reached 442
PFLOPS on the HPL benchmark.

## 2. Operational decisions

Two decisions stand out in retrospect.

First, we decided to use water cooling over air cooling because the
thermal density of the A64FX racks exceeded what air could remove at
the densities we wanted to pack. The capital cost was higher, but
sustained PUE landed at 1.18, better than the air-cooled comparison
target.

Second, we chose to run a custom Linux kernel patch over upstream
because the system call overhead on the upstream kernel at 158k nodes
caused job-launch storms. Always run a launch dry-run on the full
system before announcing a new scheduler config — this convention
emerged from one such storm in November 2020 and saved us from a
repeat in January 2021.

## 3. The TOP500 runs

| Date     | Rank | HPL (PFLOPS) | Notes                           |
|----------|------|--------------|---------------------------------|
| 2020-06  | 1    | 415.5        | Initial #1                      |
| 2020-11  | 1    | 442.0        | Optimized scheduler             |
| 2021-06  | 1    | 442.0        | Hold (no architectural change)  |
| 2022-05  | 2    | 442.0        | Frontier exceeds Fugaku at 1.1 EFLOPS |

## 4. Authors and Affiliations

Akira Tanaka, RIKEN R-CCS, Kobe, Japan. atanaka@riken.example
Kazuhiko Yamamoto, RIKEN R-CCS, Kobe, Japan. kyamamoto@riken.example
Satoshi Hashimoto, RIKEN R-CCS, Kobe, Japan. shashimoto@riken.example

## 5. Acknowledgments

The R-CCS operations team, particularly K. Yamamoto and A. Tanaka,
ran the production fleet for two years. The TOP500 result is theirs.
"""


# ── Stage 1: shingled chunking (real call) ──────────────────────────


CHUNK_TARGET_CHARS = 2000
CHUNK_OVERLAP_CHARS = 400


def chunk_text(text: str,
               target_chars: int = CHUNK_TARGET_CHARS,
               overlap_chars: int = CHUNK_OVERLAP_CHARS) -> list[str]:
    """Overlapping-window chunking — the shingled-extraction mechanic.

    Same implementation as examples/extract_memory.py. Inlined here so
    the demo is self-contained.
    """
    lines = text.splitlines(keepends=True)
    if not lines:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    i = 0
    while i < len(lines):
        current.append(lines[i])
        current_len += len(lines[i])

        if current_len >= target_chars:
            chunks.append("".join(current))
            # Backtrack for overlap
            overlap: list[str] = []
            overlap_len = 0
            for j in range(len(current) - 1, -1, -1):
                overlap_len += len(current[j])
                overlap.insert(0, current[j])
                if overlap_len >= overlap_chars:
                    break
            current = overlap
            current_len = overlap_len
        i += 1

    if current:
        last = "".join(current)
        if not chunks or len(last) > overlap_chars * 1.5:
            chunks.append(last)
    return chunks if chunks else [text]


# ── Stage 2: atom extraction (curated for determinism) ──────────────


def curated_atoms_from_paper() -> list[ShardAtom]:
    """The atoms a real shingled-extraction pass would produce on the
    paper above. Hand-curated so the demo doesn't need an API key.

    Mix of kinds: FACT (about the system), DECISION (architectural
    choices with rationale), CONVENTION (operational rules). Source-ref
    pins each atom back to the paper for citation. Entity strings are
    intentionally varied for the resolver to dedup in stage 4.
    """
    src = "paper:fugaku-top500-2022"
    return [
        # Facts about Fugaku
        ShardAtom(text="Fugaku is at RIKEN R-CCS in Kobe, Japan.",
                  kind=AtomKind.FACT, entity="Fugaku",
                  key="location", value="Kobe, Japan", source_ref=src),
        ShardAtom(text="Fugaku held TOP500 #1 from June 2020 through May 2022.",
                  kind=AtomKind.FACT, entity="Fugaku",
                  key="top500_rank_1_window", value="2020-06..2022-05",
                  source_ref=src),
        ShardAtom(text="Fugaku reached 442 PFLOPS on HPL.",
                  kind=AtomKind.FACT, entity="Fugaku",
                  key="hpl_peak_pflops", value="442", source_ref=src),
        ShardAtom(text="Fugaku spans 158,976 nodes across 432 racks.",
                  kind=AtomKind.FACT, entity="Fugaku",
                  key="node_count", value="158976", source_ref=src),

        # Architectural decisions
        ShardAtom(text="ARM v8.2-A SVE chosen over x86 for power efficiency.",
                  kind=AtomKind.DECISION, entity="Fugaku",
                  key="isa_choice", value="ARM v8.2-A SVE", source_ref=src),
        ShardAtom(text="Water cooling chosen over air cooling due to A64FX thermal density.",
                  kind=AtomKind.DECISION, entity="Fugaku",
                  key="cooling_choice", value="water", source_ref=src),
        ShardAtom(text="Custom Linux kernel patch chosen over upstream to avoid syscall overhead at 158k nodes.",
                  kind=AtomKind.DECISION, entity="Fugaku",
                  key="kernel_choice", value="custom-patch", source_ref=src),

        # Operational convention
        ShardAtom(text="Always run a launch dry-run on the full system before announcing a new scheduler config.",
                  kind=AtomKind.CONVENTION, entity="rccs-ops",
                  key="scheduler_change_policy", value="dry-run-first",
                  source_ref=src),

        # Author entity atoms — same person, different surface forms
        # (Phalanx will resolve these in stage 4)
        ShardAtom(text="A. Tanaka — author, abstract byline",
                  kind=AtomKind.ENTITY, entity="author:tanaka-a-byline",
                  key="surface_form", value="A. Tanaka", source_ref=src),
        ShardAtom(text="Akira Tanaka — full form, affiliations section",
                  kind=AtomKind.ENTITY, entity="author:tanaka-akira-affiliation",
                  key="surface_form", value="Akira Tanaka", source_ref=src),
        ShardAtom(text="K. Yamamoto — author, abstract byline",
                  kind=AtomKind.ENTITY, entity="author:yamamoto-k-byline",
                  key="surface_form", value="K. Yamamoto", source_ref=src),
        ShardAtom(text="Kazuhiko Yamamoto — full form, affiliations section",
                  kind=AtomKind.ENTITY, entity="author:yamamoto-kazuhiko-affiliation",
                  key="surface_form", value="Kazuhiko Yamamoto", source_ref=src),
        ShardAtom(text="S. Hashimoto — author, abstract byline",
                  kind=AtomKind.ENTITY, entity="author:hashimoto-s-byline",
                  key="surface_form", value="S. Hashimoto", source_ref=src),
        ShardAtom(text="Satoshi Hashimoto — full form, affiliations section",
                  kind=AtomKind.ENTITY, entity="author:hashimoto-satoshi-affiliation",
                  key="surface_form", value="Satoshi Hashimoto", source_ref=src),
    ]


# ── Stage 3: Phalanx entity resolution ──────────────────────────────


# Per-field normalizers for author records. Declared once, reused for
# every candidate via apply_normalizers() below. See
# docs/entity-resolution.md § "Normalize before you resolve" for the
# why-and-when; the helpers themselves (first_initial,
# strip_punctuation, pipeline) live in spiritwriter.fabric.canonicalize.
#
# After this normalization, the byline ('K. Yamamoto') and affiliation
# ('Kazuhiko Yamamoto') forms produce identical ESS digests — so the
# merge happens at T1_EXACT, not via the fuzzy fallback. The schema's
# fuzzy_fields are configured (to demonstrate how an app would express
# them) but aren't actually exercised on this corpus.
AUTHOR_NORMALIZERS = {
    "first_name": first_initial,                          # 'K.' / 'Kazuhiko' → 'K'
    "last_name":  pipeline(str.upper, strip_punctuation), # 'O\'Brien' → 'OBRIEN'
}


def author_candidates_from_paper() -> list[dict]:
    """The structured author records the resolver consumes. Surface
    forms vary; the underlying entity is the same person.

    A real ingestion pipeline would derive these from the ENTITY atoms
    above (or directly from a structured authors section). Showing them
    explicitly here so the dedup story is obvious."""
    return [
        # Byline forms (initial + last)
        {"first_name": "A.", "last_name": "Tanaka",
         "affiliation": "RIKEN R-CCS"},
        {"first_name": "K.", "last_name": "Yamamoto",
         "affiliation": "RIKEN R-CCS"},
        {"first_name": "S.", "last_name": "Hashimoto",
         "affiliation": "RIKEN R-CCS"},

        # Full forms from the affiliations section — same people, fuller
        # first names. Normalization + fuzzy matching should resolve
        # these to the byline entries.
        {"first_name": "Akira", "last_name": "Tanaka",
         "affiliation": "RIKEN R-CCS"},
        {"first_name": "Kazuhiko", "last_name": "Yamamoto",
         "affiliation": "RIKEN R-CCS"},
        {"first_name": "Satoshi", "last_name": "Hashimoto",
         "affiliation": "RIKEN R-CCS"},

        # Acknowledgments mentions K. Yamamoto and A. Tanaka again
        # (same as byline forms — should be exact ESS match → T1)
        {"first_name": "K.", "last_name": "Yamamoto",
         "affiliation": "RIKEN R-CCS"},
        {"first_name": "A.", "last_name": "Tanaka",
         "affiliation": "RIKEN R-CCS"},
    ]


def resolve_authors(registry: CanonicalRegistry,
                    candidates: list[dict],
                    tracer: TraceEmitter) -> dict[str, list[tuple[dict, str]]]:
    """Resolve each candidate against the registry; emit a trace event
    per resolution. Returns a {canonical_id: [(original_candidate, tier), ...]}
    map showing how many surface forms collapsed into each entity.

    Each candidate is pre-normalized via AUTHOR_NORMALIZERS before
    resolution — that's where 'K. Yamamoto' and 'Kazuhiko Yamamoto'
    become indistinguishable to the ESS digest.
    """
    by_canon: dict[str, list[tuple[dict, str]]] = {}
    for i, cand in enumerate(candidates):
        normalized = apply_normalizers(cand, AUTHOR_NORMALIZERS)
        result = registry.resolve(normalized)
        canon = registry.upsert(
            normalized, result,
            source_name="paper:fugaku-top500-2022",
            source_id=f"author-mention-{i:02d}",
            raw=cand,  # keep the original surface form for audit
        )
        tracer.emit("entity_resolved",
                    candidate_idx=i,
                    surface_form=f"{cand['first_name']} {cand['last_name']}",
                    normalized=f"{normalized['first_name']} {normalized['last_name']}",
                    canonical_id=canon[:12] + "…",
                    tier=result.tier.value,
                    confidence=round(result.confidence, 3))
        by_canon.setdefault(canon, []).append((cand, result.tier.value))
    return by_canon


# ── Stage 4: delegate summarization ─────────────────────────────────


def delegate_summarization(store: ShardStore,
                           source_content_shard_id: str,
                           content_atoms: list[ShardAtom],
                           tracer: TraceEmitter) -> tuple[str, str, MemoryShard]:
    """Package the paper's atoms as a delegated summarization job.
    The "sub-agent" here runs inline (no spawning) — it hydrates, then
    creates a result shard. The result shard's parent_shard_id pins to
    `source_content_shard_id` (the original plaintext content shard
    from stage 2) so the chain of custody is reconstructible regardless
    of the encrypted job-internal shards.

    Returns (entitlement_token_id, pkg_content_shard_id, result_shard).
    `pkg_content_shard_id` is the encrypted content shard that
    package_job() created internally — different bytes/hash from the
    stage-2 plaintext shard, exposed here only for the trace audit.
    """
    spec = JobSpec(
        prompt="Summarize the Fugaku retrospective in 2 paragraphs.",
        budget_usd=0.50,
        constraints={
            "output_format": "prose-only-no-bullets",
            "audience": "technical-but-non-specialist",
            "citation_style": "inline-source_ref-markers",
        },
    )

    pkg = package_job(
        store=store,
        content_atoms=content_atoms,
        job_spec=spec,
        agent_id="orchestrator:phalanx-flow-demo",
        granted_to="worker:summarizer-1",
        tracer=tracer,
    )

    # Sub-agent side: hydrate via the entitlement
    tracer.emit("worker_starting", token_id=pkg.entitlement_token.token_id,
                role="summarizer")
    job = hydrate_job(store, pkg.spawn_task_text(), tracer=tracer)

    # The "summarization" — for the demo we just produce a fixed
    # summary atom. A real worker would call the LLM here.
    summary_text = (
        "Fugaku, built at RIKEN R-CCS in Kobe, held TOP500 #1 from June "
        "2020 through May 2022 by sustaining 442 PFLOPS on HPL across "
        "158,976 A64FX nodes. Two operational choices held up under "
        "load: water cooling (chosen for A64FX thermal density, "
        "delivered 1.18 sustained PUE) and a custom Linux kernel patch "
        "(chosen to dodge upstream syscall overhead at 158k nodes). "
        "[paper:fugaku-top500-2022]"
    )

    # Lineage: pin parent_shard_id at the stage-2 plaintext content
    # shard (not the encrypted job-internal one package_job built).
    # That's the meaningful predecessor — the atoms the paper was
    # extracted into. Since 0.8.2, create_result_shard takes
    # parent_shard_id as a kwarg, so we don't have to mutate-then-re-put.
    result = create_result_shard(job, {
        "budget": {"spent_usd": 0.0, "budget_usd": spec.budget_usd},
        "outputs": [{"type": "summary", "ref": summary_text}],
    }, parent_shard_id=source_content_shard_id)
    store.put(result)

    tracer.emit("worker_completed",
                token_id=pkg.entitlement_token.token_id,
                result_shard_id=result.shard_id,
                summary_chars=len(summary_text))

    return pkg.entitlement_token.token_id, pkg.content_shard_id, result


# ── Main ────────────────────────────────────────────────────────────


def main(output_dir: Path | None = None) -> int:
    # Force UTF-8 stdout so the box-drawing + arrow characters render on
    # Windows cp1252 consoles too (Linux/macOS already default to UTF-8).
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="demo06_"))
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=== Demo 6: Phalanx flow — paper → atomize → shard → delegate → resolve ===\n")
    print(f"Output dir: {output_dir}\n")

    trace_path = output_dir / "trace.jsonl"
    store = ShardStore(output_dir / "shards")
    registry_path = output_dir / "authors.db"

    tracer = TraceEmitter(
        run_id="run-phalanx-flow-demo",
        agent_id="orchestrator:phalanx-flow-demo",
        out_path=trace_path,
    )

    # ── Stage 1: shingled chunking ─────────────────────────────────
    print("── Stage 1: shingled chunking ─────────────────────────────")
    print(f"Paper length: {len(PAPER_TEXT)} chars")
    chunks = chunk_text(PAPER_TEXT)
    print(f"Chunks produced: {len(chunks)} "
          f"(target={CHUNK_TARGET_CHARS} chars, overlap={CHUNK_OVERLAP_CHARS} chars)")
    for i, chunk in enumerate(chunks):
        head = chunk.lstrip().splitlines()[0][:60]
        print(f"  chunk {i+1}: {len(chunk):>4} chars · starts: {head!r}")
    tracer.emit("shingled_chunking_completed",
                paper_chars=len(PAPER_TEXT),
                chunks=len(chunks),
                target_chars=CHUNK_TARGET_CHARS,
                overlap_chars=CHUNK_OVERLAP_CHARS)
    print()

    # ── Stage 2: atom extraction (curated) ─────────────────────────
    print("── Stage 2: atom extraction (curated for determinism) ─────")
    atoms = curated_atoms_from_paper()
    by_kind: dict[str, int] = {}
    for a in atoms:
        by_kind[a.kind.value] = by_kind.get(a.kind.value, 0) + 1
    kinds_summary = ", ".join(f"{n} {k}" for k, n in sorted(by_kind.items()))
    print(f"Atoms produced: {len(atoms)} ({kinds_summary})")

    content_shard = MemoryShard(
        atoms=atoms,
        scope="paper:fugaku-top500-2022",
        origin="orchestrator:phalanx-flow-demo",
        decay_class=DecayClass.STABLE,
        tags=["paper", "extracted-atoms"],
    )
    content_ref = store.put(content_shard)
    print(f"Content shard: {content_ref.shard_id[:16]}…  "
          f"(scope={content_shard.scope}, decay={content_shard.decay_class.value})")
    tracer.emit("content_shard_stored",
                shard_id=content_ref.shard_id,
                atom_count=len(atoms),
                kinds=by_kind)
    print()

    # ── Stage 3: Phalanx entity resolution ─────────────────────────
    print("── Stage 3: Phalanx — resolve author entities ─────────────")
    schema = CanonicalSchema(
        name="author",
        ess_fields=["last_name", "first_name"],
        fuzzy_fields={"last_name": 0.90, "first_name": 0.50},
        context_fields=["affiliation"],
    )
    with CanonicalRegistry(registry_path, schema) as registry:
        candidates = author_candidates_from_paper()
        print(f"Author mentions to resolve: {len(candidates)}")
        by_canon = resolve_authors(registry, candidates, tracer)
        print(f"Resolved into {len(by_canon)} canonical entities:")
        for canon_id, occurrences in by_canon.items():
            forms = sorted({f"{c['first_name']} {c['last_name']}"
                            for c, _ in occurrences})
            tiers = [t for _, t in occurrences]
            print(f"  {canon_id[:12]}…  ({len(occurrences)} mention(s), "
                  f"tiers={tiers})")
            for f in forms:
                print(f"    └─ {f}")
    print()

    # ── Stage 4: delegate summarization ────────────────────────────
    print("── Stage 4: delegate summarization to a sub-agent ─────────")
    token_id, pkg_content_id, result_shard = delegate_summarization(
        store, content_ref.shard_id, atoms, tracer)
    print(f"Entitlement token:    {token_id[:16]}…")
    print(f"Encrypted job-content: {pkg_content_id[:16]}…  "
          f"(internal to package_job, distinct from stage-2 plaintext)")
    print(f"Result shard:         {result_shard.shard_id[:16]}…  "
          f"(parent={result_shard.parent_shard_id[:12]}… → stage-2 content)")
    print()

    # ── Verification ───────────────────────────────────────────────
    print("── Verification ───────────────────────────────────────────")
    events = [json.loads(line) for line in trace_path.read_text(
        encoding="utf-8").splitlines() if line.strip()]
    assert verify_chain(events), "trace chain broken"
    print(f"  [ok] trace chain intact ({len(events)} events)")

    event_types = {e["type"] for e in events}
    expected_types = {
        "shingled_chunking_completed",
        "content_shard_stored",
        "entity_resolved",
        "entitlement_granted",
        "job_packaged",
        "worker_starting",
        "job_started",
        "shard_decrypted",
        "capability_checked",
        "worker_completed",
    }
    missing = expected_types - event_types
    assert not missing, f"missing event types: {missing}"
    print(f"  [ok] all {len(expected_types)} expected event types present")

    # Result shard's parent points at the content shard
    assert result_shard.parent_shard_id == content_ref.shard_id, \
        "result shard parent_shard_id should point at content shard"
    print(f"  [ok] result shard parent_shard_id pins to source content shard")

    # The two "Yamamoto" forms collapsed into one canonical entity
    yamamoto_canons = {cid for cid, occ in by_canon.items()
                       if any(c["last_name"] == "Yamamoto" for c, _ in occ)}
    assert len(yamamoto_canons) == 1, \
        f"K. Yamamoto and Kazuhiko Yamamoto should resolve to one entity, " \
        f"got {len(yamamoto_canons)}"
    print(f"  [ok] Phalanx merged 'K. Yamamoto' + 'Kazuhiko Yamamoto' → 1 canonical id")

    # 8 author mentions → 3 canonical entities (Tanaka, Yamamoto, Hashimoto)
    assert len(by_canon) == 3, \
        f"expected 3 canonical authors, got {len(by_canon)}"
    print(f"  [ok] 8 author mentions → 3 canonical entities")
    print()

    print("=== Demo complete ===")
    print(f"Trace:      {trace_path}")
    print(f"Shards:     {store.root}")
    print(f"Registry:   {registry_path}")
    print()
    print("Each stage emitted under one TraceEmitter, hash-chained, "
          "verifiable end-to-end.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
