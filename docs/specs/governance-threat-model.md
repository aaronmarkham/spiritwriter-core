# Governance Threat Model: Spiritwriter vs. Transport-Layer Approaches

**Context:** Industry proposals (e.g., ProofTrail / MCP tool rug-pull mitigations)
are converging on the idea that agent tool definitions need governance.
Spiritwriter arrived at the same conclusion independently — but solved it at the
data layer rather than the transport layer, driven by Frio's real threat model.

This document maps the known threat vectors, compares approaches, and identifies
remaining work.

---

## Threat: Tool Definition Rug Pull

**What it is:** An MCP server operator silently changes what a tool does after a
user has approved it. The tool name and description stay the same, but the
implementation now exfiltrates data, escalates scope, or behaves differently than
what was reviewed.

**Why it matters:** Users approve tools based on their description at approval
time. If the definition can change without detection, approval is meaningless.

### Transport-layer approach (ProofTrail-style)

- Hash tool definitions at approval time
- Store hashes in a Merkle tree
- Verify hashes before each invocation
- Log verification results to an audit trail

**Limitation:** This detects changes after the fact. The integrity check is
bolted onto a mutable transport layer. The underlying data model still allows
mutation — the check is a guard, not a constraint.

### Spiritwriter approach: abilities as shards

Tool definitions (called "abilities") are `INSTRUCTION` atoms inside a
`MemoryShard` (`shard.py:34-43`). Because shards are content-addressed:

```
shard_id = SHA-256(canonical_json(atoms + scope + origin))
```

A different definition **is** a different shard. There is no "updating" a shard
in place. The rug pull is structurally impossible at the data layer because the
identity of the ability *is* its content hash.

**Key difference:** ProofTrail adds verification to a mutable system.
Spiritwriter makes the system immutable. Verification is still useful (and the
trace chain provides it), but it's a belt on top of suspenders — the data model
itself prevents the attack.

**Source:** `spiritwriter/fabric/shard.py:168-176` (content addressing),
`AtomKind.INSTRUCTION` at line 43.

---

## Threat: Scope Escalation

**What it is:** A sub-agent performs actions beyond what it was authorized to do.
It was granted read access but writes. It was scoped to one project but accesses
another. It was budgeted $5 but spends $50.

### Transport-layer approach

Most MCP governance proposals don't address this. The focus is on tool identity,
not execution scope. Access control is left to the server implementation.

### Spiritwriter approach: entitlement tokens

Every sub-agent receives an `EntitlementToken` (`entitlement.py:30-43`) that
binds together:

| Field | What it controls |
|-------|-----------------|
| `capabilities` | Whitelist of allowed actions (`shard:read`, `shard:write`, `web:search`, `exec:run`, etc.) |
| `scopes` | fnmatch patterns restricting which shard scopes the agent can access |
| `shard_keys` | Per-shard AES-256-GCM decryption keys — agent literally cannot read shards it wasn't given keys for |
| `budget_usd` | Maximum spend cap |
| `expires_at` | UTC expiry timestamp |
| `constraints` | Extensible policy dict for additional restrictions |

Validation happens at every access point:

- `validate_capability(token, action)` — deny-by-default capability check
- `validate_scope(token, scope)` — fnmatch scope boundary
- `validate_budget(token, spent)` — budget enforcement
- `is_expired(token)` — time-based revocation

Every check is logged to the trace hash chain via `TraceEmitter`, creating an
auditable record of what was attempted, what was allowed, and what was denied.

**Source:** `spiritwriter/fabric/entitlement.py`, `spiritwriter/fabric/runner.py:172-181`.

---

## Threat: Operator Surveillance

**What it is:** The system operator can read the data flowing through their
infrastructure. Even if the operator is benign, a subpoena, breach, or insider
threat exposes all user data.

### Transport-layer approach

Not addressed. ProofTrail and similar proposals focus on integrity (was this
tampered with?) not confidentiality (can the operator read it?).

### Spiritwriter + Frio approach: sealed-box zero-knowledge

Frio's threat model requires that the operator **cannot read their own database**.
Families submit encrypted search queries using NaCl sealed-box encryption. The
system processes matches without ever decrypting the query content. If subpoenaed,
there is nothing to hand over.

At the Spiritwriter layer, this maps to:

- **Per-shard encryption** (`crypto.py`): AES-256-GCM with per-shard keys
- **Key distribution via entitlements**: Only the intended recipient's token
  contains the decryption key
- **Operator exclusion**: The orchestrator issues tokens but does not retain
  shard keys for data it shouldn't access

This is a stronger guarantee than integrity alone. ProofTrail proves you *didn't*
tamper. Sealed boxes ensure you *can't* — because you can't read the content.

**Source:** `spiritwriter/fabric/crypto.py`, ToorCamp CFP (`docs/specs/toorcamp-2026-cfp.md:22-23`).

---

## Threat: Audit Trail Tampering

**What it is:** An operator or attacker modifies the audit log to hide
unauthorized actions. If the log is mutable, it proves nothing.

### Transport-layer approach (ProofTrail-style)

- Merkle tree of tool definition hashes
- Append-only log structure
- Periodic checkpoints

### Spiritwriter approach: hash-chained trace events

`TraceEmitter` (`emitter.py`) produces a hash chain where each event includes
the hash of the previous event:

```
event_n.prev_hash = SHA-256(event_{n-1})
```

Inserting, deleting, or modifying any event breaks the chain. `verify_chain()`
walks the full chain and confirms integrity.

Additionally, because trace events reference shard IDs (which are themselves
content hashes), the trace chain and shard store form a **mutual integrity
lock** — the trace proves the shard existed, and the shard's content address
proves it hasn't changed.

**Source:** `spiritwriter/fabric/emitter.py`, `docs/traced-workflows.md`.

---

## Threat: Adversary Contributors

**What it is:** In a distributed system, some participants may be hostile. They
could submit false results, attempt to learn what's being searched for, or
disrupt the network.

### Transport-layer approach

Not addressed. MCP governance proposals assume a client-server trust model, not
a distributed contributor network.

### Spiritwriter + Frio approach: forced-value contribution

From the ToorCamp CFP: "How to build a network where even hostile participants
are forced to contribute real value while learning nothing useful."

- Contributors receive **encrypted job shards** — they cannot read the search
  query
- Their browser executes the job (handling CAPTCHAs naturally as a real user)
- Results are **encrypted to the requestor's key** — the contributor cannot read
  what they found
- The contributor's only option is to run the job honestly or not at all
- IPFS + Tailscale split transport ensures contributors see only the DHT layer,
  not the operator mesh

**Source:** ToorCamp CFP (`docs/specs/toorcamp-2026-cfp.md:29, 36`).

---

## Threat: Ability Version Confusion

**What it is:** Multiple versions of a tool/ability exist. An agent runs an
outdated or wrong version. In mutable systems, it's unclear which version was
approved or executed.

### Transport-layer approach

Version tracking via hash comparison. Requires external version registry.

### Spiritwriter approach: content addressing eliminates versioning

Each version of an ability is a distinct shard with a distinct `shard_id`. There
is no version number — the content hash *is* the version. The entitlement token
binds specific `shard_keys` to specific shard IDs, so the agent can only decrypt
and execute the exact ability version it was granted access to.

`parent_shard_id` (`shard.py:162`) provides optional lineage tracking — you can
walk the chain of superseded shards to see how an ability evolved — but the
execution path is always pinned to a specific content hash.

**Source:** `spiritwriter/fabric/shard.py:162` (parent lineage), `entitlement.py:35` (shard_keys binding).

---

## Summary: Layer Comparison

| Threat | Transport-layer (ProofTrail) | Data-layer (Spiritwriter) |
|--------|------------------------------|--------------------------|
| Tool rug pull | Detect via hash comparison | Prevent via content addressing — different content = different identity |
| Scope escalation | Not addressed | Entitlement tokens: capability whitelist, scope patterns, budget caps, expiry |
| Operator surveillance | Not addressed | Sealed-box encryption — operator cannot read data |
| Audit tampering | Merkle tree | Hash-chained trace events with mutual shard-ID integrity lock |
| Adversary contributors | Not addressed (client-server model) | Encrypted jobs + encrypted results — hostile nodes contribute value, learn nothing |
| Version confusion | Hash-based version tracking | No versions — content hash is identity; entitlements pin to specific hashes |

---

## Remaining Work

### Wiring abilities into MCP transport (if MCP is retained)

The `spiritwriter_mcp/server.py` adapter defines tools as static Python
functions decorated with `@mcp.tool()`. These definitions exist outside the
shard system. If someone swaps the server binary, tool behavior changes without
shard-layer detection.

The `tool:execute` capability is documented in the entitlements skill
(`skills/entitlements/SKILL.md:24`) but not yet defined in the `Capability`
class or enforced in code.

**Fix (if MCP adapter is kept):** Publish each MCP tool definition as an
`INSTRUCTION` shard, bind its `shard_id` to the entitlement token, and verify
the hash before dispatch. All primitives exist — this is plumbing, not
architecture.

**Alternative:** Remove the MCP adapter entirely. It is a thin convenience layer
over `ShardStore`, not a core component. The governance model does not depend on
it.

### Distributed ability validation

The self-improving loop (contributors submit new scraping abilities) needs an
evaluation agent that validates submitted abilities before they enter the network.
This is described in the ToorCamp CFP but not yet implemented.

### Formal entitlement revocation

Tokens expire via TTL but there is no active revocation mechanism (e.g.,
revocation list or epoch-based invalidation). For short-lived tokens this is
acceptable; for longer-lived grants, a revocation check may be needed.

---

## Design Lineage

Spiritwriter's governance model was designed independently of ProofTrail and
similar MCP-layer proposals. The architecture was driven by Frio's operational
threat model: an ICE detention monitoring system where the operator must be
cryptographically excluded from the data, contributors must be unable to learn
what they're searching for, and every action must be auditable without trusting
any single node.

The convergence with industry proposals around tool governance validates the
intuition that governance belongs at the data layer. The difference is that
Spiritwriter started there, while transport-layer approaches are trying to
retrofit it.

---

*Last updated: March 24, 2026*
