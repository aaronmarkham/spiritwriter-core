# Changelog

All notable changes to `spiritwriter` are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project follows pre-1.0 SemVer — **minor** for breaking changes, **patch** for additive/non-breaking changes.

Entries before 0.8.0 are not backfilled; consult `git log` for earlier history. Releases through 0.8.3 were published under the distribution name `spiritwriter-core`.

## [0.10.3] — 2026-08-28

Two `NetworkResolver` backends this cycle — a new **S3 backend** (spiritwriter-core#98) and the **IPFS/Kubo backend brought to correctness parity** with it (spiritwriter-core#99) — plus a **shared resolver contract fix** threaded up through the store API and the protocol so a transport failure surfaces instead of being mistaken for "absent". The last released version was 0.10.2, so both land under this single increment. Opt-in and additive — a `ShardStore` is byte-identical to 0.10.2 until you pass `resolver=...`. Patch bump under the pre-1.0 convention.

### Added
- **S3 storage backend** (`spiritwriter.fabric.backends.s3.S3Backend`) — implements the existing `NetworkResolver` protocol against an S3 bucket, the object-store analog of the IPFS/Kubo backend. Behind the same local-first `ShardStore` (L1 local cache → L2 backend). Ships as the opt-in `s3` extra (`pip install 'spiritwriter[s3]'`, adds boto3); the core library stays dependency-light and never imports boto3 unless the backend is constructed. Object keys mirror `ShardStore`'s git-object layout under a configurable `bucket`/`prefix`, with `.json` / `.enc.json` / `.sealed.json` namespaces for plaintext, encrypted, and sealed shards. Because keys are a deterministic function of `shard_id`, the backend needs **no** persisted `cid_map.json` (unlike IPFS): the protocol's `cid` slot *is* the S3 key. Constructed with `bucket`/`prefix`/`region`/`endpoint_url` or `S3Config.from_env()` (`SPIRITWRITER_S3_BUCKET`/`_PREFIX`/`_REGION`/`_ENDPOINT`); accepts a pre-configured boto3 `client=` for connection-pool / timeout / retry tuning and for stub-based tests.
- **Fails loud on operator error, not silently empty.** A missing or misconfigured bucket raises `S3ConfigurationError` rather than reading as an empty store — a typo'd `SPIRITWRITER_S3_BUCKET` surfaces immediately instead of masquerading as "no shards". Only a genuine object-not-found (`NoSuchKey`/`404`) becomes a `None` result on the `resolve*` path.
- `pin`/`unpin` on the S3 backend are documented no-ops that report success — S3 durability is a bucket-level property (lifecycle/versioning), not a per-object pin, so there is nothing to pin; `unpin` notably does **not** delete (use an explicit S3 delete or lifecycle rule). `ShardLocation.local` is `False` for S3 objects (an L2/remote store), so a local-first resolver does not wrongly skip the network fallback.

### Fixed
- **Resolver contract, consistent across both backends and up through the store API: transport failures propagate; `None` means genuine absence only.** `ShardStore.get`/`get_encrypted`/`get_sealed`/`resolve`/`resolve_ref` and the `NetworkResolver` protocol now document that `resolve*` raise `NetworkUnavailable`/`NetworkTimeout` (and `S3ConfigurationError` for S3) on a transport/config failure and return `None` only when a shard was genuinely never published — a transient blip is never collapsed into "absent", which in a hosted worker (e.g. an async Lambda resolving through `get`) would be a silent false-success. Batch helpers (`resolve_many`/`by_scope`/`hydrate`) therefore fail loud on a transient error rather than return a misleadingly-partial list. (Previously the backends swallowed these to `None`; the fix stopped at the backend layer, leaving the public store API and protocol still promising `None` — this threads it up. spiritwriter-core#99)
- **IPFS/Kubo backend parity with S3.** `resolve` / `resolve_sealed` / `resolve_encrypted` / `resolve_manifest` propagate transport errors instead of swallowing them (the genuine not-found signal is a `cid_map` miss — the IPFS analog of `NoSuchKey` — which still returns `None`). `publish` / `publish_sealed` / `publish_encrypted` / `publish_public` set `ShardLocation.local=False` (IPFS objects are remote, not in the local `ShardStore`), and an idempotent re-publish now reports `pinned` per `pin_by_default` instead of a hardcoded `True` (which lied when `pin_by_default=False`). `resolve_manifest` verifies the fetched bytes **re-derive to the requested CID** (`_recompute_cid` mirrors `publish`'s Kubo `add --only-hash` derivation; `IntegrityError` on mismatch) and additionally requires the declared `manifest_id` to match the identity recomputed from the parsed content — a stronger anchor than the S3 key check, since on IPFS the CID *is* the content hash. Honest residual: the recompute uses the same Kubo node that served the bytes, so it defends against wrong-CID / truncation / corruption / a rewritten manifest, but not a node that lies consistently on both `cat` and `add`. The manifest feeds the receipt/lineage path, so this is where the integrity guarantee must not have a hole.

### Documentation
- `docs/network-distribution.md` gains an **S3 backend** section (install, configure, storage layout, and the honest S3-vs-IPFS semantic differences) and a **Choosing a backend: S3 vs IPFS** comparison. README gains the `s3` install-matrix line, the `backends/s3.py` tree entry, and the `test_s3_backend.py` test invocation. (spiritwriter-core#98)

### Tests
- `tests/test_s3_backend.py` — offline coverage (fake S3 client, no AWS): `NoSuchBucket`/misconfig raises `S3ConfigurationError`, transport/permission errors propagate from every `resolve*`, genuine `NoSuchKey` → `None`, manifest integrity mismatch raises, `from_env` region/endpoint + empty-string→`None`.
- `tests/test_ipfs_backend_unit.py` — offline unit tests (no Kubo node) using an in-memory `FakeKubo` injected at the `_add_bytes` / `_cat_cid` boundary: a simulated transient/timeout error propagates from every `resolve*` (rather than returning `None`), a genuine not-found (`cid_map` miss) returns `None`, `publish*` sets `local=False`, an idempotent re-publish honors `pin_by_default`, and a manifest that fails the requested-CID anchor raises `IntegrityError`. The Kubo-gated integration tests in `test_ipfs_backend.py` are unchanged and still skipped without a node.

## [0.10.2] — 2026-08-20

Attribute folding for the entity resolver. Additive API, **no database change** — the SQLite schema a registry creates is byte-identical to 0.10.1 (verified object-for-object), so there is no migration, no new table or column, and nothing to back up before upgrading. Patch bump under the pre-1.0 convention. Also ships the design notes behind the resolver, golden vectors pinning `shard_id`, and harness fixes that make the repo's own documented setup and benchmark commands work as written.

### Added
- **Attribute folding** (`fold_entity_fields`) — a T1/T2 match now reconciles the candidate's fields into the entity's stored blob. Previously `upsert()` bumped `last_seen` and `source_count` and nothing else, so the canonical record was frozen at whatever the *first* sighting carried: a later richer record contributed nothing, a later contradicting one raised no signal. Nothing was lost — `sightings` kept every record — but reconciliation fell to every consumer. Pure function: mutates neither argument, returns a fresh dict. Identity fields are never rewritten (that would desynchronize the stored `ess_digest` from the fields it hashes); disagreement there is reported with `reason="identity"`.
- **`ResolutionPolicy`** — the single place fold behavior is decided, every option a total order or a pure predicate, so repeated runs over the same records produce byte-identical stored fields. `precedence="richest"` degrades to `keep-first` on a tie rather than choosing arbitrarily. `conflicts="keep-all"` additionally records suppressed values under a `__conflicts__` key *inside the existing `ess_fields` JSON blob* — a value-shape change on an opt-in path, not a schema change.
- **Exact dry run** — `field_conflicts()` and `CanonicalRegistry.plan()` are the non-mutating twins of the fold, calling the same pure function the write path calls, so a plan cannot drift from the write it predicts. `canonicalize_batch()` gains `dry_run=` and a **timestamp-free** `ResolutionReport`, making two reports diffable across runs.
- **`strip_accents()`** — a normalization helper that drops diacritics while keeping the base letters. `normalize_name` does not do this (`[^\w\s-]` is Unicode-aware, so `Í` survives `.upper()`), and fuzzy matching does not rescue it: `GARCÍA`/`GARCIA` scores 0.833, `MUÑOZ`/`MUNOZ` 0.800, `PEÑA`/`PENA` 0.750 — all under a strict threshold, because on a short surname one substitution is a large fraction of the string. So the same person arriving from two sources that disagree about accents becomes two canonical entities, silently. Compose it — `pipeline(strip_accents, normalize_name)` — rather than expecting `normalize_name` to change: stripping accents there would alter the digest of every accented entity already stored, orphaning it from records normalized the new way.
- The registry warns when `combine_fields` names a field the fold can never reach — one the schema does not declare, or an identity field (never folded, since rewriting one would desynchronize the stored `ess_digest`). Naming an unreachable field is otherwise accepted silently and simply does nothing, so a typo reads as "combining never applied" rather than as a mistake.

### Changed
- `upsert()` on a T1/T2 match now fills entity fields that were **empty**, where before it left them frozen at first sight. It never overwrites a value that already exists — on a genuine conflict the stored value wins by default and the disagreement is reported rather than silently absorbed. This is the one behavior change; everything else is opt-in.

  It is not inert for later resolution: `_context_resolve` reads stored context fields and `_fuzzy_resolve` reads stored fuzzy fields, so a field that was empty (and therefore skipped) starts participating once filled. Measured on a 360-record corpus with 45% context-field dropout, resolution was unaffected — identical tier histogram, identical cluster count, zero false merges and zero split entities either way — while canonical records carrying `facility` went from 32/60 to 59/60 and `booking_date` from 26/60 to 60/60. The gain is data completeness on the canonical record; it is not a matching improvement.

### Documentation
- **`docs/resolver-design.md`** — why the resolver is shaped the way it is, and what was deliberately not built. The decision the rest follows from is that an `EntitySenseSig` **retains its `(key, normalized_value)` pairs** rather than collapsing identity to an opaque id: an opaque id answers only *equal or not*, while retained pairs also support the graded question `overlap()` asks, which is what makes tiered thresholds possible at all. Each choice is set against [docling-graph](https://github.com/docling-project/docling-graph)'s `core/merge/`, which solves the same problem from the batch graph-fusion side — including where that design is ahead, and why the conflict-variant and anti-merge work was cut rather than shipped. (spiritwriter-core#96)
- `docs/entity-resolution.md` gains an accent-normalization section — the fuzzy scores that show why matching does not rescue `GARCÍA`/`GARCIA`, and why `strip_accents` is composed rather than folded into `normalize_name`. (spiritwriter-core#93)

### Tests
- **Golden vectors pin `shard_id`.** `shard_id` is a content address other systems store and compare, and nothing pinned it — twelve test classes in `test_shard.py` and not one literal address, while `test_orbit.py` pins five digests for exactly this reason. Both `fabric.canonicalize` and `fabric.orbit` now import `_canonical_json` and `_sha256` from `shard`, so a change made to suit an ordering or padding requirement in either consumer would silently re-address every shard ever emitted, with no test failing. Two vectors — a single atom and a full report — close that. (spiritwriter-core#95)
- **The 20 async tests no longer fail when `pytest-asyncio` is absent.** They carry `@pytest.mark.asyncio` and the plugin ships only in the `dev` extra, while `CLAUDE.md` documented setup as `pip install -e .` — so following the repo's own instructions produced 20 failures that read as breakage in the Anthropic provider. CI installs `[dev,sealed,network]` and never saw them. `tests/conftest.py` now skips those tests with a reason naming the install command, and `[tool.pytest.ini_options]` registers the `asyncio` and `ipfs` markers. (spiritwriter-core#92)
- **The documented benchmark command actually collects benchmarks.** Benchmark files are named `bench_*.py`, which pytest's default `python_files` does not match, so `pytest benchmarks/` collected nothing and exited clean. A `benchmarks/pytest.ini` fixes the pattern, scoped so it applies only when benchmark paths are on the command line — pytest loads exactly one config, chosen by those paths, so an unscoped setting would have changed how the main suite collects. (spiritwriter-core#92)

## [0.10.1] — 2026-08-18

Adds exact canonicalization under declared symmetry, and the harness that measures what it is for. Additive throughout — patch bump under the pre-1.0 convention.

### Added
- **`spiritwriter.fabric.orbit`** — exact canonical forms for structures that carry a declared symmetry, so equivalence is a byte comparison rather than a threshold decision. `canonical_cycle` / `cycle_digest` give the dihedral (rotation + optional reflection) form of a ring, `anchor_cycle` rotates to a distinguished element without losing traversal direction, and `canonical_under` / `orbit_digest` generalize both to any caller-supplied permutation group. `least_rotation` is Booth's algorithm, O(n) and correct under repeated elements. The exact counterpart to `fabric.canonicalize`, which resolves entities by fuzzy similarity: here the symmetry is declared and the answer has no confidence score attached. (spiritwriter-core#89)
  - `perms` is treated as a **generating set** and closed under composition (`permutation_closure`) before a representative is chosen — passing generators to a canonicalizer that only scans the supplied permutations sends members of one orbit to different representatives, silently.
  - Elements that canonical JSON cannot serialize (`bytes`, `datetime`, domain objects) are supported through a `key=` surrogate function, the way `sorted` takes one; without it the `TypeError` names the offending element and index.
  - Digests carry a versioned domain prefix, so an orbit digest never collides with a plain content hash of the same bytes.
  - `padded(width)` builds a `key=` that keeps bounded non-negative integers in numeric order, since lexicographic byte ordering otherwise sorts them as text (`10` before `9`). The width bound is enforced — an overflowing value raises rather than sorting silently into the wrong place. Dates need no helper: ISO-8601, which `normalize_date` already emits, sorts correctly as text.

### Benchmarks
- **`benchmarks/eval/structural_accuracy/`** — new eval suite measuring canonicalization against similarity scoring on records whose variation is structural rather than textual. The companion to `ess_accuracy`, which mutates fields (case, whitespace, typos, unicode) and where scoring does well. Across 200 pairs over three corpora — undirected rings, directed cycles, unordered groupings — the rule settles 114/114 same-structure pairs with 0 wrong merges, while the best similarity threshold that makes no wrong merges catches **0**. The classes are not separable at any cutoff: pairs that must stay apart score *higher* on average (0.729) than rotations (0.690), permutations (0.610), or reflections (0.546), because rewriting a structure changes the text a great deal while altering the structure changes it hardly at all. Negatives are deliberately hard — same members, different arrangement — since negatives drawn from unrelated structures would measure nothing.
- Golden-vector tests pin the digest wire format (canonical algorithm, JSON encoding, domain prefix, hash). Two systems agree only while those bytes are stable, so changing one is a versioned act rather than a refactor.

### Fixed
- `_canonical_json` and `canonicalize.age_to_bucket` document their ordering behavior. `_canonical_json`'s byte ordering (numbers sort as text) and dict-key coercion (`{1: "a"}` and `{"1": "a"}` share a content address) were undocumented despite governing shard IDs and entity resolution. `age_to_bucket` now warns that its output is **not** text-sortable — `'8-9'` sorts after `'42-43'` — which is harmless as an ESS field value, where unique field keys mean the value never decides order, but wrong if anything sorts or range-scans on it. Docstrings only; no behavior change.

## [0.10.0] — 2026-06-16

This release makes spiritwriter the **single source of truth for the knowledge-base pipeline** that had been duplicated in `claude-studio-producer` (CSP). Over a six-step lift-and-shift consume-back (strangler-fig) migration, the KB models, classifier, JSON extractor, LLM client, ingest, and `kb` helpers were consolidated here; CSP now imports them and deletes its copies (tracking issues spiritwriter-core#76 / claude-studio-producer#15). The work that lands in *this* package is the additive surface and guardrail tests below; the LLM vision extension carries one breaking-per-convention ABC change (see Changed), which drives the minor bump.

> Supersedes the never-published `0.9.1` (its content is folded in below — only `0.9.0` was released to PyPI).

### Added
- **Vision: multi-image queries + bytes-or-path input** — `LLMProvider.query_with_image` now accepts either raw `image_data=bytes` or `image_path=`, and a new `query_with_images` sends several images in one call. Backported from CSP's vision path so the consolidated provider is a superset of both. `MockLLMProvider` mirrors the contract for offline tests. (spiritwriter-core#81)
- **`spiritwriter.audit.redact`** — witness-bundle redaction tool (`python -m spiritwriter.audit.redact SRC OUT [--strict]`) completing the audit lifecycle `seed → provenance → verify → **redact**`. Best-effort, pattern-based scrubbing of known-sensitive shapes that re-chains the redacted tree with the project's canonical hashing, so the public bundle stays internally verifiable while the unredacted originals stay private. (spiritwriter-core#80)
- **`spiritwriter.ingest.load_documents` / `extract_document_text`** — basic multi-format text ingestion (markdown, text, PDF via PyMuPDF) into a `{source_ref: text}` mapping. The lightweight, format-dispatching counterpart to `DocumentIngestor` (which stays the rich single-PDF structural analyzer). Unsupported formats raise `UnsupportedDocument`; PDFs without PyMuPDF raise a clear error rather than failing obscurely.
- **`spiritwriter.llm.MockLLMProvider`** — deterministic scripted `LLMProvider` (string / list / dict / callable responses) for offline tests, demos, and CI. Previously referenced in docstrings but never shipped.

### Changed
- **BREAKING (subclass contract):** `LLMProvider` gains an abstract `query_with_images` method. External subclasses of `LLMProvider` must now implement it (all in-tree providers — `AnthropicProvider`, `MockLLMProvider` — already do). Under this project's pre-1.0 convention (minor = breaking) this is why 0.10.0 is a minor rather than a patch. (spiritwriter-core#81)
- `spiritwriter.ingest.document.DocumentIngestor._extract_json` now delegates to the shared, more robust `spiritwriter.llm.anthropic.JSONExtractor` (handles code fences, truncation, stray prose), removing ~35 lines of duplicated hand-parsing while preserving the graceful `{}`-on-failure fallback.

### Removed
- The `/style` command and the ToorCamp CFP material were removed from this repo (moved to `frio`, where they belong). (spiritwriter-core#79)

### Fixed
- `DocumentIngestor._describe_image` no longer stages the image through a dead temp file before the vision call — it passes the bytes directly, dropping the unused round-trip. (spiritwriter-core#83)

### Tests
- Carried CSP's guardrail suites into spiritwriter as the consume-back source of truth: knowledge/document models (spiritwriter-core#77), `JSONExtractor` (spiritwriter-core#78), and the ingest extraction-internal guardrails (spiritwriter-core#82). PR #81's review also added consistent vision-contract coverage across `base`/`MockLLMProvider` with input-validation guards.
- New `tests/test_ingest.py` — first test coverage of the (claude-studio-producer-derived) ingest pipeline, which previously shipped untested: multi-format loaders, `normalize_atom_type`, `DocumentIngestor` in mock- and LLM-mode over generated PDFs, and figure extraction (rendered + embedded + Claude-Vision description) via synthetic image PDFs. Combined coverage of the ingest modules rose from ~15% to ~85%.

## [0.9.0] — 2026-05-31

### Changed
- **BREAKING (distribution rename):** the PyPI distribution is now `spiritwriter` (was `spiritwriter-core`). Install with `pip install spiritwriter` and the extras `spiritwriter[network]` / `spiritwriter[sealed]` / `spiritwriter[mempalace]`. The **import name is unchanged** — code still does `import spiritwriter` / `from spiritwriter.fabric import ...`, so no source changes are required in consumers beyond their dependency declaration. The `-core` suffix implied an open-core split that no longer reflects the project (the whole library is open source). `importlib.metadata` lookups, the `spiritwriter --version` option, the benchmark version reporter, and all `pip install` instructions in docs/skills were updated to the new name. The GitHub repository remains `aaronmarkham/spiritwriter-core`; repo-relative paths (CI checkout, editable-install paths, homepage links) are unchanged.

### Packaging
- Bundled JSON data is now shipped in the sdist and wheel via `[tool.setuptools.package-data]` (`spiritwriter/audit/data/canonical_findings.json`, `spiritwriter/sw_vocab/data/canonical_terms.json`). These are loaded at runtime relative to `__file__`, so a wheel built without them would `FileNotFoundError` in the audit-seed and sw-vocab-seed paths on a fresh `pip install`. Verified by installing the wheel into a clean venv and loading both files.
- Added `[project.urls]` (Homepage / Repository / Changelog / Issues) and trove `classifiers` (Apache-2.0, audience, topic, generic Python 3 + the CI-tested 3.12) so the PyPI project page renders complete metadata. Per-version classifiers are scoped to what CI actually tests; `requires-python = ">=3.9"` remains the install-surface guarantee.
- Refreshed the package `description` to match the README identity ("Agent memory you own…") instead of the stale "Shared foundation for AI content pipelines" — this string is baked immutably into the release and renders under the name on PyPI.

## [0.8.3] — 2026-05-31

### Fixed
- `spiritwriter.fabric.entitlement.is_expired()` no longer crashes on the canonical `Z`-suffixed UTC timestamps that `_now_iso()` actually produces. `datetime.fromisoformat()` rejects the `Z` suffix on Python 3.9/3.10 (`ValueError`), and a naive timestamp raised `TypeError` against an aware `now()` — either crash, if swallowed upstream, silently disabled expiry enforcement (an access-control bypass). Now normalizes the `Z` suffix and coerces naive timestamps to UTC. Regression tests added for both cases (the prior tests used `isoformat()`'s `+00:00` form and never exercised the real token format). ([PR #70](https://github.com/aaronmarkham/spiritwriter-core/pull/70))
- `spiritwriter.fabric.backends.ipfs.publish_public()` now enforces the encrypted/sealed precondition its docstring documented (raises `ValueError`) instead of only logging a warning, preventing accidental plaintext publication to public IPFS. ([PR #70](https://github.com/aaronmarkham/spiritwriter-core/pull/70))

### Changed
- `click` and `rich` promoted from the `[cli]` optional extra into core `dependencies`. The `spiritwriter` console script imports `click` at module top, so a bare `pip install spiritwriter-core` followed by `spiritwriter ...` previously failed with `ModuleNotFoundError`. ([PR #70](https://github.com/aaronmarkham/spiritwriter-core/pull/70))
- `LICENSE` replaced the short notice with the full Apache-2.0 text so GitHub detects the license and the grant is complete. Personal absolute paths / machine context scrubbed from `examples/` demo shard data ahead of the public release. ([PR #70](https://github.com/aaronmarkham/spiritwriter-core/pull/70))

## [0.8.2] — 2026-05-30

### Added
- `create_result_shard(*, parent_shard_id=...)` — keyword-only kwarg for first-class lineage pinning. Pin at the plaintext predecessor your orchestrator extracted, not at `job.content_shard_id` (the encrypted job-internal shard). `parent_shard_id` is in `signing_payload()`, so the kwarg sets the parent before signing — strictly safer than the prior mutate-then-re-put pattern, which would silently invalidate any signature applied at construction. ([PR #67](https://github.com/aaronmarkham/spiritwriter-core/pull/67))

### Changed
- Demo `examples/06_phalanx_flow/` refactored to use the shared normalization helpers from `spiritwriter.fabric.canonicalize` (replacing its inline `normalize_author()`) and the new `parent_shard_id` kwarg (replacing the post-hoc field mutation). Behavior byte-identical — same `content_shard_id` and `result_shard_id` before and after. Pinned in `tests/test_demos.py::TestDemo06PhalanxFlow::test_shard_ids_pinned` as a regression sentinel. ([PR #67](https://github.com/aaronmarkham/spiritwriter-core/pull/67))

## [0.8.1] — 2026-05-30

### Added
- Pre-resolution normalization helpers in `spiritwriter.fabric.canonicalize` (also re-exported from `spiritwriter.fabric`):
  - `first_initial(s)` — `'K.'` / `'Kazuhiko'` → `'K'`
  - `strip_punctuation(s)` — preserves hyphens (name-internal)
  - `apply_normalizers(candidate, normalizers)` — non-mutating per-field application; recommended entry point
  - `pipeline(*fns)` — left-to-right composer

  The registry's `CanonicalRegistry.resolve()` / `upsert()` do not auto-normalize beyond the `.strip().lower()` baseline that `EntitySenseSig.compute()` does internally; these helpers exist for application-side pre-processing. ([PR #65](https://github.com/aaronmarkham/spiritwriter-core/pull/65))

### Documentation
- New `## Normalize before you resolve` section in `docs/entity-resolution.md` documenting the registry's normalization gap, the recommended pre-processing pattern, and the consequence of normalizer drift (which `schema_hash()` does not guard against). Quick Start rewritten to model normalization as the default pattern. ([PR #65](https://github.com/aaronmarkham/spiritwriter-core/pull/65))
- Docstring callouts on `CanonicalSchema` and `CanonicalRegistry.resolve()` pointing at the new helpers + doc section. ([PR #65](https://github.com/aaronmarkham/spiritwriter-core/pull/65))

## [0.8.0] — 2026-05-30 — BREAKING

### Changed
- **BREAKING:** `spiritwriter.fabric.jobs.package_job(agent_id=...)` is now a **required** argument (no default). `agent_id` is load-bearing provenance — recorded as `origin` on both the content and task shards (part of their content-addressed `shard_id`) and as `granted_by` on the entitlement token's audit trail. The prior default of `"lilit"` silently misattributed work to a historical agent. Migration: thread your orchestrator's actual identifier through every `package_job()` call. The `TypeError` on missing arg is self-documenting. ([PR #63](https://github.com/aaronmarkham/spiritwriter-core/pull/63))

### Added
- `examples/extract_memory.py` rewritten end-to-end: switched LLM provider from OpenAI to Anthropic Claude via `spiritwriter.secrets.get_api_key()`, removed hardcoded workspace paths in favor of `--input` / `--store` CLI args, renamed `shingle` (noun) → `shingled` (process adjective) per `docs/shingled-extraction.md`. Algorithm (overlapping windows + multi-pass consensus voting) preserved end-to-end. ([PR #63](https://github.com/aaronmarkham/spiritwriter-core/pull/63))

### Fixed
- `spiritwriter.__version__` now derives from `importlib.metadata.version("spiritwriter-core")` rather than a hardcoded string (which had drifted to `"0.7.1"` while `pyproject.toml` was already at `0.8.0`). Falls back to `"0.0.0+unknown"` when running from source without `pip install -e .`. ([PR #63](https://github.com/aaronmarkham/spiritwriter-core/pull/63))
