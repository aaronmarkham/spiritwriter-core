# API Reference

Complete public API surface of `spiritwriter.trace`.

## spiritwriter.trace.shard

### Classes

#### `MemoryShard`

Content-addressed bundle of knowledge atoms.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `atoms` | `list[ShardAtom]` | required | Knowledge atoms |
| `scope` | `str` | required | Entitlement boundary |
| `origin` | `str` | required | Creating agent ID |
| `decay_class` | `DecayClass` | `STABLE` | TTL classification |
| `created_at` | `str` | now (UTC ISO) | Creation timestamp |
| `trace_ref` | `str \| None` | `None` | Link to trace chain |
| `parent_shard_id` | `str \| None` | `None` | Predecessor shard ID |
| `tags` | `list[str]` | `[]` | Human-readable labels |
| `meta` | `dict[str, Any]` | `{}` | Application metadata |
| `last_checked` | `str \| None` | `None` | Last verification timestamp |
| `check_count` | `int` | `0` | Verification cycle count |

**Properties:**
- `shard_id: str` — SHA-256 content address (deterministic)
- `ref: ShardRef` — Lightweight pointer
- `token_estimate: int` — Rough token count for hydration

**Methods:**
- `to_dict() -> dict` — Serialize to dict
- `to_json() -> str` — Canonical JSON
- `from_dict(d) -> MemoryShard` — Deserialize (validates content address)
- `from_json(raw) -> MemoryShard` — From JSON string
- `get_atom(key) -> ShardAtom | None` — Find atom by key
- `hydrate_context() -> str` — Render as injectable XML-tagged context

#### `ShardAtom`

Single knowledge atom.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `text` | `str` | required | Content text |
| `kind` | `AtomKind` | `CONTEXT` | Semantic type |
| `entity` | `str \| None` | `None` | Subject entity |
| `key` | `str \| None` | `None` | Structured field name |
| `value` | `str \| None` | `None` | Structured field value |
| `confidence` | `float` | `1.0` | Extraction confidence (0.0-1.0) |
| `source_ref` | `str \| None` | `None` | Trace event ID or doc reference |

**Properties:**
- `content_hash: str` — SHA-256 of (text, kind, entity, key, value)

#### `ShardRef`

Lightweight pointer to a shard.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `shard_id` | `str` | required | Content address |
| `scope` | `str` | required | Entitlement boundary |
| `label` | `str \| None` | `None` | Human-readable hint |
| `origin` | `str \| None` | `None` | Creating agent |

#### `DecayClass` (Enum)

| Value | TTL | Description |
|-------|-----|-------------|
| `PERMANENT` | Never | Identities, architecture decisions |
| `STABLE` | 90 days | Project details, relationships |
| `ACTIVE` | 14 days | Current tasks, active monitoring |
| `SESSION` | 24 hours | Debugging context, temp state |
| `CHECKPOINT` | 4 hours | Pre-flight state saves |

#### `AtomKind` (Enum)

`FACT`, `DECISION`, `CONVENTION`, `PREFERENCE`, `ENTITY`, `CONTEXT`, `INSTRUCTION`, `CHECKPOINT`

---

## spiritwriter.trace.store

### `ShardStore(root, resolver=None)`

Content-addressed shard storage.

| Parameter | Type | Description |
|-----------|------|-------------|
| `root` | `str \| Path` | Storage root directory |
| `resolver` | `NetworkResolver \| None` | Optional L2 network fallback |

**Core Operations:**
- `put(shard) -> ShardRef` — Store shard (idempotent)
- `get(shard_id) -> MemoryShard | None` — Retrieve (L1 local, L2 network)
- `has(shard_id) -> bool` — Local existence check
- `delete(shard_id) -> bool` — Remove shard

**Query:**
- `resolve(ref) -> MemoryShard | None` — Resolve ref to shard
- `resolve_many(refs) -> list[MemoryShard]` — Batch resolve (skips missing)
- `hydrate(refs) -> str` — Resolve + render context
- `by_scope(scope) -> list[MemoryShard]` — All shards in scope
- `list_scopes() -> list[str]` — All scopes
- `iter_all() -> Iterator[MemoryShard]` — Iterate all shards
- `count() -> int` — Total shard count

**Named Refs:**
- `set_ref(name, shard_id)` — Create/update named ref
- `get_ref(name) -> str | None` — Resolve name to shard_id
- `resolve_ref(name) -> MemoryShard | None` — Resolve name to shard
- `delete_ref(name) -> bool` — Remove named ref
- `list_refs(prefix="") -> list[str]` — List ref names

**Scope:**
- `move_scope(shard_id, new_scope) -> MemoryShard` — Copy to new scope

**Encrypted:**
- `put_encrypted(encrypted) -> str` — Store EncryptedShard
- `get_encrypted(shard_id) -> EncryptedShard | None` — Retrieve
- `has_encrypted(shard_id) -> bool`
- `encrypt_and_store(shard, key) -> EncryptedShard` — Encrypt + store
- `decrypt_and_get(shard_id, key) -> MemoryShard` — Retrieve + decrypt

**Sealed:**
- `put_sealed(sealed) -> str` — Store SealedShard
- `get_sealed(shard_id) -> SealedShard | None` — Retrieve
- `has_sealed(shard_id) -> bool`
- `seal_and_store(shard, owner_pubkey) -> SealedShard` — Seal + store
- `unseal_and_get(shard_id, owner_private_key) -> MemoryShard` — Retrieve + unseal

**Entitlement:**
- `hydrate_with_entitlement(token) -> str` — Validate + decrypt + render

**Maintenance:**
- `prune_expired() -> int` — Remove expired shards
- `stats() -> dict` — Summary statistics

---

## spiritwriter.trace.crypto

### `EncryptedShard`

AES-256-GCM encrypted shard.

| Field | Type | Description |
|-------|------|-------------|
| `shard_id` | `str` | Content address (visible) |
| `scope` | `str` | Entitlement boundary (visible) |
| `encrypted_payload` | `bytes` | Ciphertext |
| `nonce` | `bytes` | 12-byte random nonce |
| `atom_count` | `int` | Atom count (visible) |
| `created_at` | `str` | Timestamp (visible) |
| `origin_agent` | `str` | Creating agent (visible) |
| `content_hash` | `str` | SHA-256 of plaintext |

### Functions

- `generate_job_key() -> bytes` — 32-byte random key
- `encrypt_shard(shard, key) -> EncryptedShard`
- `decrypt_shard(encrypted, key) -> MemoryShard` — Raises `DecryptionError`
- `serialize_key(key) -> str` — Base64url
- `deserialize_key(s) -> bytes` — Validates 32-byte length

### Exceptions

- `DecryptionError` — Wrong key or corrupted data

---

## spiritwriter.trace.sealed

### Classes

#### `OwnerKeypair`

| Field | Type | Description |
|-------|------|-------------|
| `private_key` | `bytes` | 32-byte Curve25519 private key |
| `public_key` | `bytes` | 32-byte Curve25519 public key |

**Properties:** `private_key_b64`, `public_key_b64`
**Class methods:** `from_private_b64(s)`, `from_public_b64(s)`

#### `SealedShard`

NaCl sealed-box encrypted shard.

| Field | Type | Description |
|-------|------|-------------|
| `shard_id` | `str` | Content address (visible) |
| `scope` | `str` | Entitlement boundary (visible) |
| `sealed_payload` | `bytes` | NaCl sealed box |
| `owner_pubkey` | `bytes` | Owner's public key |
| `atom_count` | `int` | Atom count (visible) |
| `created_at` | `str` | Timestamp (visible) |
| `origin_agent` | `str` | Creating agent (visible) |
| `content_hash` | `str` | SHA-256 of plaintext |

### Functions

**Key Management:**
- `generate_owner_keypair() -> OwnerKeypair`

**Seal/Unseal (raw bytes):**
- `seal_for_owner(plaintext, owner_pubkey) -> bytes`
- `unseal_as_owner(ciphertext, owner_private_key) -> bytes`

**Seal/Unseal (shards):**
- `seal_shard(shard, owner_pubkey) -> SealedShard`
- `unseal_shard(sealed, owner_private_key) -> MemoryShard` — Raises `UnsealError`

**Signing (Ed25519):**
- `generate_signing_keypair() -> tuple[bytes, bytes]` — (signing_key, verify_key)
- `sign_data(data, signing_key) -> bytes` — 64-byte signature
- `verify_signature(data, signature, verify_key) -> bool` — Raises `BadSignatureError`

### Exceptions

- `UnsealError` — Wrong key or tampered data

---

## spiritwriter.trace.canonicalize

### Classes

#### `EntitySenseSig`

Content-addressed identity anchor.

| Field | Type | Description |
|-------|------|-------------|
| `fields` | `tuple[tuple[str, str], ...]` | Sorted (key, value) pairs |
| `digest` | `str` | SHA-256 of canonical fields |

**Class methods:** `compute(**fields) -> EntitySenseSig`
**Methods:** `overlap(other) -> float` (0.0-1.0)

#### `ResolutionTier` (Enum)

| Value | Confidence | Auto-Merge |
|-------|-----------|------------|
| `T1_EXACT` | 0.95 | Yes |
| `T2_STRONG` | 0.85 | Yes |
| `T3_FUZZY` | 0.70 | No |
| `T4_WEAK` | 0.50 | No |
| `NO_MATCH` | 0.0 | N/A |

#### `ResolutionResult`

| Field | Type | Description |
|-------|------|-------------|
| `tier` | `ResolutionTier` | Match tier |
| `confidence` | `float` | Adjusted confidence |
| `canonical_id` | `str \| None` | Matched entity ID |
| `field_matches` | `dict[str, bool]` | Per-field breakdown |
| `notes` | `str` | Human-readable explanation |

#### `CanonicalSchema`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | required | Domain name |
| `ess_fields` | `list[str]` | required | Fields for ESS hash |
| `fuzzy_fields` | `dict[str, float]` | required | Field → min threshold |
| `context_fields` | `list[str]` | `[]` | Contextual fields |
| `metadata_fields` | `list[str]` | `[]` | Informational fields |
| `age_bucket_size` | `int` | `2` | Age bucketing |
| `temporal_window_days` | `int` | `7` | Proximity window |

#### `CanonicalRegistry(db_path, schema, emitter=None)`

**Resolution:**
- `resolve(candidate) -> ResolutionResult` — Read-only resolution
- `upsert(candidate, resolution, source_name, source_id, raw=None) -> str` — Persist
- `merge(keep_id, discard_id, reason="")` — Manual merge

**Query:**
- `get_entity(canonical_id) -> dict | None` — With sightings
- `find_by_ess(ess) -> list[dict]` — Exact ESS lookup
- `find_fuzzy(fields, limit=10) -> list[ResolutionResult]` — Similarity search
- `entities(since=None) -> Iterator[dict]` — Iterate entities
- `sightings(canonical_id) -> list[dict]` — Source records
- `stats() -> dict` — Registry statistics

**Lifecycle:** `close()`, context manager (`with` statement)

### Functions

- `normalize_name(s) -> str` — Uppercase, strip, collapse whitespace
- `normalize_date(s) -> str | None` — Various formats → ISO 8601
- `age_to_bucket(age, bucket_size=2) -> str` — e.g., "42-43"
- `fuzzy_score(a, b) -> float` — 0.0-1.0 similarity
- `canonicalize_batch(records, registry, source_name, source_id_field="source_id") -> list[tuple]`

---

## spiritwriter.trace.emitter

### `TraceEmitter(run_id, agent_id, out_path, signer=None)`

Hash-chained JSONL event emitter.

**Core:**
- `emit(event_type, **kwargs) -> dict` — Emit custom event

**Shard events:**
- `shard_created(shard_id, scope, atom_count, **kw) -> dict`
- `shard_resolved(shard_id, by_agent, **kw) -> dict`
- `shard_superseded(old_shard_id, new_shard_id, **kw) -> dict`
- `spawn_with_shards(child_agent_id, shard_refs, task, **kw) -> dict`

**Entitlement events:**
- `entitlement_granted(token_id, granted_to, shard_ids, scopes, capabilities, budget_usd, **kw) -> dict`
- `shard_decrypted(shard_id, token_id, scope, **kw) -> dict`
- `capability_checked(token_id, capability, allowed, **kw) -> dict`
- `budget_spent(token_id, label, amount, total_spent, budget_usd, **kw) -> dict`

**Studio events:**
- `studio_job_packaged(content_shard_id, task_shard_id, token_id, budget_usd, **kw) -> dict`
- `studio_job_started(token_id, content_shard_id, task_shard_id, prompt=None, **kw) -> dict`
- `studio_job_completed(token_id, result_shard_id, spent_usd, outputs=None, **kw) -> dict`
- `studio_job_failed(token_id, error, spent_usd=0.0, **kw) -> dict`

**Decision:**
- `decision_extracted(shard_id, decision_text, entity=None, rationale=None, **kw) -> dict`

**Utility:**
- `get_events() -> list[dict]` — Read all events from file

### `verify_chain(events) -> bool`

Verify hash chain integrity. Returns `True` if valid or empty.

---

## spiritwriter.trace.entitlement

### `EntitlementToken`

| Field | Type | Description |
|-------|------|-------------|
| `token_id` | `str` | UUID |
| `granted_to` | `str` | Agent identity |
| `granted_by` | `str` | Issuer |
| `shard_keys` | `dict[str, str]` | Per-shard decryption keys (serialized) |
| `scopes` | `list[str]` | fnmatch patterns |
| `capabilities` | `list[str]` | Allowed actions |
| `secrets` | `list[str]` | Accessible API keys |
| `budget_usd` | `float` | Max spend |
| `created_at` | `str` | ISO timestamp |
| `expires_at` | `str \| None` | UTC expiry |
| `trace_parent` | `str \| None` | Trace chain link |
| `constraints` | `dict` | Application rules |

### `Capability` (Enum)

`SHARD_READ`, `SHARD_WRITE`, `KB_CREATE`, `KB_PRODUCE`, `UPLOAD_YOUTUBE`, `WEB_SEARCH`, `WEB_FETCH`, `EXEC_RUN`

### Functions

- `create_entitlement(granted_to, granted_by, shard_keys, scopes, capabilities, secrets=None, budget_usd=0.0, expires_at=None, constraints=None) -> EntitlementToken`
- `validate_capability(token, action) -> bool`
- `validate_scope(token, scope) -> bool` — fnmatch pattern matching
- `validate_budget(token, spent) -> bool`
- `is_expired(token) -> bool`
- `get_shard_key(token, shard_id) -> bytes` — Raises `KeyError`
- `serialize_token(token) -> str` — JSON
- `deserialize_token(s) -> EntitlementToken`
