# Skill: Spiritwriter Governance Review

Audit a downstream project for patterns that should be pushed down into spiritwriter-core.

## When to Use

- A project **built on spiritwriter-core** has grown local implementations that duplicate or extend core primitives
- You need to identify **what belongs in core vs. what's application-specific**
- You're preparing a **refactor to extract shared governance primitives** from a downstream project
- You want to **validate that a downstream project is using core correctly** rather than reimplementing

## How to Run

From the downstream project (e.g., Frio), read this skill and then audit the codebase against the checklist below.

```
SHARD_REFS: spiritwriter-review
Review this project for patterns that should be pushed down into spiritwriter-core.
```

Or manually: read this file, then walk the downstream codebase systematically.

## Audit Checklist

### 1. Crypto & Encryption

**Core provides:** `spiritwriter/trace/crypto.py` — AES-256-GCM encrypt/decrypt, `EncryptedShard`, `generate_key`, `serialize_key`/`deserialize_key`.

**Look for:**
- [ ] Local encryption/decryption functions that duplicate `encrypt_shard`/`decrypt_shard`
- [ ] Key generation that doesn't use `generate_key()`
- [ ] NaCl sealed-box or other asymmetric crypto that should be added to core's `crypto.py`
- [ ] Key serialization/deserialization reimplemented locally
- [ ] Hardcoded algorithms or key sizes that should be constants in core

**Push-down signal:** If the downstream project uses a crypto pattern in 2+ places, it belongs in core.

### 2. Entitlements & Scope

**Core provides:** `spiritwriter/trace/entitlement.py` — `EntitlementToken`, `Capability` class, `create_entitlement`, `validate_capability`, `validate_scope`, `validate_budget`, `is_expired`.

**Look for:**
- [ ] New capability strings not in core's `Capability` class (e.g., `monitor:search`, `roster:scrape`, `alert:send`)
- [ ] Custom scope validation logic that doesn't use `validate_scope()`
- [ ] Budget tracking reimplemented outside `validate_budget()` / `BudgetTracker`
- [ ] Token creation that bypasses `create_entitlement()`
- [ ] Expiry checks that don't use `is_expired()`
- [ ] Custom constraint types in `token.constraints` that should be first-class fields
- [ ] Revocation logic (core doesn't have this yet — if downstream built it, it should move to core)

**Push-down signal:** New capabilities that are generic (not app-specific) belong in the `Capability` class. App-specific capabilities (e.g., `roster:scrape`) stay downstream but should still use the `Capability` pattern.

### 3. Shard Types & Atoms

**Core provides:** `spiritwriter/trace/shard.py` — `MemoryShard`, `ShardAtom`, `AtomKind` (fact, decision, convention, preference, entity, context, checkpoint, instruction), `DecayClass` (permanent, stable, active, session, checkpoint).

**Look for:**
- [ ] New `AtomKind` values defined locally (e.g., `QUERY`, `RESULT`, `ALERT`, `ABILITY`)
- [ ] New `DecayClass` values (e.g., shorter TTLs for real-time monitoring)
- [ ] Custom shard subclasses or wrapper types
- [ ] Shard creation patterns that should be factory functions in core
- [ ] `meta` dict keys used consistently across the project (these may deserve promotion to first-class fields)
- [ ] Custom `hydrate_context()` overrides or formatters

**Push-down signal:** If a new atom kind or decay class is generic (not domain-specific), add it to core. If it's domain-specific but the *pattern* is reusable, consider making core extensible (registry pattern).

### 4. Trace & Audit

**Core provides:** `spiritwriter/trace/emitter.py` — `TraceEmitter`, hash-chained events, `verify_chain`. `spiritwriter/trace/visualize.py` — Mermaid diagram generation.

**Look for:**
- [ ] Custom event types not using `TraceEmitter.emit()`
- [ ] Parallel or alternative audit logging
- [ ] Chain verification reimplemented or extended
- [ ] Custom visualization/reporting on trace data
- [ ] Trace events that carry domain-specific metadata which should be standardized

**Push-down signal:** If the downstream project extends trace with new event schemas used across multiple agents, those schemas belong in core.

### 5. Network & Distribution

**Core provides:** `spiritwriter/trace/network.py` — `NetworkResolver` protocol, `ShardLocation`, `ShardManifest`. `spiritwriter/trace/backends/kubo.py` — IPFS/Kubo backend.

**Look for:**
- [ ] Custom network transport (e.g., Tailscale mesh, direct HTTP, WebSocket)
- [ ] Shard routing logic that should be a `NetworkResolver` backend
- [ ] Job distribution patterns not using `StudioJob` / `StudioRunner`
- [ ] IPFS pinning/unpinning logic that should be in the Kubo backend
- [ ] Split-transport patterns (e.g., IPFS for public, Tailscale for operator) that should be a core concept

**Push-down signal:** New transport backends that are reusable (not Frio-specific) belong in `spiritwriter/trace/backends/`.

### 6. Studio Jobs & Budget

**Core provides:** `spiritwriter/trace/studio_job.py` — `StudioJob` packaging. `spiritwriter/trace/studio_runner.py` — `StudioRunner`, `BudgetTracker`.

**Look for:**
- [ ] Job types or schemas defined outside `StudioJob`
- [ ] Budget tracking not using `BudgetTracker`
- [ ] Job lifecycle management (queue, retry, timeout) built locally
- [ ] Job result validation patterns that should be standardized
- [ ] Multi-step job orchestration that extends beyond single `StudioRunner` scope

**Push-down signal:** Job lifecycle primitives (retry, timeout, queue) are core concerns if used by more than one downstream project.

### 7. Governance Patterns

**Core provides:** The threat model at `docs/specs/governance-threat-model.md` documents six threat vectors and Spiritwriter's approach to each.

**Look for:**
- [ ] Zero-knowledge patterns (sealed-box, operator exclusion) that should be core primitives
- [ ] Adversary contributor handling (encrypted jobs, encrypted results) that generalizes
- [ ] Ability versioning / self-improving loop patterns not yet in core
- [ ] Capability-based access control reimplemented outside entitlements
- [ ] Audit/compliance patterns (what was accessed, by whom, when) beyond basic trace

**Push-down signal:** If a governance pattern protects against a threat in the threat model doc, it belongs in core — that's the whole point of the governance layer.

## Output Format

After auditing, produce a report with three sections:

### 1. Push Down (belongs in spiritwriter-core)

For each item:
- **What:** Description of the pattern/code
- **Where in downstream:** File path and line numbers
- **Where in core:** Which core module it extends or which new module it needs
- **Why:** Which threat vector or design principle it serves
- **Effort:** S/M/L

### 2. Stay Downstream (application-specific)

For each item:
- **What:** Description
- **Why it's app-specific:** What makes this not generalizable

### 3. Core Gaps (core should support this but doesn't yet)

For each item:
- **What:** The missing primitive or extension point
- **Workaround in downstream:** How the app works around the gap
- **Proposed core change:** What core should provide
- **Threat model ref:** Which threat vector from `docs/specs/governance-threat-model.md` this addresses

## Cross-Reference: Core Module Map

When proposing push-downs, map to these core locations:

```
spiritwriter/
  trace/
    shard.py         — MemoryShard, ShardAtom, AtomKind, DecayClass
    store.py         — ShardStore, refs, hydration, encrypted storage
    emitter.py       — TraceEmitter, hash chain, verify_chain
    crypto.py        — AES-256-GCM, EncryptedShard, key management
    entitlement.py   — EntitlementToken, Capability, validation
    studio_job.py    — StudioJob packaging
    studio_runner.py — StudioRunner, BudgetTracker
    network.py       — NetworkResolver protocol, ShardLocation
    visualize.py     — Mermaid trace diagrams
    extract.py       — Atom extraction from text
    backends/
      kubo.py        — IPFS/Kubo network backend
```

## Source Files

- `docs/specs/governance-threat-model.md` — Threat model (reference during audit)
- `skills/entitlements/SKILL.md` — Entitlement API reference
- `skills/shards/SKILL.md` — Shard API reference
- `skills/trace/SKILL.md` — Trace API reference
- `skills/studio/SKILL.md` — Studio job API reference
- `skills/network/SKILL.md` — Network resolver API reference
