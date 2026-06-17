# Changelog

All notable changes to `spiritwriter` are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project follows pre-1.0 SemVer — **minor** for breaking changes, **patch** for additive/non-breaking changes.

Entries before 0.8.0 are not backfilled; consult `git log` for earlier history. Releases through 0.8.3 were published under the distribution name `spiritwriter-core`.

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
