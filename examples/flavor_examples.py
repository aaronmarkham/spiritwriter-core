"""Generate worked examples for docs/substrate-flavor.md.

Run this script to produce the canonical example values shown in the
flavor document. Re-run after any wire-format change to detect drift —
if the hex values printed here no longer match what's in the doc, the
doc needs updating.

Uses a fixed Ed25519 seed so output is reproducible across runs.
"""

from __future__ import annotations

import json

from spiritwriter.fabric.shard import (
    MemoryShard,
    ShardAtom,
    AtomKind,
    DecayClass,
    pubkey_thumbprint,
    generate_signing_keypair,
)


def canonical_json(obj) -> bytes:
    """Canonical JSON for content addressing — sorted keys, no whitespace,
    UTF-8 with non-ASCII unescaped. This is the rule the substrate flavor
    doc describes; an implementer in another language only needs these
    three options on their JSON encoder."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
from spiritwriter.fabric.entitlement import (
    EntitlementToken,
    Capability,
    Caveat,
    CaveatType,
    create_entitlement,
    issue_delegated,
    serialize_token,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


# === Reproducible keypairs (DO NOT use these seeds in production) ====

ROOT_SEED = bytes.fromhex(
    "0000000000000000000000000000000000000000000000000000000000000001"
)
ORCH_SEED = bytes.fromhex(
    "0000000000000000000000000000000000000000000000000000000000000002"
)
WORKER_SEED = bytes.fromhex(
    "0000000000000000000000000000000000000000000000000000000000000003"
)


def _kp(seed: bytes) -> tuple[bytes, bytes]:
    sk = Ed25519PrivateKey.from_private_bytes(seed)
    sk_bytes = sk.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pk_bytes = sk.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return sk_bytes, pk_bytes


def _print_section(title: str) -> None:
    print(f"\n{'=' * 64}\n{title}\n{'=' * 64}")


def main() -> None:
    root_sk, root_pk = _kp(ROOT_SEED)
    orch_sk, orch_pk = _kp(ORCH_SEED)
    worker_sk, worker_pk = _kp(WORKER_SEED)

    _print_section("KEYS")
    print(f"root_pubkey   = {root_pk.hex()}")
    print(f"orch_pubkey   = {orch_pk.hex()}")
    print(f"worker_pubkey = {worker_pk.hex()}")
    print(f"worker_thumbprint = {pubkey_thumbprint(worker_pk)}")

    # === Example 1: a plaintext memory shard (unsigned) =================

    shard = MemoryShard(
        atoms=[
            ShardAtom(
                text="The Fugaku supercomputer is in Kobe, Japan.",
                kind=AtomKind.FACT,
                entity="Fugaku",
                key="location",
                value="Kobe, Japan",
            ),
        ],
        scope="sw:article:run-abc",
        origin="agent:builder-2",
        decay_class=DecayClass.PERMANENT,
        created_at="2026-05-10T12:00:00Z",
    )

    _print_section("EXAMPLE 1 — UNSIGNED SHARD")
    print(f"shard_id (sha256 hex of canonical {{atoms, scope, origin}}):")
    print(f"  {shard.shard_id}")
    print("\nCanonical JSON of {atoms, scope, origin}:")
    canon = canonical_json({
        "atoms": [a.to_dict() for a in shard.atoms],
        "scope": shard.scope,
        "origin": shard.origin,
    })
    print(f"  {canon.decode('utf-8')}")
    print("\nFull serialized shard:")
    print(json.dumps(json.loads(shard.to_json()), indent=2, sort_keys=True))

    # === Example 2: a 3-link cap chain ==================================

    root = create_entitlement(
        granted_to="aaron",
        granted_by="self",
        shard_keys={},
        scopes=["sw:*"],
        capabilities=[Capability.SHARD_READ, Capability.SHARD_WRITE],
        secrets=[],
        budget_usd=100.0,
    )
    # Pin the random uuid + timestamp so the example is reproducible.
    root.token_id = "00000000-0000-0000-0000-000000000001"
    root.created_at = "2026-05-10T12:00:00Z"
    root.subject_pubkey = root_pk
    root.caveats = [Caveat(CaveatType.MAX_DELEGATION_DEPTH, 3)]
    root.sign(root_sk)

    orch = issue_delegated(
        root, root_sk,
        subject_pubkey=orch_pk,
        granted_to="orchestrator:run-abc",
        scopes=["sw:article:run-abc:*"],
        capabilities=[Capability.SHARD_WRITE],
        caveats=[Caveat(CaveatType.EXPIRES_AT, "2026-05-10T13:00:00Z")],
    )
    orch.token_id = "00000000-0000-0000-0000-000000000002"
    orch.created_at = "2026-05-10T12:00:00Z"
    orch.signature = None
    orch.sign(root_sk)

    leaf = issue_delegated(
        orch, orch_sk,
        subject_pubkey=worker_pk,
        granted_to="worker:builder-2",
        capabilities=[Capability.SHARD_WRITE],
    )
    leaf.token_id = "00000000-0000-0000-0000-000000000003"
    leaf.created_at = "2026-05-10T12:00:00Z"
    leaf.signature = None
    leaf.sign(orch_sk)

    _print_section("EXAMPLE 2 — CAP CHAIN (root -> orchestrator -> worker)")
    for i, cap in enumerate([root, orch, leaf]):
        label = ["root", "orchestrator", "worker"][i]
        print(f"\n--- {label} (cap_id = {cap.cap_id}) ---")
        print(serialize_token(cap))

    # === Example 3: a signed produced shard linked to the leaf cap ======

    produced = MemoryShard(
        atoms=[
            ShardAtom(
                text="Fugaku is currently the world's fourth-fastest supercomputer.",
                kind=AtomKind.FACT,
                entity="Fugaku",
                key="ranking",
                value="4",
            ),
        ],
        scope="sw:article:run-abc:builder-2",
        origin="agent:builder-2",
        decay_class=DecayClass.PERMANENT,
        created_at="2026-05-10T12:30:00Z",
        cap_id=leaf.cap_id,
    )
    produced.sign(worker_sk)

    _print_section("EXAMPLE 3 — SIGNED PRODUCED SHARD")
    print(f"shard_id     = {produced.shard_id}")
    print(f"created_by   = {produced.created_by}")
    print(f"cap_id       = {produced.cap_id}")
    print(f"signature    = {produced.signature}")
    print("\nFull serialized shard:")
    print(json.dumps(json.loads(produced.to_json()), indent=2, sort_keys=True))

    # === Verification round-trip ========================================
    _print_section("VERIFICATION ROUND-TRIP")
    from spiritwriter.fabric.entitlement import (
        verify_chain as verify_cap_chain,
        authorize_chain,
    )
    chain = [root, orch, leaf]
    verify_cap_chain(chain, root_pubkeys=[root_pk])
    print("[ok] cap chain verified")
    assert authorize_chain(
        chain,
        scope=produced.scope,
        now_iso="2026-05-10T12:30:00Z",
    )
    print("[ok] chain authorizes shard scope at issuance time")
    produced.verify(worker_pk)
    print("[ok] produced shard signature valid against leaf subject pubkey")


if __name__ == "__main__":
    main()
