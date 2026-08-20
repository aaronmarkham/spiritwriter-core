# spiritwriter

Shared Python library for knowledge management, secrets, LLM abstraction, and **memory/trace shards**.

## Setup

```bash
pip install -e .                        # library only
pip install -e ".[dev,sealed,network]"  # what CI installs — use this to run the tests
```

The `dev` extra carries `pytest-asyncio`. Without it the async tests in
`tests/test_llm_anthropic.py` are skipped, not run.

## Skills

Read the relevant skill for your task:

| Skill | When to use | Path |
|-------|------------|------|
| **shards** | Create, store, hydrate, query knowledge shards | `skills/shards/SKILL.md` |
| **trace** | Log agent actions with hash-chained provenance | `skills/trace/SKILL.md` |
| **entitlements** | Encrypt shards, grant scoped access tokens | `skills/entitlements/SKILL.md` |
| **jobs** | Package and run sub-agent jobs with budget tracking | `skills/jobs/SKILL.md` |
| **network** | Publish/resolve shards over IPFS, private swarm config | `skills/network/SKILL.md` |
| **audit** | Traced security audit of Android apps — findings + hash-chain + witness | `skills/audit/SKILL.md` |
| **sw-vocab** | Validate spiritwriter's own terminology in docs/AI-drafts — catches drift, invented terms, deferred-but-claimed terms | `skills/sw-vocab/SKILL.md` |

## Examples

`examples/` has four self-contained fabric demos (no LLM, no network). Run any demo with `python examples/NN_xxx/run.py`. Tests: `python -m pytest tests/test_demos.py -v`.

## Module Map

```
spiritwriter/
  kb/          — Knowledge base manager
  secrets/     — Keychain/secrets management
  classify/    — Content classification
  ingest/      — Document ingestion
  llm/         — LLM provider abstraction
  fabric/      — Shards, store, emitter, crypto, entitlements, jobs, network resolver
    backends/  — Network backends (IPFS/Kubo)
  audit/       — Traced Android APK security audits (provenance, registry, verify)
  sw_vocab/    — Terminology canonicalization for spiritwriter's own docs (dogfoods CanonicalRegistry)
  stopwords.py — Centralized stopword list
```

## Conventions

- Content-addressed storage (SHA-256). Same content = same ID.
- File-based, no external databases. DHT-ready.
- Namespace packages (`from spiritwriter.fabric import ShardStore`).
- Apache 2.0 license.
- File discovery: use the Glob and Grep tools, not shell `find`/`grep`. Only the tool-based versions are auto-approved — `find -exec`, `find | xargs grep`, and out-of-tree searches trigger permission prompts, and shell `find` is fragile cross-platform. Applies to subagents too (e.g. the audit skill's scan agent): have them discover with Glob/Grep/Read rather than shelling out.

## Tests

```bash
pip install -e ".[dev,sealed,network]"
python -m pytest tests/ -v
```

Expect 948 passed, 11 skipped — the skips are the IPFS tests, which need a
local Kubo node. Any other skip means an extra is missing; the skip reason
names it.
