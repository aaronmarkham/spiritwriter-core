# Spiritwriter Substrate — Flavor Document

This document describes the wire format and verification rules of the
Spiritwriter content-addressed memory substrate. An agent that has read
this document — plus a delegation bundle (cap chain + their own
keypair) — can produce and verify valid shards in any language without
installing `spiritwriter-core`.

The primitives required: `sha256`, `Ed25519 sign/verify`, and a JSON
encoder that supports sorted keys. Every modern stdlib has these or a
single small dependency.

> **Status.** Describes what is currently implemented in
> `spiritwriter-core` v0.5+. The surface is intentionally small. Future
> additions (revocation sets, trust epochs, a `shards.spiritwriter.ai`
> HTTP protocol) are deferred until the primitives below are exercised
> in production. Worked examples are reproducible —
> see [`examples/flavor_examples.py`](../examples/flavor_examples.py).

## 1. Wire format conventions

**Canonical JSON.** Used wherever bytes need to be hashed or signed:

- Object keys sorted lexically (UTF-8 codepoint order).
- No whitespace between tokens. Separators are `,` and `:` exactly.
- Strings encoded as UTF-8 with non-ASCII characters left unescaped
  (i.e., `ensure_ascii=False` in Python).
- Numbers, booleans, and `null` use standard JSON forms.

**Hashing.** SHA-256, lowercase hex. 64 characters.

**Signing.** Ed25519 (RFC 8032). Keys are 32 bytes raw; signatures are
64 bytes. Encoded as lowercase hex on the wire.

**Pubkey thumbprint.** SHA-256 hex of the 32 raw pubkey bytes. Used to
identify a signer compactly without embedding the full key.

## 2. Memory shards

A memory shard is a content-addressed bundle of knowledge atoms. The
schema below shows every field; only the first three are required.
Optional fields are omitted from serialized output when at their default.

```json5
{
  // Required
  "atoms":          [Atom, ...],
  "scope":          "sw:article:run-abc",        // colon-namespaced
  "origin":         "agent:builder-2",           // creator id

  // Required-but-defaulted
  "decay_class":    "permanent" | "stable" | "active" | "session" | "checkpoint",
  "created_at":     "2026-05-10T12:30:00Z",     // ISO 8601, Z suffix

  // Identity (computed; round-trip verifies it matches)
  "shard_id":       "<sha256 hex>",

  // Optional content-related
  "trace_ref":      "chain:abc#42" | null,
  "parent_shard_id": "<previous version's shard_id>" | null,
  "tags":           ["..."],
  "meta":           {},

  // Operational state (mutable; NOT in shard_id, NOT in signing payload)
  "last_checked":   "2026-05-10T12:00:00Z" | null,
  "check_count":    0,

  // Authorship (set by signing)
  "signature":      "<128-char hex Ed25519>" | null,
  "created_by":     "<sha256 hex of signer pubkey>" | null,
  "cap_id":         "<sha256 hex of authorizing cap>" | null
}
```

**Atom shape:**

```json5
{
  "text":         "...",                         // required
  "kind":         "fact" | "decision" | "convention" | "preference" |
                  "entity" | "context" | "checkpoint" | "instruction",
  "entity":       "Fugaku" | null,
  "key":          "location" | null,
  "value":        "Kobe, Japan" | null,
  "confidence":   1.0,                           // omitted when 1.0
  "source_ref":   "trace:abc123" | null
}
```

### 2.1 Computing `shard_id`

`shard_id` is the SHA-256 hex of the canonical JSON of `{atoms, scope,
origin}` — *only* those three fields. Everything else (timestamps,
decay class, signatures, cap references) is envelope metadata, not
content. Two shards with the same atoms/scope/origin collide
intentionally — they're the same fact stated by the same agent.

```
shard_id = sha256_hex(canonical_json({
  "atoms":  [...],
  "scope":  "...",
  "origin": "..."
}))
```

### 2.2 Signing a shard

Signing binds the *envelope* to a producer key. The signing payload is
the canonical JSON of the full serialized shard **minus** these fields:

- `signature` (recursive, must be excluded)
- `last_checked`, `check_count` (mutable poll metadata — excluded so
  that polling doesn't invalidate signatures)

Procedure:

```
created_by = sha256_hex(signer_pubkey_bytes)
payload    = canonical_json(to_dict(shard) − {signature, last_checked, check_count})
signature  = ed25519_sign(signer_private_key, payload).hex()
```

`shard_id` stays inside the signed payload (it's recomputed in
serialization), so the signature ends up binding the content address
to the envelope.

## 3. Capabilities

A capability is a typed, signed token authorizing the holder to act
within a defined attenuation. Capabilities form chains via
`parent_cap_id`; the leaf's authority is the *intersection* of every
caveat in the chain.

```json5
{
  // Identity / display
  "token_id":       "<uuid, opaque>",
  "granted_to":     "worker:builder-2",          // human-readable label
  "granted_by":     "orchestrator:run-abc",

  // Authority surface (matches existing v0 entitlement model)
  "scopes":         ["sw:article:run-abc:*"],    // fnmatch patterns
  "capabilities":   ["shard:read", "shard:write", ...],
  "secrets":        ["OPENAI_API_KEY"],
  "budget_usd":     0.0,
  "shard_keys":     {},                          // optional AES key map

  // Lifecycle
  "created_at":     "2026-05-10T12:00:00Z",
  "expires_at":     "2026-05-10T13:00:00Z" | null,
  "trace_parent":   "trace:..." | null,
  "constraints":    {},                          // legacy freeform; prefer caveats

  // Cryptographic identity (set when signing)
  "subject_pubkey": "<64-char hex>",             // 32-byte Ed25519, holder
  "issuer_pubkey":  "<64-char hex>",             // 32-byte Ed25519, granter
  "parent_cap_id":  "<sha256 hex>" | null,       // null on root cap
  "caveats":        [Caveat, ...],
  "signature":      "<128-char hex>"
}
```

### 3.1 Caveat types (closed set)

A caveat is `{"type": "...", "value": ...}`. Verifiers **MUST fail
closed** on unknown types — silently ignoring an unrecognized
restriction would let attackers strip caveats by claiming a
non-existent type.

| Type                     | Value type             | Semantics                                                                |
|--------------------------|------------------------|--------------------------------------------------------------------------|
| `expires_at`             | ISO 8601 string        | Reject if `now >= value`. Lex sort works for UTC `Z` strings.            |
| `scope_limit`            | fnmatch pattern        | Reject if requested `scope` doesn't match.                               |
| `max_delegation_depth`   | non-negative int       | Reject if number of links *below this cap* exceeds value.                |

A cap's effective authority is the intersection of every caveat in its
chain: every caveat on every ancestor must pass for the leaf to act.

### 3.2 Computing `cap_id`

`cap_id` is content-addressable like `shard_id`, but covers the cap's
*signing payload* (not just its content):

```
cap_id = sha256_hex(canonical_json(signing_payload(cap)))
```

The signing payload includes everything that defines authority and
chain position. It deliberately **excludes**:

- `signature` (recursive)
- `shard_keys` (AES bookkeeping; rotated independently of authority)
- `trace_parent` (telemetry attachment, not authority)

Cap signing covers the same payload:

```
signature = ed25519_sign(issuer_private_key, signing_payload(cap)).hex()
```

Because Ed25519 is deterministic, a given signing payload + issuer key
yields exactly one valid signature; `cap_id` therefore uniquely names
this signed cap (issuer claim included).

### 3.3 Issuing a child cap

To delegate from parent cap `P` to a new subject:

1. Subject generates an Ed25519 keypair `(sk_C, pk_C)` locally.
2. Subject sends `pk_C` to the parent.
3. Parent constructs child cap `C`:
   - `subject_pubkey = pk_C`
   - `issuer_pubkey = P.subject_pubkey`
   - `parent_cap_id = P.cap_id`
   - `scopes`, `capabilities`, `secrets` ⊆ parent's
   - `caveats` add restrictions; `max_delegation_depth` auto-decrements
     (parent's value − 1) unless explicitly set to a smaller value.
4. Parent signs `C` with `sk_P`; returns `C` and the parent chain.

A cap with `max_delegation_depth = 0` cannot issue further children.

## 4. Verifying a produced shard

Given a produced shard `s`, its cap chain `chain[0..n]` (root → leaf),
and the substrate's trust roots `root_pubkeys`:

```
1. Recompute s.shard_id from canonical_json({atoms, scope, origin}).
   If it doesn't match s["shard_id"], reject.

2. Verify the cap chain (root to leaf):
   a. chain[0].issuer_pubkey ∈ root_pubkeys
   b. chain[0].parent_cap_id is null
   c. chain[0].verify()  -- signature against issuer_pubkey
   For i in 1..n:
     d. chain[i].issuer_pubkey == chain[i-1].subject_pubkey
     e. chain[i].parent_cap_id == chain[i-1].cap_id
     f. chain[i].verify()

3. Authorize the chain for this operation:
   For i in 0..n:
     depth_below = n − i
     For each caveat c in chain[i].caveats:
       - If c.type unknown → reject (fail closed).
       - If c is expires_at: reject if now >= c.value.
       - If c is scope_limit: reject if not fnmatch(s.scope, c.value).
       - If c is max_delegation_depth: reject if depth_below > c.value.

4. Verify the leaf attestation:
   pk_leaf = chain[n].subject_pubkey
   thumbprint(pk_leaf) must equal s.created_by
   ed25519_verify(pk_leaf, signing_payload(s), s.signature)
   where signing_payload(s) = canonical_json(to_dict(s) − {signature, last_checked, check_count})

5. Confirm s.cap_id == chain[n].cap_id.
```

Steps 2 and 3 are independent: structural validity of the chain (does
this cap legitimately descend from the substrate root?) is separate
from authorization (does what the chain permits cover this specific
operation right now?). Step 4 is independent again: the chain says
*who is authorized*; the leaf signature says *who actually produced
this artifact*.

## 5. Bootstrap

A library-free agent is bootstrapped with three pieces of information:

1. **This document.** The wire format and verification rules.
2. **Trust roots.** The Ed25519 pubkey(s) of the substrate root issuer,
   as hex.
3. **Delegation bundle.** Their leaf cap, plus every ancestor up to the
   root. Their own ephemeral keypair was generated locally.

That's enough to:

- Verify any shard they receive (steps 1–4 above).
- Produce signed shards within their scope: build the envelope, set
  `cap_id` to their leaf's `cap_id`, set `created_by` to the SHA-256
  hex of their pubkey, sign the canonical payload, ship.

If the leaf's `max_delegation_depth > 0`, they can also issue further
children to sub-workers using §3.3.

## 6. Worked examples

Reproducible from [`examples/flavor_examples.py`](../examples/flavor_examples.py),
which uses fixed Ed25519 seeds (1, 2, 3) so the hex values below are
stable. Re-run after any wire-format change to detect drift.

### Keys

```
root_pubkey       = 4cb5abf6ad79fbf5abbccafcc269d85cd2651ed4b885b5869f241aedf0a5ba29
orch_pubkey       = 7422b9887598068e32c4448a949adb290d0f4e35b9e01b0ee5f1a1e600fe2674
worker_pubkey     = f381626e41e7027ea431bfe3009e94bdd25a746beec468948d6c3c7c5dc9a54b
worker_thumbprint = c2b6bf688fb8be003dcf12ee147bfd0708d7931a786c0d42ba9f5381a722998f
```

### 6.1 An unsigned memory shard

Canonical JSON of `{atoms, scope, origin}`:

```
{"atoms":[{"entity":"Fugaku","key":"location","kind":"fact","text":"The Fugaku supercomputer is in Kobe, Japan.","value":"Kobe, Japan"}],"origin":"agent:builder-2","scope":"sw:article:run-abc"}
```

`shard_id = sha256_hex(...) = 7e25b712ff54f42e333da4c526de24177ffe9e6fc71dff549a38b46f25b58d9c`

Full serialized form:

```json
{
  "atoms": [{
    "entity": "Fugaku",
    "key": "location",
    "kind": "fact",
    "text": "The Fugaku supercomputer is in Kobe, Japan.",
    "value": "Kobe, Japan"
  }],
  "created_at": "2026-05-10T12:00:00Z",
  "decay_class": "permanent",
  "origin": "agent:builder-2",
  "scope": "sw:article:run-abc",
  "shard_id": "7e25b712ff54f42e333da4c526de24177ffe9e6fc71dff549a38b46f25b58d9c"
}
```

### 6.2 A 3-link cap chain

Root cap (self-signed, `max_delegation_depth: 3`):

- `cap_id = 0c5f215c1aa6d704c57c7eacda4ef3edd8c5b9c45b64a12a8410ae3b14ae9134`
- subject_pubkey = issuer_pubkey = root_pubkey
- caveats: `[{"type":"max_delegation_depth","value":3}]`

Orchestrator cap (issued by root, expires in 1 hour):

- `cap_id = 855c4b0ea32ceb294378f06fe61c79b34a16e4b9a3ecc4ebca8c2d7e4cb6a93f`
- `parent_cap_id = 0c5f215c...9134`
- subject_pubkey = orch_pubkey, issuer_pubkey = root_pubkey
- caveats: `[expires_at: 2026-05-10T13:00:00Z, max_delegation_depth: 2]`
- scopes: `["sw:article:run-abc:*"]`

Worker cap (issued by orchestrator, leaf):

- `cap_id = 0bb95a741cffeba06a377d564a9484c7305cf9dfb43318c97cccc5f508427aa6`
- `parent_cap_id = 855c4b0e...a93f`
- subject_pubkey = worker_pubkey, issuer_pubkey = orch_pubkey
- caveats: `[max_delegation_depth: 1]` (auto-decremented from orchestrator's 2)

### 6.3 A signed produced shard

The worker writes a fact about Fugaku, signing under their leaf cap:

```json
{
  "atoms": [{
    "entity": "Fugaku",
    "key": "ranking",
    "kind": "fact",
    "text": "Fugaku is currently the world's fourth-fastest supercomputer.",
    "value": "4"
  }],
  "cap_id": "0bb95a741cffeba06a377d564a9484c7305cf9dfb43318c97cccc5f508427aa6",
  "created_at": "2026-05-10T12:30:00Z",
  "created_by": "c2b6bf688fb8be003dcf12ee147bfd0708d7931a786c0d42ba9f5381a722998f",
  "decay_class": "permanent",
  "origin": "agent:builder-2",
  "scope": "sw:article:run-abc:builder-2",
  "shard_id": "2e23e1733453531bd6a640c500049767ea12ddd2333c8cb1acd5c50e222b37fe",
  "signature": "c33490397c7c22705e869197aae112bf0a8494da134a1c412d32a19b5eb5fcbbe3b1b0a1840298ffcd31b44019467603f73146cff9ec1eed9545a7356c215408"
}
```

Verifier checks:

- `created_by` equals `sha256_hex(worker_pubkey)`. ✓
- `cap_id` matches the leaf cap. ✓
- `scope` (`sw:article:run-abc:builder-2`) is permitted by the orchestrator's
  `scope_limit` caveat (`sw:article:run-abc:*`). ✓
- At `2026-05-10T12:30:00Z`, the orchestrator's `expires_at`
  (`2026-05-10T13:00:00Z`) has not passed. ✓
- `signature` verifies under `worker_pubkey` against the signing
  payload. ✓

Shard accepted. Provenance is intact: this artifact is attributable
specifically to the worker, authorized by a chain rooted in the
substrate trust anchor.
