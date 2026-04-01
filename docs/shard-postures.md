# Shard Postures: Choosing the Right Trust Model

## Privacy is a dial, not a switch.

The same `MemoryShard` primitive can protect a detained person's family from surveillance — sealed-box encryption, zero-knowledge, no public footprint — and in the same system, serve as an unsigned, freely shareable saved game for a volunteer contributor. Same atoms. Same store. Same content addressing. Different dial position.

A **posture** is where you set that dial: the combination of choices you make about a shard's encryption, signing, scope, decay, and distribution. These choices define who can read it, who can prove it's real, how long it lives, and what happens if someone tampers with it.

This document helps you choose the right posture for your use case. It's organized from most restrictive to most open, with real implementations as examples.

---

## The Spectrum

```
SEALED                                                          OPEN
  │                                                               │
  │  zero-knowledge    scoped         signed        unsigned      │
  │  sealed-box        entitlements   plaintext     saved game    │
  │                                                               │
  │  operator can't    agents get     anyone can    anyone can    │
  │  read own data     scoped keys    read, nobody  read, edit,   │
  │                    + budgets      can forge     share, copy   │
  │                                                               │
  ▼                                                               ▼
 privacy-first                                     access-first
```

Every posture uses the same `MemoryShard` and `ShardAtom` primitives. The same `ShardStore`. The same content-addressing (SHA-256). The difference is what you layer on top.

---

## Posture 1: Sealed Box (Zero-Knowledge)

**When to use:** The operator must not be able to read the data they're storing or processing. The system works *on* encrypted data without seeing it.

**Properties:**
- NaCl sealed-box encryption (Curve25519 + XSalsa20-Poly1305)
- Only the holder of the private key can decrypt
- The operator/server has no decryption capability
- Content-addressed on the ciphertext — even the shard ID reveals nothing about contents

**Example: Frio search shards**

A family submits a search for a detained person. The query is encrypted to their key before it touches the server. The server matches against roster data using the service key, but results are encrypted back to the requestor's key. The operator — the person running frio — cannot read who is being searched for, cannot see match results, cannot identify the requestor.

```python
# Requestor side
shard = MemoryShard(scope="frio:search:sealed", atoms=[...])
sealed = seal_shard(shard, service_pubkey)  # only service can open to process
# ... later ...
result = unseal_result(result_shard, requestor_privkey)  # only requestor can read
```

**When you need this:** Your users are at risk. The act of searching is itself a threat. The system must be trustworthy even if the operator is compromised, subpoenaed, or hostile. The data is sensitive enough that "trust me, I won't look" is not acceptable.

**Tradeoffs:**
- No plaintext indexing or search server-side
- Key management is the user's responsibility — lose the key, lose access
- Processing encrypted data requires careful protocol design
- Highest implementation complexity

---

## Posture 2: Scoped Entitlements (Trust-but-Verify)

**When to use:** Multiple agents or services need access to different slices of data, with enforced boundaries on what each can see and do.

**Properties:**
- AES-256-GCM per-shard encryption
- Decryption keys embedded in `EntitlementToken`s
- Tokens carry capability whitelists (`shard:read`, `tool:execute`, `budget:spend`)
- Scope patterns (fnmatch) restrict which shards an agent can access
- Budget caps limit spend
- TTL-based expiry
- Every grant, check, decrypt, and spend is logged to the trace chain

**Example: Multi-agent production pipeline**

A video production system delegates work to specialized agents (script writer, video generator, QA reviewer). Each agent gets an entitlement token scoped to only the shards it needs, with a budget cap for API calls. The script writer can read the brief but not the raw footage. The renderer can read everything but can't spend more than $2 on API calls.

```python
token = EntitlementToken(
    capabilities=["shard:read", "tool:execute"],
    scopes=["project:video:script:*"],  # only script-related shards
    shard_keys={"shard_abc": per_shard_key},
    budget_cents=200,
    ttl_seconds=3600
)
```

**When you need this:** You have multiple actors (agents, services, tenants) that need different access levels. You want cryptographic enforcement, not just policy. You need an audit trail of who accessed what.

**Tradeoffs:**
- Token management overhead
- Key distribution for each shard
- Budget tracking adds state
- Medium implementation complexity

---

## Posture 3: Signed Plaintext (Prove-but-Share)

**When to use:** The data can be public, but its origin and integrity matter. Anyone can read it, but nobody should be able to forge it or tamper with it undetected.

**Properties:**
- Plaintext atoms — no encryption
- Ed25519 signatures on atoms or the shard as a whole
- Content-addressing provides tamper detection (change content = different hash = different shard)
- Signatures prove authorship
- Can be freely distributed, cached, replicated

**Example: Site skills and ability shards**

When a contributor profiles a jail roster site, the validated result becomes a `SiteSkill` — a plaintext shard describing how to scrape that facility. It's signed by the validating agent. Anyone can read it (it describes a public website). The signature proves it was validated by the system, not fabricated. The content address means any cached copy is verifiable.

```python
skill_shard = MemoryShard(
    scope="frio:skill:validated",
    atoms=[...],  # platform, fields, CAPTCHA type, navigation steps
    origin="profile-reviewer-agent"
)
# Content-addressed: ID = SHA-256 of atoms
# Signature attached: proves profile-reviewer-agent produced this
# Anyone can read, distribute, cache
```

**When you need this:** The data isn't secret, but provenance matters. You need to know *who* produced it and that it hasn't been modified. Supply chain transparency. Versioned capabilities. Published specifications.

**Tradeoffs:**
- No confidentiality — anyone with the shard can read it
- Signing key management (who holds the signing key, how is it rotated)
- Content addressing means no in-place updates — each version is a new shard

---

## Posture 4: Lightweight / Saved Game (Just Enough Structure)

**When to use:** The shard is primarily a local convenience — a portable record for the user's benefit. Integrity of the shard itself doesn't matter much because the *work products* it references are validated independently elsewhere. The goal is accessibility and portability, not security.

**Properties:**
- Plaintext, no encryption
- No signatures on the shard contents
- Content-addressed (the shard still has an ID), but no one is checking it for tamper
- Freely copyable, shareable, editable
- Server-signed entitlements embedded *within* the shard for any access-controlled resources
- Everything else is cosmetic / local state

**Example: Frio contributor memory shard**

A volunteer helps profile jail roster sites and search for detained people. Their shard tracks what missions they've completed, what rank they've earned, what facilities they've touched. It's their saved game — download it, move it to a new device, hand it to a friend. The scorecard and mission history are unsigned because who cares if someone edits their local stats. The actual work (submitted profiles, search results) was validated server-side on submission.

The *only* signed element: intelligence entitlements. When a contributor earns access to facility intelligence (booking trends, audit findings, enforcement patterns), the server issues a signed token stored in the shard. This token gates access to real data. Everything else in the shard is just the contributor's local record.

```python
shard = MemoryShard(
    scope="frio:contributor",
    atoms=[
        # Unsigned local state — the saved game
        ShardAtom(kind="fact", key="rank", value="tracker"),
        ShardAtom(kind="fact", key="missions_completed", value="15"),
        ShardAtom(kind="fact", key="facilities_profiled", value="12"),

        # Server-signed entitlement — the only thing that gates access
        ShardAtom(kind="entity", key="entitlement",
                  value={"facility": "cobb_county_ga", "tier": "dossier",
                         "server_sig": "..."}),
    ],
    decay_class=DecayClass.PERMANENT  # it's their save file
)
# No shard-level signature. No encryption.
# Portable. Shareable. Editable (except entitlements).
```

**When you need this:** The shard is for the user, not for the system. The system validates work at submission time, not by inspecting the shard later. You want zero friction — no passwords, no key management, no "forgot my passphrase." Sharing the shard is a feature, not a threat. The only access control needed is for specific high-value resources, handled by embedded signed tokens.

**Tradeoffs:**
- No confidentiality or integrity guarantees on the shard itself
- Cannot trust the shard's contents server-side (scorecard, history could be fabricated)
- Server-signed entitlements are the only verifiable element
- Simplest implementation — lowest barrier to entry

---

## Choosing a Posture

| Question | If yes → | If no → |
|----------|----------|---------|
| Can the operator see this data? | Posture 2, 3, or 4 | **Posture 1 (Sealed Box)** |
| Do multiple agents need different access? | **Posture 2 (Entitlements)** | Posture 1, 3, or 4 |
| Does provenance matter more than secrecy? | **Posture 3 (Signed)** | Posture 1, 2, or 4 |
| Is this primarily for the user's local benefit? | **Posture 4 (Saved Game)** | Posture 1, 2, or 3 |
| Is the user at risk if this data leaks? | **Posture 1 (Sealed Box)** | Posture 2, 3, or 4 |
| Is sharing the shard a feature? | **Posture 4 (Saved Game)** | Posture 1 or 2 |

### Decision flow

```
Is the data sensitive / dangerous if exposed?
├── YES → Can the system process it without seeing plaintext?
│   ├── YES → Posture 1: Sealed Box
│   └── NO  → Posture 2: Scoped Entitlements (encrypt, grant scoped access)
└── NO  → Does the origin/integrity of the data matter to the system?
    ├── YES → Posture 3: Signed Plaintext
    └── NO  → Posture 4: Saved Game
```

---

## Mixing Postures

Real systems use multiple postures simultaneously. The same application can have shards at different posture levels for different purposes.

**Frio uses three postures in one system:**

| Data | Posture | Why |
|------|---------|-----|
| Search queries from families | **Sealed Box** | The act of searching is the threat model. Operator must not see queries. |
| Validated site skills | **Signed Plaintext** | Public data (how to scrape a public website), but provenance matters for trust. |
| Contributor memory | **Saved Game** | Local record for the contributor. Sharing is fine. Only intelligence entitlements are signed. |

This is the same `MemoryShard` primitive in all three cases. Same atoms, same store, same content addressing. The posture is a set of choices about what you encrypt, what you sign, and what you leave open.

---

## Postures and Decay

Postures are orthogonal to decay classes, but some combinations are more natural:

| Posture | Typical Decay | Why |
|---------|---------------|-----|
| Sealed Box | ACTIVE (14d) or custom | Sensitive data shouldn't persist longer than needed |
| Scoped Entitlements | STABLE (90d) | Agent work products outlive the session but not forever |
| Signed Plaintext | PERMANENT or STABLE | Published capabilities / validated skills should persist |
| Saved Game | PERMANENT | It's the user's file — they decide when to delete it |

---

## Building Your Own Posture

These four postures aren't a closed set. They're points on a spectrum. You might need:

- **Signed + encrypted:** Provenance matters AND the data is sensitive. Sign first, then encrypt. The signature is on the plaintext — decryptor can verify authorship.
- **Sealed + distributed:** Zero-knowledge AND high availability. Publish encrypted shards to IPFS. Network sees ciphertext. Only key holders can read.
- **Saved game + hash chain:** Local convenience AND ordering matters. No signatures, but each atom references the previous hash. Tamper with one, the chain breaks. Useful when you want chronological integrity without server involvement.
- **Entitlements + decay:** Scoped access AND auto-cleanup. Tokens expire, shards decay, nothing persists beyond its useful life.

The primitives compose. Encryption, signing, decay, scoping, distribution — these are independent knobs. A posture is just the combination you choose for a given use case.

---

## Further Reading

- [Shards skill](../skills/shards/SKILL.md) — Core API: atoms, shards, store, decay
- [Entitlements skill](../skills/entitlements/SKILL.md) — Encryption, tokens, capabilities, budget tracking
- [Governance threat model](specs/governance-threat-model.md) — What can go wrong and how postures defend against it
- [Network distribution](network-distribution.md) — Publishing and resolving shards over IPFS
- [Traced workflows](traced-workflows.md) — Checkpoint/resume patterns with audit trails
