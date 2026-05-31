# spiritwriter

Shared Python library for knowledge management, secrets, LLM abstraction, and **memory/trace shards**.

## Setup

```bash
pip install -e .
```

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

## Commands

| Command | Purpose | Path |
|---------|---------|------|
| `/style` | Apply Aaron's writing voice — auto-detects content type (lesson, docs, blog, spec) | `.claude/commands/style.md` |

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

## Tests

```bash
python -m pytest tests/ -v
```
