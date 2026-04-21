# API Reference

Complete public API surface of spiritwriter-core.

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

---

## spiritwriter.models

### Enums

#### `AtomType`

Document atom types: `TITLE`, `ABSTRACT`, `SECTION_HEADER`, `PARAGRAPH`, `QUOTE`, `CITATION`, `FIGURE`, `CHART`, `TABLE`, `EQUATION`, `DIAGRAM`, `AUTHOR`, `DATE`, `KEYWORD`

#### `DocumentType`

Document classification: `SCIENTIFIC_PAPER`, `NEWS_ARTICLE`, `BLOG_POST`, `TECHNICAL_REPORT`, `DATASET_README`, `GOVERNMENT_DOCUMENT`, `GENERIC`

#### `ZoneRole`

Document zone roles: `FRONT_MATTER`, `BODY`, `BACK_MATTER`, `BIOGRAPHICAL`, `BOILERPLATE`

#### `SourceType`

Knowledge source types: `PAPER`, `ARTICLE`, `NOTE`, `DATASET`, `URL`

### Classes

#### `DocumentAtom`

Smallest unit of extracted knowledge from a document.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `atom_id` | `str` | required | Unique identifier |
| `atom_type` | `AtomType` | required | Type of atom |
| `content` | `str` | required | Text content or description |
| `raw_data` | `bytes \| None` | `None` | Image data for figures/tables |
| `source_page` | `int \| None` | `None` | Page number in source |
| `source_location` | `tuple \| None` | `None` | Bounding box (x0, y0, x1, y1) |
| `topics` | `list[str]` | `[]` | Associated topics |
| `entities` | `list[str]` | `[]` | Named entities |
| `relationships` | `list[str]` | `[]` | Related atom_ids |
| `importance_score` | `float` | `0.5` | Importance (0-1) |
| `caption` | `str \| None` | `None` | Caption for figures/tables |
| `figure_number` | `str \| None` | `None` | Figure/table label |
| `data_summary` | `str \| None` | `None` | LLM-generated description |

**Methods:** `to_dict() -> dict`

#### `DocumentGraph`

Knowledge graph of atoms from a single document.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `document_id` | `str` | required | Unique document ID |
| `source_path` | `str` | required | Path to source file |
| `atoms` | `dict[str, DocumentAtom]` | `{}` | atom_id -> DocumentAtom |
| `hierarchy` | `dict[str, list[str]]` | `{}` | Parent -> child atom_ids |
| `flow` | `list[str]` | `[]` | Reading order (atom_ids) |
| `one_sentence` | `str` | `""` | One-sentence summary |
| `one_paragraph` | `str` | `""` | Paragraph summary |
| `full_summary` | `str` | `""` | Full summary |
| `figures` | `list[str]` | `[]` | Figure atom_ids |
| `tables` | `list[str]` | `[]` | Table atom_ids |
| `title` | `str` | `""` | Document title |
| `authors` | `list[str]` | `[]` | Document authors |
| `page_count` | `int` | `0` | Number of pages |

**Properties:** `atom_count -> int`
**Methods:**
- `get_atom(atom_id) -> DocumentAtom | None`
- `get_atoms_by_type(atom_type) -> list[DocumentAtom]`
- `get_figures() -> list[DocumentAtom]`
- `get_tables() -> list[DocumentAtom]`
- `get_section(section_name) -> list[DocumentAtom]`
- `get_children(atom_id) -> list[DocumentAtom]`
- `to_dict() -> dict`

#### `KnowledgeSource`

A single source within a knowledge project.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `source_id` | `str` | required | Unique identifier |
| `source_type` | `SourceType` | required | Type of source |
| `title` | `str` | `""` | Source title |
| `authors` | `list[str]` | `[]` | Authors |
| `source_path` | `str \| None` | `None` | File path |
| `document_id` | `str \| None` | `None` | Linked DocumentGraph ID |
| `one_sentence` | `str` | `""` | One-sentence summary |
| `atom_count` | `int` | `0` | Extracted atoms |
| `tags` | `list[str]` | `[]` | User tags |

**Methods:** `to_dict() -> dict`, `from_dict(d) -> KnowledgeSource`

#### `KnowledgeGraph`

Unified knowledge graph spanning all sources in a project.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `project_id` | `str` | required | Parent project ID |
| `atoms` | `dict[str, DocumentAtom]` | `{}` | All atoms from all sources |
| `atom_sources` | `dict[str, str]` | `{}` | atom_id -> source_id |
| `cross_links` | `list[CrossSourceLink]` | `[]` | Cross-source relationships |
| `topic_index` | `dict[str, list[str]]` | `{}` | topic -> atom_ids |
| `entity_index` | `dict[str, list[str]]` | `{}` | entity -> atom_ids |
| `unified_summary` | `str` | `""` | Graph-level summary |
| `key_themes` | `list[str]` | `[]` | Themes across sources |

**Properties:** `atom_count`, `source_count`, `cross_link_count`
**Methods:**
- `get_atoms_for_source(source_id) -> list[DocumentAtom]`
- `get_shared_topics() -> dict[str, list[str]]` — Topics in multiple sources
- `get_shared_entities() -> dict[str, list[str]]` — Entities in multiple sources
- `to_dict() -> dict`, `from_dict(d) -> KnowledgeGraph`

#### `CrossSourceLink`

Relationship between atoms from different sources.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `link_id` | `str` | required | Unique identifier |
| `source_atom_id` | `str` | required | Source atom |
| `target_atom_id` | `str` | required | Target atom |
| `source_source_id` | `str` | required | Source's source_id |
| `target_source_id` | `str` | required | Target's source_id |
| `relationship` | `str` | required | Type: "supports", "contradicts", "same_topic" |
| `confidence` | `float` | `0.5` | Confidence (0-1) |

#### `KnowledgeProject`

Top-level container for a knowledge base.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `project_id` | `str` | required | Unique project ID |
| `name` | `str` | required | Project name |
| `description` | `str` | `""` | Description |
| `sources` | `dict[str, KnowledgeSource]` | `{}` | source_id -> KnowledgeSource |
| `notes` | `dict[str, Note]` | `{}` | note_id -> Note |
| `connections` | `list[Connection]` | `[]` | User-defined connections |
| `has_knowledge_graph` | `bool` | `False` | Whether graph is built |
| `total_atoms` | `int` | `0` | Aggregate atom count |

**Properties:** `source_count -> int`
**Methods:**
- `add_source(source) -> None` — Add source, update counts
- `get_source(source_id) -> KnowledgeSource | None`
- `to_dict() -> dict`, `from_dict(d) -> KnowledgeProject`

#### `ContentProfile`

Document classification and extraction guidance (produced by ContentClassifier).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `document_type` | `DocumentType` | required | Classification result |
| `confidence` | `float` | required | Classification confidence (0-1) |
| `zones` | `list[DocumentZone]` | `[]` | Structural zones |
| `detected_authors` | `list[str]` | `[]` | Early-detected authors |
| `detected_doi` | `str \| None` | `None` | Detected DOI |
| `topic_extraction_zones` | `list[ZoneRole]` | `[]` | Zones for topic extraction |
| `entity_extraction_zones` | `list[ZoneRole]` | `[]` | Zones for entity extraction |

**Methods:** `is_metadata_block(block_index) -> bool`, `get_zone_for_block(block_index) -> DocumentZone | None`

### Functions

- `generate_id(prefix, seed) -> str` — Short hash ID with prefix (e.g., `"kb_a1b2c3d4"`)

---

## spiritwriter.kb

Knowledge base CRUD — create, load, save, and rebuild knowledge graphs.

### `KnowledgeBaseManager(kb_dir)`

High-level manager wrapping module functions with a configurable directory.

- `resolve(project) -> Path | None` — Resolve project name/ID/prefix to directory
- `load(project_dir) -> KnowledgeProject` — Load project from directory
- `save(project_dir, project) -> None` — Save project to directory
- `rebuild_graph(project_dir, project) -> KnowledgeGraph` — Rebuild unified graph
- `build_concept(proj, kg, prompt, source_filter=None) -> str` — Build rich concept string for LLM

### Module Functions

- `resolve_project(project, kb_dir) -> Path | None` — Resolve project identifier to directory
- `load_project(project_dir) -> KnowledgeProject` — Load from directory
- `save_project(project_dir, project) -> None` — Save to directory
- `rebuild_knowledge_graph(project_dir, project) -> KnowledgeGraph` — Merge atoms, build indices, detect cross-links
- `build_concept_from_kb(proj, kg, prompt, source_filter=None) -> str` — Assemble structured context (~4000 chars) from KB content
- `calculate_topic_quality(topics, atom_sources) -> dict` — Analyze topic quality, detect noise
- `calculate_entity_quality(entities) -> dict` — Categorize entities (acronyms, proper names, other)
- `get_atom_type_distribution(atoms) -> dict[str, int]` — Count atoms by type

---

## spiritwriter.secrets

Secure API key management using OS-native credential storage with environment variable fallback.

Uses `keyring` for cross-platform keychain access (Windows Credential Manager, macOS Keychain, Linux Secret Service).

### Functions

- `configure(service_name=None, extra_keys=None) -> None` — Set keychain service name and register additional keys
- `register_keys(keys) -> None` — Register additional key names for tracking
- `get_api_key(key_name, fallback_to_env=True) -> str | None` — Retrieve key (keychain first, then env)
- `set_api_key(key_name, value) -> bool` — Store key in keychain
- `delete_api_key(key_name) -> bool` — Delete key from keychain
- `list_api_keys() -> dict` — All known keys with status ("keychain", "env", "not_set")
- `import_from_env_file(env_path) -> dict` — Import keys from .env file to keychain
- `is_keyring_available() -> bool` — Check if keychain backend is functional

### CLI (`spiritwriter secrets`)

- `spiritwriter secrets list` — List all keys and status
- `spiritwriter secrets set KEY_NAME` — Store key (prompts for value)
- `spiritwriter secrets get KEY_NAME` — Show key (masked)
- `spiritwriter secrets delete KEY_NAME` — Delete key
- `spiritwriter secrets import .env` — Import from .env file
- `spiritwriter secrets check` — Check keyring backend status

---

## spiritwriter.llm

LLM provider abstraction with automatic SDK fallback.

### `LLMProvider` (Abstract Base)

- `async query(prompt, *, return_usage=False, **kwargs) -> str | tuple[str, dict]` — Send prompt, get response
- `async query_with_image(prompt, image_data, *, return_usage=False, **kwargs) -> str | tuple[str, dict]` — Vision query

### `AnthropicProvider(debug=False, model=None)`

Concrete Anthropic/Claude implementation. Tries Claude Agent SDK first, falls back to Anthropic SDK. `model` defaults to `DEFAULT_ANTHROPIC_MODEL` (currently `claude-sonnet-4-6`). Individual calls can override by passing `model=` in `**kwargs`.

- `async query(prompt, system_prompt=None, return_usage=False, model=None) -> str | tuple[str, dict]` — Text query (max_tokens: 16384)
- `async query_with_image(prompt, image_data, system_prompt=None, return_usage=False, model=None) -> str | tuple[str, dict]` — Vision query (supports JPEG, PNG, GIF, WebP; max_tokens: 4096)

**Usage dict:** `{"input_tokens": int, "output_tokens": int, "total_tokens": int}`

### `JSONExtractor`

- `@staticmethod extract(response, debug=False) -> dict` — Extract JSON from Claude responses (handles markdown blocks, truncation, escape issues)

**Alias:** `ClaudeClient = AnthropicProvider` (backward compatibility)

---

## spiritwriter.ingest

PDF document ingestion using PyMuPDF + LLM classification.

### `DocumentIngestor(llm_provider=None, mock_mode=False)`

- `async ingest(source_path) -> DocumentGraph` — Full ingestion pipeline: extract -> classify -> analyze -> graph

**Pipeline:**
1. Phase 1: PyMuPDF extracts text blocks, images, metadata
2. Phase 1.5: ContentClassifier detects document type and zones
3. Phase 2: LLM classifies atoms, extracts topics/entities, generates summaries (or mock heuristics if no LLM)

### Extraction Functions

- `extract_with_pymupdf(path, use_rendered_figures=True) -> ExtractionResult` — Raw PDF extraction
- `extract_rendered_figures(path, text_blocks) -> list[dict]` — Caption-driven figure extraction
- `extract_embedded_images(path) -> list[dict]` — Direct image extraction (fallback)

### `ExtractionResult`

| Field | Type | Description |
|-------|------|-------------|
| `text_blocks` | `list[dict]` | Text with page, bbox, font_size, is_bold |
| `images` | `list[dict]` | Images with page, bbox, image_bytes, caption |
| `page_count` | `int` | Total pages |
| `metadata` | `dict` | PDF metadata (title, author, subject, etc.) |

---

## spiritwriter.classify

Pre-LLM document classification using structural heuristics.

### `ContentClassifier`

- `classify(extraction) -> ContentProfile` — Detect document type, identify zones, extract early metadata

### Functions

- `is_theme_candidate(topic) -> bool` — Filter noise: returns False for institutional names, venue names, structural terms
- `is_institutional_name(text) -> bool`
- `is_venue_name(text) -> bool`
- `is_structural_noise(text) -> bool`

---

## spiritwriter.integrations

Pluggable memory system bridges. Auto-discovers installed providers.

### Functions

- `available_providers() -> dict[str, RetrievalProvider]` — All discovered providers
- `get_provider(name) -> RetrievalProvider | None` — Get specific provider
- `register_provider(name, provider) -> None` — Manually register provider

### `RetrievalProvider` (Abstract)

- `info() -> ProviderInfo` — Metadata and capabilities
- `search(query: SearchQuery) -> list[SearchResult]` — Semantic/hybrid search
- `has_capability(cap) -> bool` — Check capability
- `is_available() -> bool` — Ready check

### `StorageProvider` (Abstract)

- `store(documents) -> list[str]` — Persist documents, return IDs
- `get(document_id) -> Document | None` — Retrieve by ID
- `delete(document_id) -> bool` — Delete by ID
- `count() -> int` — Total documents
- `check_duplicate(text, threshold=0.9) -> str | None` — Near-duplicate check

### `EntityProvider` (Abstract)

- `add_triple(triple: EntityTriple) -> str` — Add knowledge graph triple
- `query_entity(entity, as_of=None) -> list[EntityTriple]` — Query relationships
- `invalidate(subject, predicate, obj, ended=None) -> bool` — Mark triple invalid
- `entity_stats() -> dict` — Statistics

### Data Classes

- `SearchQuery(text, top_k=10, filters={}, scope=None, after=None, before=None)`
- `SearchResult(document_id, text, score, metadata={}, shard_id=None, scope=None, source=None)`
- `Document(document_id, text, metadata={})`
- `EntityTriple(subject, predicate, object, valid_from=None, valid_to=None, source_ref=None)`
- `ProviderInfo(name, version, description, capabilities, requires_api_key=False, local_only=True)`
- `ProviderCapability` — Enum: `SEMANTIC_SEARCH`, `KEYWORD_SEARCH`, `HYBRID_SEARCH`, `KNOWLEDGE_GRAPH`, `TEMPORAL_QUERY`, `ENTITY_DETECTION`, `COMPRESSION`, `LLM_RERANK`, `STORAGE`, `DEDUP`
