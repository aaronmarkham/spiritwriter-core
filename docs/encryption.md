# Encryption and Sealed Shards

Two encryption layers, one access-control layer. Pick based on **who you don't trust**:

- **AES-256-GCM (symmetric)** — operator and key-holder are the same entity (or cooperating). Both sides have the key.
- **NaCl sealed-box (asymmetric)** — operator must not see content, period. Only the owner's private key opens it.
- **Entitlement tokens** — package keys + scopes + capabilities + budget into a delegatable bearer credential for sub-agents.

If your operator is your own service running on your own machine, AES is fine and faster. If your operator is shared infrastructure, a third party, or anything you wouldn't hand a plaintext copy to — sealed-box.

## What Stays Visible

Both shapes leak the same metadata. The trade-off is operator capability, not metadata privacy.

| Field | Plaintext | AES-encrypted | Sealed |
|-------|-----------|---------------|--------|
| `shard_id` (content address) | Visible | Visible | Visible |
| `scope` | Visible | Visible | Visible |
| `atom_count` | Derivable | Visible | Visible |
| `created_at`, `origin_agent` | Visible | Visible | Visible |
| `content_hash` (plaintext SHA-256) | Derivable | Visible | Visible |
| Atom contents (text, entity, key, value) | **Visible** | Encrypted | Encrypted |
| Operator can decrypt? | n/a | **Yes, with key** | **No, ever** |

The visible metadata is what makes routing, indexing, and entitlement checks possible without decryption. If you need scope to be private too, you're past what this layer offers — encrypt the scope name itself before passing it in, or push routing to a trusted node.

## AES-256-GCM Symmetric Encryption

```python
from spiritwriter.fabric.shard import MemoryShard, ShardAtom, AtomKind
from spiritwriter.fabric.crypto import (
    generate_job_key, encrypt_shard, decrypt_shard,
    serialize_key, deserialize_key, DecryptionError,
)

shard = MemoryShard(
    atoms=[ShardAtom(text="API key", kind=AtomKind.FACT,
                     entity="project", key="api_key", value="sk-...")],
    scope="project:secrets",
    origin="admin-agent",
)

key = generate_job_key()                # 32 random bytes (os.urandom)
encrypted = encrypt_shard(shard, key)   # AES-256-GCM with 12-byte random nonce
decrypted = decrypt_shard(encrypted, key)
assert decrypted.shard_id == shard.shard_id
```

`encrypt_shard` produces an `EncryptedShard` carrying ciphertext, nonce, and the SHA-256 hash of the original plaintext. `decrypt_shard` verifies that hash after decryption — if the bytes have been tampered with (or you decrypted with a key that produces valid-looking but wrong plaintext), it raises `DecryptionError`.

### Failure Modes

```python
# Wrong key — AES-GCM auth tag fails
try:
    decrypt_shard(encrypted, generate_job_key())
except DecryptionError as e:
    print(f"Auth tag mismatch: {e}")

# Tampered ciphertext — same path
encrypted.encrypted_payload = b"\x00" + encrypted.encrypted_payload[1:]
try:
    decrypt_shard(encrypted, key)
except DecryptionError:
    pass  # GCM detects modification

# Tampered metadata that bypasses GCM — content_hash check catches it
# (e.g. someone swaps in a different shard's content_hash)
# decrypt_shard raises DecryptionError("Content hash mismatch — data may be corrupted")
```

GCM's auth tag covers the ciphertext. The separate `content_hash` field is belt-and-suspenders — it catches the case where someone swaps payloads at the storage layer without re-encrypting.

### EncryptedShard Fields

The on-disk shape, for anyone implementing a custom backend or debugging a serialization issue:

| Field | Visible | Description |
|-------|---------|-------------|
| `shard_id` | Yes | Content address of the original (plaintext) shard |
| `scope` | Yes | Entitlement boundary |
| `encrypted_payload` | No | AES-256-GCM ciphertext (the encrypted shard JSON) |
| `nonce` | No | 12-byte random nonce — fresh per encryption |
| `atom_count` | Yes | Number of atoms (for cost estimation without decryption) |
| `created_at` | Yes | ISO timestamp of the source shard |
| `origin_agent` | Yes | Creating agent id |
| `content_hash` | Yes | SHA-256 of plaintext — verified on decrypt |

Serialization is JSON via `to_dict()` / `from_dict()`. Binary fields (`encrypted_payload`, `nonce`) are base64-encoded.

### Key Serialization

Keys are 32 bytes — base64url for transport:

```python
key_str = serialize_key(key)             # "Abc123..."  (43-char URL-safe base64)
key_back = deserialize_key(key_str)      # bytes; raises ValueError if not 32 bytes
assert key == key_back
```

### Store Integration

```python
from spiritwriter.fabric.store import ShardStore

store = ShardStore("~/.myapp/shards")

encrypted = store.encrypt_and_store(shard, key)         # one call: encrypt + persist
enc = store.get_encrypted(encrypted.shard_id)           # operator side: metadata only
decrypted = store.decrypt_and_get(encrypted.shard_id, key)  # holder side: full content
```

See [shard-store.md](shard-store.md#encrypted-shards) for the lifecycle of encrypted shards in storage.

## NaCl Sealed-Box Encryption

Sealed-box uses an ephemeral Curve25519 key per message, encrypted with the owner's public key via XSalsa20-Poly1305. The operator stores the public key, can encrypt *to* the owner, and **cannot read what was sealed** — not even what they sealed themselves, since the ephemeral sender key is discarded.

Requires PyNaCl: `pip install 'spiritwriter-core[sealed]'`.

### When to Use Sealed-Box

- **Zero-knowledge monitoring.** Families search for detained people on shared infrastructure; the operator never sees search terms or matches (frio).
- **Multi-tenant hosting.** Tenant data lives on shared storage; the host can't read it.
- **Source protection.** Journalistic or legal data where chain-of-custody matters.
- **Result delivery.** A service does work for the owner, seals results to them, then deletes its own working copy.

If any of those describe the trust boundary, sealed-box. Otherwise the symmetric-key path is simpler and faster.

### Key Management

```python
from spiritwriter.fabric.sealed import (
    OwnerKeypair, generate_owner_keypair,
    seal_for_owner, unseal_as_owner,
)

keypair = generate_owner_keypair()
keypair.public_key_b64    # share with the service — stored alongside shards
keypair.private_key_b64   # capability key — owner keeps, never sends

# Reconstruct from private key (full keypair — owner side)
owner = OwnerKeypair.from_private_b64(keypair.private_key_b64)

# Reconstruct from public key only (seal-only — service side)
service_kp = OwnerKeypair.from_public_b64(keypair.public_key_b64)
# service_kp.private_key == b""   — service literally doesn't have it
```

The split is enforced at construction. A service that never holds the private key cannot accidentally leak it.

### Sealing Raw Data

```python
plaintext = b'{"name": "John Smith", "booking": "2024-1234"}'
ciphertext = seal_for_owner(plaintext, keypair.public_key)

decrypted = unseal_as_owner(ciphertext, keypair.private_key)
assert decrypted == plaintext
```

### Sealing Shards

```python
from spiritwriter.fabric.sealed import seal_shard, unseal_shard, UnsealError

sealed = seal_shard(shard, keypair.public_key)
sealed.shard_id        # same content address as plaintext shard
sealed.scope           # visible
sealed.atom_count      # visible
sealed.content_hash    # plaintext SHA-256 (integrity)
sealed.owner_pubkey    # stored alongside — used for result delivery later
# sealed.sealed_payload — opaque to operator

decrypted = unseal_shard(sealed, keypair.private_key)
```

`unseal_shard` raises `UnsealError` if the wrong private key is used or the payload is tampered.

### SealedShard Fields

| Field | Visible | Description |
|-------|---------|-------------|
| `shard_id` | Yes | Content address of the original shard |
| `scope` | Yes | Entitlement boundary |
| `sealed_payload` | No | NaCl sealed-box ciphertext — opaque to operator |
| `owner_pubkey` | Yes | 32-byte Curve25519 public key (used for result delivery) |
| `atom_count` | Yes | Number of atoms |
| `created_at` | Yes | ISO timestamp |
| `origin_agent` | Yes | Creating agent id |
| `content_hash` | Yes | SHA-256 of plaintext — verified on unseal |

Note that `owner_pubkey` is stored alongside the sealed payload. That's deliberate: it lets the operator address subsequent results back to the same owner without holding any per-owner state.

### Ed25519 Signing for Result Integrity

Sealed-box keeps content private but doesn't prove who produced it. For "this came from service X, not someone impersonating service X," sign with Ed25519:

```python
from spiritwriter.fabric.sealed import (
    generate_signing_keypair, sign_data, verify_signature,
)

signing_key, verify_key = generate_signing_keypair()
# signing_key: store with chmod 600 — anyone holding this can sign as the service
# verify_key:  share publicly — clients use it to verify

signature = sign_data(ciphertext, signing_key)   # 64 bytes

# Client side
verify_signature(ciphertext, signature, verify_key)   # True or raises BadSignatureError
```

Pair sealing with signing for the full pattern: service seals result for owner, signs the sealed payload, owner verifies signature then unseals.

### Store Integration

```python
sealed = store.seal_and_store(shard, keypair.public_key)
s = store.get_sealed(sealed.shard_id)                          # operator: metadata only
decrypted = store.unseal_and_get(sealed.shard_id, keypair.private_key)
```

See [shard-store.md](shard-store.md#sealed-shards) for the lifecycle of sealed shards in storage.

## Entitlement Tokens

An entitlement token packages decryption keys + scopes + capabilities + budget into a single bearer credential. Sub-agents present the token; the store validates and decrypts only what's entitled.

```python
from spiritwriter.fabric.entitlement import (
    create_entitlement, Capability,
    validate_capability, validate_scope, is_expired,
    get_shard_key, serialize_token, deserialize_token,
)

token = create_entitlement(
    granted_to="script-writer",
    granted_by="producer",
    shard_keys={encrypted.shard_id: key},     # raw bytes; create_entitlement serializes internally
    scopes=["project:*", "user:aaron"],       # fnmatch patterns — ":" is not special
    capabilities=[Capability.SHARD_READ, Capability.WEB_SEARCH],
    secrets=["ANTHROPIC_API_KEY"],            # named secrets sub-agent can request
    budget_usd=10.0,
    expires_at="2026-12-31T23:59:59Z",        # optional
)

assert validate_capability(token, Capability.SHARD_READ)
assert validate_scope(token, "project:myapp")    # matches "project:*"
assert not is_expired(token)

key = get_shard_key(token, encrypted.shard_id)   # returns bytes (deserialized)

token_json = serialize_token(token)
restored = deserialize_token(token_json)
```

Pass `key` as raw bytes. `create_entitlement` calls `serialize_key()` internally on each value; passing pre-serialized strings double-serializes and breaks decryption.

### Capabilities

| Capability | Grants |
|-----------|--------|
| `SHARD_READ` | Decrypt and hydrate entitled shards |
| `SHARD_WRITE` | Create or update shards |
| `KB_CREATE` | Create knowledge bases |
| `KB_PRODUCE` | Produce knowledge base content |
| `WEB_SEARCH` | Web search |
| `WEB_FETCH` | Fetch URLs |
| `EXEC_RUN` | Execute commands |
| `UPLOAD_YOUTUBE` | Upload to YouTube |

### Entitlement-Aware Hydration

```python
context = store.hydrate_with_entitlement(token)
```

The store validates expiry → `SHARD_READ` capability → per-shard scope match against the token's `scopes` patterns. Any failure raises `PermissionError` *before* decryption. Shards the token doesn't entitle (or that aren't on disk) are skipped silently — possibly resolvable later from a DHT.

See [shard-store.md](shard-store.md#entitlement-aware-hydration) for the full validation order.

## Threat Model

What the layers protect against, and what they don't:

| Threat | AES | Sealed | Notes |
|--------|-----|--------|-------|
| Operator reads content | No | **Yes** | AES requires a cooperating operator |
| Storage compromise (cold) | **Yes** | **Yes** | Both leave only ciphertext on disk |
| Network interception | **Yes** | **Yes** | Content-addressed, integrity-checked |
| Tampered ciphertext | **Yes** (GCM) | **Yes** (Poly1305) | Auth tag detects modification |
| Tampered ciphertext + metadata swap | **Yes** | **Yes** | `content_hash` catches whole-payload swaps |
| Key compromise → past shards | No | No | No forward secrecy at this layer |
| Replay (storing the same shard twice) | n/a | n/a | Idempotent by content address — re-store is a no-op |
| Impersonation of result producer | No | No | Use Ed25519 signing for that |

### What This Layer Is Not

- **Not perfect forward secrecy.** A leaked key compromises every shard encrypted with it. Rotate by re-encrypting under a new key (mint a new shard, since the underlying plaintext is the same).
- **Not a key-management system.** Generating, storing, distributing, and rotating keys is your problem. See [entitlement tokens](#entitlement-tokens) for the per-job key-distribution pattern, but the root key trust is up to you.
- **Not access control by itself.** A bare encrypted shard is reachable by anyone who can reach the store. Access control comes from gating *who gets the key* — entitlement tokens, sealed-box ownership, or your application layer.
- **Not metadata-private.** Scope, atom count, origin, timestamps are all visible. If you need scope privacy, encrypt the scope string before constructing the shard.
