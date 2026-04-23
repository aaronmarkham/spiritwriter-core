# Agent Memory Systems: A Survey

## Section A: Content Addressing

Content-addressed storage uses cryptographic hashes (typically SHA-256) as
identifiers. The same content always produces the same ID, making storage
naturally deduplicated. Git objects, IPFS blocks, and memory shards all
use this pattern. The key insight is that the address *is* the integrity
check — if the content changes, the address changes.

## Section B: Provenance Tracking

Hash-chained event logs provide tamper-evident audit trails. Each event
includes the hash of the previous event, forming an append-only chain.
Breaking any link (inserting, modifying, or deleting an event) invalidates
the chain. This is the same principle behind blockchain, but without
consensus overhead — a single agent emits its own chain.

## Section C: Access Control

Entitlement tokens grant scoped access to encrypted shards. A token
specifies which shards can be decrypted, what capabilities are allowed
(read, write, execute), and a budget ceiling. The issuing agent controls
what a sub-agent can do without trusting it to self-regulate.

## Section D: Entity Resolution

Cross-document entity resolution maps different mentions to the same
canonical identity. "John Smith", "J. Smith", and "Smith, J." might all
refer to the same person. Tiered confidence matching (T1: exact, T2:
strong, T3: fuzzy) lets agents merge knowledge at the right confidence
level without false positives.
