# Entitlements

An **entitlement token** is a bearer credential that bundles four things into one object: decryption keys for specific shards, scope patterns the holder is allowed to reach, capabilities the holder is allowed to invoke, and a budget the holder is allowed to spend. A main agent issues a token to a sub-agent; the sub-agent presents it to the store; the store enforces every constraint before decrypting.

Tokens are the access-control layer that sits on top of [encryption](encryption.md). Encryption keeps content private at rest. Entitlements decide who gets the keys, for which shards, with what permissions, for how long.

## What's In a Token

```python
@dataclass
class EntitlementToken:
    token_id: str                    # uuid4, stable identifier for trace correlation
    granted_to: str                  # sub-agent identity
    granted_by: str                  # issuing agent identity
    shard_keys: dict[str, str]       # {shard_id: serialized_key} — the actual decryption material
    scopes: list[str]                # fnmatch patterns the holder may reach (e.g. "project:*")
    capabilities: list[str]          # action verbs the holder may invoke
    secrets: list[str]               # named secrets the holder may request
    budget_usd: float                # spend cap
    created_at: str                  # ISO timestamp
    expires_at: str | None           # ISO timestamp, None for no expiry
    trace_parent: str | None         # link back to the issuing trace event
    constraints: dict                # caller-defined extras (e.g. row limits, per-stage caps)
```

Each field is enforced by a different code path. The store checks `expires_at` and capability + scope before decrypting. The job runner checks `is_expired` and `validate_capability` before hydrating. `BudgetTracker` checks spend against `budget_usd` on each `record()`. None of these depend on the others — strip any of them and the rest still apply.

## Quick Start

```python
from spiritwriter.fabric.crypto import generate_job_key
from spiritwriter.fabric.entitlement import (
    create_entitlement, Capability,
    validate_capability, validate_scope, is_expired,
    get_shard_key, serialize_token, deserialize_token,
)
from spiritwriter.fabric.store import ShardStore

store = ShardStore("/path/to/shards")
key = generate_job_key()
encrypted = store.encrypt_and_store(my_shard, key)

token = create_entitlement(
    granted_to="extractor",
    granted_by="orchestrator",
    shard_keys={encrypted.shard_id: key},      # raw bytes; create_entitlement serializes
    scopes=["project:*"],                      # fnmatch — covers project:myapp, project:other
    capabilities=[Capability.SHARD_READ, Capability.WEB_SEARCH],
    secrets=["ANTHROPIC_API_KEY"],
    budget_usd=5.0,
    expires_at="2026-12-31T23:59:59Z",
)

# Issuer side: ship the token to the holder
token_str = serialize_token(token)

# Holder side: validate and unpack
restored = deserialize_token(token_str)
assert validate_capability(restored, Capability.SHARD_READ)
assert validate_scope(restored, "project:myapp")    # "project:*" pattern matches
assert not is_expired(restored)
shard_key = get_shard_key(restored, encrypted.shard_id)
```

`create_entitlement` calls `serialize_key()` internally on each value in `shard_keys`. Pass raw bytes — passing pre-serialized strings double-serializes and breaks decryption.

## Capabilities

Capabilities are action verbs. The library declares the names; the application decides what each one gates.

| Capability | Conventional meaning |
|---|---|
| `Capability.SHARD_READ` | Decrypt and hydrate entitled shards |
| `Capability.SHARD_WRITE` | Create or update shards |
| `Capability.KB_CREATE` | Create knowledge bases |
| `Capability.KB_PRODUCE` | Produce knowledge base content |
| `Capability.WEB_SEARCH` | Web search |
| `Capability.WEB_FETCH` | Fetch URLs |
| `Capability.EXEC_RUN` | Execute commands |
| `Capability.UPLOAD_YOUTUBE` | Upload to YouTube |

`Capability.SHARD_READ` is the only one the library itself enforces — `ShardStore.hydrate_with_entitlement()` rejects tokens that lack it. The rest are conventions: your application code calls `validate_capability(token, Capability.WEB_SEARCH)` before allowing a web search, and emits a `capability_checked` event whether the check passes or fails. The audit trail then shows what an agent did *and* what it tried to do and was prevented from doing.

Add custom capability strings if you need them — `capabilities` is a `list[str]`, not an enum-restricted set. `Capability` is a constants holder, not an `Enum`.

## Scopes

Scopes are `fnmatch` patterns matched against each shard's `scope` attribute.

| Pattern | Matches |
|---|---|
| `project:myapp` | Exactly `project:myapp` |
| `project:*` | Any `project:` scope (the `:` is not special) |
| `user:aaron` | Exactly `user:aaron` |
| `*` | Everything (avoid in production tokens) |

Multiple patterns combine with OR — a shard passes if *any* pattern in `token.scopes` matches.

```python
from spiritwriter.fabric.entitlement import validate_scope

token = create_entitlement(
    granted_to="...", granted_by="...",
    shard_keys={}, scopes=["project:*", "user:aaron"],
    capabilities=[Capability.SHARD_READ], secrets=[],
    budget_usd=1.0,
)

validate_scope(token, "project:myapp")    # True — matches "project:*"
validate_scope(token, "user:aaron")       # True — exact match
validate_scope(token, "user:bob")         # False — no pattern matches
```

Scope is the layer where you keep one customer's data out of another customer's sub-agent. If two tenants share a store, scope each tenant's shards under `tenant:<id>:...` and issue tokens with `scopes=["tenant:<id>:*"]`. A leaked token then can't reach across tenants.

## Budget

`budget_usd` is a hard cap stored on the token. Three pieces of code enforce it:

1. `validate_budget(token, spent)` — pure function, returns `True` if `spent <= budget_usd`.
2. `BudgetTracker(budget_usd=token.budget_usd, ...)` — accumulates spend across multiple `record(label, amount)` calls, raises `JobRunnerError` on the call that *would* exceed the cap.
3. The application itself, by checking `tracker.can_spend(amount)` before issuing the call.

The library does not autodetect spend from LLM SDK responses — your code reads cost off the response and calls `tracker.record(label, cost)`. See [jobs.md](jobs.md#budget-tracking) for the full pattern.

## Validation Order

When a token is presented to `ShardStore.hydrate_with_entitlement()`, three checks raise `PermissionError` *before* any decryption happens:

1. **Token not expired.** `is_expired(token)` — raises `PermissionError("token expired")` if `expires_at` is in the past.
2. **Token has `SHARD_READ`.** `validate_capability(token, Capability.SHARD_READ)` — raises `PermissionError("token lacks shard:read")` if missing.
3. **Per-shard scope match.** For each shard the token claims keys for, `validate_scope(token, shard.scope)` must pass. Raises `PermissionError(f"token not entitled to scope {scope}")` on the first mismatch.

Each rejection is fail-fast — the store doesn't return partial results from a bad token.

A fourth condition — **shard exists in store** — does *not* raise. Missing shards are skipped silently, on the assumption that they may be on a DHT the resolver hasn't reached. Hydration continues with whichever shards do resolve. If you need to assert a shard was found, check the result's keys against the token's `shard_keys` after hydration.

See [shard-store.md](shard-store.md#entitlement-aware-hydration) for the calling-side details.

## Trace Events

Every entitlement decision is worth recording. `TraceEmitter` has helpers for the three common events:

```python
from spiritwriter.fabric.emitter import TraceEmitter

emitter = TraceEmitter(run_id="run-001", agent_id="orchestrator", out_path="/tmp/trace.jsonl")

# Issue
emitter.entitlement_granted(
    token_id=token.token_id,
    granted_to="extractor",
    shard_ids=[encrypted.shard_id],
    scopes=["project:*"],
    capabilities=[Capability.SHARD_READ],
    budget_usd=5.0,
)

# Use — emit on BOTH allowed and denied paths
emitter.capability_checked(
    token_id=token.token_id,
    capability=Capability.WEB_SEARCH,
    allowed=False,
)

emitter.shard_decrypted(
    shard_id=encrypted.shard_id,
    token_id=token.token_id,
    scope="project:myapp",
)
```

`capability_checked` is the most useful event for auditing — it captures the *attempt*, not just successful actions. A trace showing two `capability_checked(allowed=False)` followed by `job_failed` tells the post-incident reviewer that the sub-agent tried to escalate and the entitlement system held the line.

See [tracing.md](tracing.md#entitlement-and-budget-events) for the full set of trace event helpers.

## Serialization

Tokens are JSON, designed to round-trip through any text channel — task prompts, environment variables, file contents, HTTP headers:

```python
serialized = serialize_token(token)        # JSON string, ensure_ascii=False
restored = deserialize_token(serialized)   # EntitlementToken — same shape, re-validate before use
```

Two things to know:

- **`shard_keys` is base64url-encoded after serialization.** `create_entitlement` does this conversion for you on the way in; `deserialize_token` keeps the strings as-is. Use `get_shard_key(token, shard_id)` to get the raw bytes back.
- **`shard_keys` is deliberately outside the cap signing payload.** AES keys may be added or rotated after a token is issued (a granter can hand the same cap to a worker, then later attach keys for newly-encrypted shards). If signing covered `shard_keys`, every key rotation would invalidate the cap. The signing payload covers authority (who, what scope, what caveats); key material is bookkeeping that travels alongside.
- **Re-validate after deserializing.** A serialized token is just text — anyone who held it could have edited it. The store's `hydrate_with_entitlement` re-validates on every call, but if your code uses fields directly (like reading `token.budget_usd` to size a `BudgetTracker`), assume the value could be tampered with and constrain elsewhere.

For wire-format integrity beyond what `verify_chain` provides, sign the token with Ed25519 (see [encryption.md](encryption.md#ed25519-signing-for-result-integrity)) and verify the signature before trusting any field.

## Threat Model

What entitlements protect against:

- **Sub-agent escalation.** A sub-agent that exfiltrates its token still can't reach shards outside `scopes`, invoke capabilities outside `capabilities`, or spend past `budget_usd`.
- **Long-lived credential drift.** `expires_at` bounds the blast radius of a token that ends up somewhere it shouldn't.
- **Cross-tenant leakage.** Per-tenant scope prefixes turn store-wide compromise into per-tenant compromise.
- **Silent overspend.** `BudgetTracker.record()` raises rather than letting a runaway sub-agent burn through the budget.

What entitlements do not protect against:

- **A compromised issuer.** The agent calling `create_entitlement` is trusted. If it's compromised, it can mint any token.
- **Operator collusion.** A token + decrypted content + a captured sub-agent process can re-export the content. Encryption stops at decryption.
- **Replay.** Tokens carry no nonce; a leaked token works until `expires_at`. For tighter bounds, set short expiries and re-issue per task.
- **Side channels.** Capabilities gate actions, not information about actions. A sub-agent that can only `SHARD_READ` can still leak via the trace it emits, the latency of its operations, or the work-product it produces.

Entitlements are the access-control layer, not the threat-modeling layer. Pair them with [trace integrity](tracing.md), [encryption](encryption.md), and your application's own input/output filtering.
