# Changelog

All notable changes to `spiritwriter` are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project follows pre-1.0 SemVer — **minor** for breaking changes, **patch** for additive/non-breaking changes.

Entries before 0.8.0 are not backfilled; consult `git log` for earlier history. Releases through 0.8.3 were published under the distribution name `spiritwriter-core`.

## [0.9.1] — 2026-06-01

### Added
- **`spiritwriter.fabric.familiarize`** — the "get familiar with my app" path: turn a folder of project docs into one aligned, deduplicated knowledge shard an agent hydrates on startup instead of re-reading and re-deriving every session. `familiarize(sources, provider, store, ref_name, ...)` orchestrates extract → align → store; `extract_atoms_llm()` and `align_atoms()` are usable standalone. Built to the CMC spec (`docs/specs/cmc-spec-v0.1.md`): LLM extraction enriched with `key_definition` (EDC "Define" step) + entity `sense`, then **tiered alignment** — deterministic exact-merge → sense gate → LLM adjudication of the ambiguous residual (`SAME`/`DIFFERENT`/`SUBSUMES`). No embedding stack; LLM fires only on the hard pairs. Worked example in `examples/07_familiarize/` (offline/deterministic via a mocked provider).
- **`spiritwriter.ingest.load_documents` / `extract_document_text`** — basic multi-format text ingestion (markdown, text, PDF via PyMuPDF), the front-end that feeds `familiarize`. `DocumentIngestor` remains the rich single-PDF structural analyzer. Raises `UnsupportedDocument` for formats without a loader.
- **`spiritwriter.llm.MockLLMProvider`** — deterministic scripted `LLMProvider` (string / list / dict / callable responses) for offline tests, demos, and CI. Previously referenced in docstrings but never shipped.
- `ShardAtom` gains two optional CMC enrichment fields — `key_definition` and `sense` (an `EntitySense`) — excluded from `content_hash`, so enriching an atom does not fork its content address. `EntitySense` exported from `spiritwriter.fabric`.
- `sw_vocab` canonical registry: added `ShardAtom`/`AtomKind` (knowledge) vs `DocumentAtom`/`AtomType` (document layout) as distinct cross-referencing terms, plus `EntitySense` and `key_definition`, and cross-linked the existing `Entity Sense Signature` (ESS) entry against `EntitySense` so the long-standing acronym collision can't silently recur.

### Changed
- `familiarize` ingests through `spiritwriter.ingest.load_documents` (markdown/text/PDF) rather than its own markdown-only globbing — `ingest` is the multi-format front-end, `familiarize` the alignment-into-common-KB back-end.
- `spiritwriter.ingest.document.DocumentIngestor._extract_json` now delegates to the shared, more robust `spiritwriter.llm.anthropic.JSONExtractor` (handles code fences, truncation, stray prose), removing ~35 lines of duplicated parsing while preserving the graceful `{}`-on-failure fallback.

### Tests
- New `tests/test_ingest.py` — first coverage of the (CSP-derived) ingest pipeline: multi-format loaders, `DocumentIngestor` mock- and LLM-mode over generated PDFs, and figure extraction (rendered + embedded + vision) via synthetic image PDFs. Added familiarize edge tests (LLM merge/subsumes, sense gate, malformed-adjudication fallback, `align=False`). Combined coverage of the familiarize + ingest flows rose from ~45% to ~89%.

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
