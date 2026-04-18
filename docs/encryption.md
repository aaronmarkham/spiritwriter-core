# Encryption & Sealed Shards

spiritwriter-core provides two layers of encryption for protecting shard content, plus an entitlement system for managing access between agents.

## Layer 1: AES-256-GCM (Symmetric)

For agent-to-agent sharing where both sides cooperate. The encryption key is shared via entitlement tokens.

### Encrypt and Decrypt

```python
from spiritwriter.trace.shard import MemoryShard, ShardAtom, AtomKind, DecayClass
from spiritwriter.trace.crypto import (
    generate_job_key, encrypt_shard, decrypt_shard,
    serialize_key, deserialize_key, DecryptionError,
)

# Create shard
shard = MemoryShard(
    atoms=[ShardAtom(text="Sensitive project data", kind=AtomKind.FACT,
                     entity="project", key="api_key", value="sk-...")],
    scope="project:secrets",
    origin="admin-agent",
)

# Generate 32-byte random key
key = generate_job_key()

# Encrypt
encrypted = encrypt_shard(shard, key)
print(encrypted.shard_id)       # same as original shard
print(encrypted.scope)          # "project:secrets" (visible)
print(encrypted.atom_count)     # 1 (visible)
print(encrypted.content_hash)   # SHA-256 of plaintext (for integrity)
# encrypted.encrypted_payload   # AES-256-GCM ciphertext (opaque)

# Decrypt
decrypted = decrypt_shard(encrypted, key)
assert decrypted.shard_id == shard.shard_id

# Wrong key raises DecryptionError
try:
    decrypt_shard(encrypted, generate_job_key())
except DecryptionError as e:
    print(f"Expected: {e}")
```

### Key Serialization

Keys can be serialized for transport (base64url):

```python
key_str = serialize_key(key)    # "Abc123..." (base64url)
key_back = deserialize_key(key_str)  # bytes
assert key == key_back
```

### EncryptedShard Fields

| Field | Visible? | Description |
|-------|----------|-------------|
| `shard_id` | Yes | Content address of the original shard |
| `scope` | Yes | Entitlement boundary |
| `encrypted_payload` | No | AES-256-GCM ciphertext |
| `nonce` | No | 12-byte random nonce |
| `atom_count` | Yes | Number of atoms (for cost estimation) |
| `created_at` | Yes | ISO timestamp |
| `origin_agent` | Yes | Creating agent |
| `content_hash` | Yes | SHA-256 of plaintext (integrity verification) |

### Store Integration

```python
from spiritwriter.trace.store import ShardStore

store = ShardStore("~/.myapp/shards")

# Encrypt + store in one call
encrypted = store.encrypt_and_store(shard, key)

# Retrieve encrypted (without decrypting)
enc = store.get_encrypted(encrypted.shard_id)

# Decrypt + retrieve in one call
decrypted = store.decrypt_and_get(encrypted.shard_id, key)
```

## Layer 2: NaCl Sealed Boxes (Asymmetric)

For zero-knowledge scenarios where the operator/service should NOT be able to read shard content. Uses X25519 + XSalsa20-Poly1305 via PyNaCl.

**Requires:** `pip install 'spiritwriter-core[sealed]'`

### Use Cases

- **Zero-knowledge monitoring** — Families search for detained people without the operator seeing search terms (frio)
- **Multi-tenant hosting** — Shards stored on shared infrastructure without operator access
- **Source protection** — Journalistic or legal data where custody chain matters
- **Result delivery** — Encrypt results so only the requestor can read them

### Key Management

```python
from spiritwriter.trace.sealed import (
    OwnerKeypair, generate_owner_keypair,
    seal_for_owner, unseal_as_owner,
)

# Generate a fresh keypair
keypair = generate_owner_keypair()
print(keypair.public_key_b64)   # shareable — stored with shard
print(keypair.private_key_b64)  # capability key — ONLY the owner keeps this

# Reconstruct from private key (capability key)
restored = OwnerKeypair.from_private_b64(keypair.private_key_b64)

# Service-side: seal-only keypair (no private key)
service_kp = OwnerKeypair.from_public_b64(keypair.public_key_b64)
```

### Seal and Unseal Raw Data

```python
# Service encrypts data for the owner
plaintext = b'{"name": "John Smith", "booking": "2024-1234"}'
ciphertext = seal_for_owner(plaintext, keypair.public_key)

# Owner decrypts with their private key
decrypted = unseal_as_owner(ciphertext, keypair.private_key)
assert decrypted == plaintext
```

### Seal and Unseal Shards

```python
from spiritwriter.trace.sealed import seal_shard, unseal_shard, UnsealError

# Seal a complete shard
sealed = seal_shard(shard, keypair.public_key)
print(sealed.shard_id)       # same as original
print(sealed.scope)          # visible
print(sealed.atom_count)     # visible
print(sealed.content_hash)   # SHA-256 of plaintext (integrity)
# sealed.sealed_payload      # NaCl sealed box (opaque to operator)
# sealed.owner_pubkey        # stored for result delivery

# Unseal with owner's private key
decrypted = unseal_shard(sealed, keypair.private_key)
assert decrypted.shard_id == shard.shard_id
```

### Ed25519 Signing

Sign data for integrity verification — proves results came from a specific service:

```python
from spiritwriter.trace.sealed import (
    generate_signing_keypair, sign_data, verify_signature,
)

# Service generates signing keypair
signing_key, verify_key = generate_signing_keypair()
# signing_key: store securely (chmod 600)
# verify_key: share with clients for verification

# Sign sealed result
signature = sign_data(ciphertext, signing_key)

# Client verifies (with public verify key)
verify_signature(ciphertext, signature, verify_key)  # True or raises
```

### Store Integration

```python
store = ShardStore("~/.myapp/shards")

# Seal + store
sealed = store.seal_and_store(shard, keypair.public_key)

# Retrieve sealed (operator side — can see metadata only)
s = store.get_sealed(sealed.shard_id)

# Unseal + retrieve (owner side)
decrypted = store.unseal_and_get(sealed.shard_id, keypair.private_key)
```

## Entitlement Tokens

Entitlement tokens grant sub-agents scoped access to encrypted shards:

```python
from spiritwriter.trace.entitlement import (
    create_entitlement, EntitlementToken, Capability,
    validate_capability, validate_scope, is_expired,
    get_shard_key, serialize_token, deserialize_token,
)

token = create_entitlement(
    granted_to="script-writer",
    granted_by="producer",
    shard_keys={
        shard.shard_id: serialize_key(key),  # per-shard decryption keys
    },
    scopes=["project:*", "user:aaron"],      # fnmatch patterns
    capabilities=[
        Capability.SHARD_READ,
        Capability.WEB_SEARCH,
    ],
    secrets=["ANTHROPIC_API_KEY"],           # accessible API keys
    budget_usd=10.0,                          # max spend
    expires_at="2026-12-31T23:59:59Z",       # optional expiry
)

# Validate
assert validate_capability(token, Capability.SHARD_READ)
assert validate_scope(token, "project:myapp")  # matches "project:*"
assert not is_expired(token)

# Extract shard key
key = get_shard_key(token, shard.shard_id)  # returns bytes

# Serialize for transport
token_json = serialize_token(token)
restored = deserialize_token(token_json)
```

### Capabilities

| Capability | Description |
|-----------|-------------|
| `SHARD_READ` | Read/decrypt entitled shards |
| `SHARD_WRITE` | Create/update shards |
| `KB_CREATE` | Create knowledge bases |
| `KB_PRODUCE` | Produce knowledge base content |
| `UPLOAD_YOUTUBE` | Upload to YouTube |
| `WEB_SEARCH` | Web search |
| `WEB_FETCH` | Fetch URLs |
| `EXEC_RUN` | Execute commands |

### Entitlement-Aware Hydration

```python
# Sub-agent hydrates with their token
context = store.hydrate_with_entitlement(token)
# Validates: token not expired, has SHARD_READ, scope matches
# Decrypts each entitled shard
# Returns injectable context string
```

## Security Model

| What | Who Can See | How |
|------|-------------|-----|
| Shard scope, metadata | Everyone | Visible on all shard types |
| AES-encrypted content | Key holders (via entitlement) | Symmetric key in token |
| NaCl-sealed content | Owner only (private key) | Sealed box — operator can't decrypt |
| Result integrity | Anyone with verify key | Ed25519 signature |
| Access grants | Token holder | Entitlement token |

### Threat Model

- **Operator compromise**: Sealed shards remain protected. AES shards are at risk if entitlement keys are in memory.
- **Transport interception**: Content-addressed storage means interception gives the attacker ciphertext + visible metadata, but not plaintext.
- **Tampered shards**: Content hash verification detects modification. Hash chains in trace detect event tampering.
