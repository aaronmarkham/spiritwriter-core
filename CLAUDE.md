# spiritwriter-core

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
| **studio** | Package and run sub-agent jobs with budget tracking | `skills/studio/SKILL.md` |
| **network** | Publish/resolve shards over IPFS, private swarm config | `skills/network/SKILL.md` |

## Module Map

```
spiritwriter/
  kb/          — Knowledge base manager
  secrets/     — Keychain/secrets management
  classify/    — Content classification
  ingest/      — Document ingestion
  llm/         — LLM provider abstraction
  trace/       — Shards, store, emitter, crypto, entitlements, studio jobs, network resolver
    backends/  — Network backends (IPFS/Kubo)
  stopwords.py — Centralized stopword list
```

## Conventions

- Content-addressed storage (SHA-256). Same content = same ID.
- File-based, no external databases. DHT-ready.
- Namespace packages (`from spiritwriter.trace import ShardStore`).
- Apache 2.0 license.

## Tests

```bash
python -m pytest tests/ -v
```
