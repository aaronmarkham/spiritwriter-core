# Skill: Spiritwriter Entitlements

Encrypted shards with scoped access tokens for secure sub-agent knowledge sharing.

## When to Use

- You need to **encrypt a shard** so only authorized agents can read it
- You need to **grant scoped access** (specific shards, capabilities, budget, expiry)
- You need to **validate access** before hydrating sensitive context
- You need **audit trail** of who accessed what (integrates with Trace)

## Install

```bash
pip install -e /path/to/spiritwriter-core
```

## Concepts

| Concept | What it is |
|---------|-----------|
| **EncryptedShard** | AES-256-GCM encrypted shard. Only shard_id and scope visible; content requires key. |
| **EntitlementToken** | Access grant: scopes (fnmatch patterns), capabilities, shard keys, budget, expiry. |
| **Capability** | What the token allows: `shard:read`, `shard:write`, `tool:execute`, `budget:spend`. |

## Python API

### Encrypt a shard

```python
from spiritwriter.trace.crypto import encrypt_shard, decrypt_shard, generate_key

key = generate_key()  # 32 bytes, AES-256
encrypted = encrypt_shard(shard, key)

# Store encrypted version
store.put_encrypted(encrypted)

# Decrypt later
original = decrypt_shard(encrypted, key)
```

### Create an entitlement token

```python
from spiritwriter.trace.entitlement import (
    create_entitlement, Capability
)

token = create_entitlement(
    issuer="lilit",
    subject="sub-agent-007",
    scopes=["project:csp", "user:*"],        # fnmatch patterns
    capabilities=[Capability.SHARD_READ, Capability.BUDGET_SPEND],
    shard_keys={encrypted.shard_id: key},     # per-shard decryption keys
    budget_cents=500,                          # $5.00 max spend
    ttl_seconds=3600,                          # 1 hour expiry
)
```

### Hydrate with entitlement

```python
# Validates expiry, scope, capability, then decrypts
context = store.hydrate_with_entitlement(token)
# Raises PermissionError if token expired, wrong scope, or missing capability
```

### Validate access

```python
from spiritwriter.trace.entitlement import (
    validate_capability, validate_scope, is_expired
)

is_expired(token)                              # bool
validate_capability(token, Capability.SHARD_READ)  # bool
validate_scope(token, "project:csp")           # bool (fnmatch)
```

## Security Model

- **AES-256-GCM**: Authenticated encryption. Tamper = decryption failure.
- **Per-shard keys**: Each shard gets its own key, embedded in the token.
- **Scope patterns**: fnmatch-style (`project:*` matches `project:csp`).
- **Budget tracking**: Tokens carry a spend cap. Consuming agent tracks usage.
- **Expiry**: UTC timestamp. Rejected after expiry.
- **Trace integration**: Every grant, check, decrypt, and spend is logged to the hash chain.

## Source Files

- `spiritwriter/trace/crypto.py` — AES-256-GCM encrypt/decrypt, EncryptedShard, key generation
- `spiritwriter/trace/entitlement.py` — EntitlementToken, create_entitlement, validate_*, Capability
- `spiritwriter/trace/store.py` — hydrate_with_entitlement(), encrypt_and_store(), decrypt_and_get()
