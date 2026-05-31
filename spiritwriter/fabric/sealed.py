"""Sealed-box encryption for zero-knowledge shard ownership.

Asymmetric encryption using NaCl sealed boxes (X25519 + XSalsa20-Poly1305).
The owner holds the private key; the service/operator holds only the public key
and can encrypt data *to* the owner but never decrypt it.

This is the "operator can't see content" layer. It complements — not replaces —
the symmetric AES-GCM layer in crypto.py (which protects shards at rest) and
the entitlement system (which governs access between cooperating agents).

Use cases:
- Zero-knowledge monitoring (Frio: families searching for detained people)
- Multi-tenant hosted shards where operator shouldn't access content
- Legal/journalistic source protection
- Any scenario where the shard creator ≠ the shard reader

Requires: PyNaCl (optional dependency)
    pip install PyNaCl

Key concepts:
- OwnerKeypair: generated client-side or in ephemeral intake process
- seal_for_owner(): encrypt plaintext so only the owner's private key can decrypt
- unseal_as_owner(): owner decrypts with their private key
- SealedShard: encrypted shard with owner_pubkey for result delivery

Note on shard_id and low-entropy plaintext:
    SealedShard.shard_id is the content address of the PLAINTEXT shard,
    which keeps content-addressing consistent across the system. For most
    uses this is fine, but if your threat model includes "operator must
    not be able to confirm whether plaintext X was sealed" and plaintext
    space is small (e.g., names), the operator can dictionary-attack
    shard_id by reconstructing candidate atoms+scope+origin and hashing.
    The fix is at the *caller* layer: include a high-entropy nonce atom
    in the shard before sealing. See docs/encryption.md, section
    "Per-Seal Uniqueness for Low-Entropy Plaintext".
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

try:
    from nacl.public import PrivateKey, PublicKey, SealedBox
    HAS_NACL = True
except ImportError:
    HAS_NACL = False

from spiritwriter.fabric.shard import MemoryShard, _sha256


def _require_nacl() -> None:
    """Raise a clear error if PyNaCl isn't installed."""
    if not HAS_NACL:
        raise ImportError(
            "Sealed-box encryption requires PyNaCl. "
            "Install it with: pip install 'spiritwriter[sealed]'"
        )


# === Key Management ===

@dataclass
class OwnerKeypair:
    """An owner's keypair for sealed-box encryption.

    The private key is the capability key — whoever holds it can decrypt.
    The public key is stored with the shard so the service can encrypt
    results *to* the owner without being able to read them.
    """
    private_key: bytes  # 32-byte Curve25519 private key
    public_key: bytes   # 32-byte Curve25519 public key

    @property
    def private_key_b64(self) -> str:
        return base64.urlsafe_b64encode(self.private_key).decode()

    @property
    def public_key_b64(self) -> str:
        return base64.urlsafe_b64encode(self.public_key).decode()

    @classmethod
    def from_private_b64(cls, private_b64: str) -> OwnerKeypair:
        """Reconstruct keypair from the private key (capability key)."""
        _require_nacl()
        private_bytes = base64.urlsafe_b64decode(private_b64)
        sk = PrivateKey(private_bytes)
        return cls(
            private_key=bytes(sk),
            public_key=bytes(sk.public_key),
        )

    @classmethod
    def from_public_b64(cls, public_b64: str) -> OwnerKeypair:
        """Create a partial keypair with only the public key (for sealing).

        This is what the service/operator stores — they can seal but not unseal.
        """
        public_bytes = base64.urlsafe_b64decode(public_b64)
        return cls(
            private_key=b"",  # not available
            public_key=public_bytes,
        )


def generate_owner_keypair() -> OwnerKeypair:
    """Generate a new owner keypair.

    The private key (capability key) should be returned to the owner
    and never stored by the service. The public key is stored with
    the shard for encrypting results.
    """
    _require_nacl()
    sk = PrivateKey.generate()
    return OwnerKeypair(
        private_key=bytes(sk),
        public_key=bytes(sk.public_key),
    )


# === Seal / Unseal ===

def seal_for_owner(plaintext: bytes, owner_pubkey: bytes) -> bytes:
    """Encrypt data so only the owner's private key can decrypt it.

    Uses NaCl sealed boxes: ephemeral sender key + owner's public key.
    The service calls this to encrypt results to the owner.
    Even the service cannot decrypt the output.
    """
    _require_nacl()
    pk = PublicKey(owner_pubkey)
    box = SealedBox(pk)
    return box.encrypt(plaintext)


def unseal_as_owner(ciphertext: bytes, owner_private_key: bytes) -> bytes:
    """Decrypt data sealed for this owner.

    The owner calls this with their private key (capability key)
    to read results the service encrypted for them.
    """
    _require_nacl()
    sk = PrivateKey(owner_private_key)
    box = SealedBox(sk)
    return box.decrypt(ciphertext)


# === SealedShard ===

@dataclass
class SealedShard:
    """A shard encrypted with sealed-box (asymmetric) encryption.

    The operator can see: shard_id, scope, owner_pubkey, atom_count,
    created_at, origin_agent, content_hash.

    The operator CANNOT see: the shard content (atoms, meta, etc).
    Only the owner (holder of the matching private key) can decrypt.
    """
    shard_id: str
    scope: str
    sealed_payload: bytes     # NaCl sealed box — only owner can open
    owner_pubkey: bytes       # 32-byte Curve25519 public key
    atom_count: int
    created_at: str
    origin_agent: str
    content_hash: str         # SHA-256 of plaintext for integrity check

    def to_dict(self) -> dict[str, Any]:
        return {
            "shard_id": self.shard_id,
            "scope": self.scope,
            "sealed_payload": base64.b64encode(self.sealed_payload).decode(),
            "owner_pubkey": base64.urlsafe_b64encode(self.owner_pubkey).decode(),
            "atom_count": self.atom_count,
            "created_at": self.created_at,
            "origin_agent": self.origin_agent,
            "content_hash": self.content_hash,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SealedShard:
        return cls(
            shard_id=d["shard_id"],
            scope=d["scope"],
            sealed_payload=base64.b64decode(d["sealed_payload"]),
            owner_pubkey=base64.urlsafe_b64decode(d["owner_pubkey"]),
            atom_count=d["atom_count"],
            created_at=d["created_at"],
            origin_agent=d["origin_agent"],
            content_hash=d["content_hash"],
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> SealedShard:
        return cls.from_dict(json.loads(raw))


class UnsealError(Exception):
    """Raised when unsealing fails (wrong key, tampered data, etc)."""
    pass


# === Ed25519 Signing ===

def generate_signing_keypair() -> tuple[bytes, bytes]:
    """Generate Ed25519 signing keypair.

    Returns (signing_key_bytes, verify_key_bytes) — both 32 bytes.
    The signing key should be stored with restricted permissions (chmod 600).
    The verify key is shareable so requestors can verify result integrity.
    """
    _require_nacl()
    from nacl.signing import SigningKey
    sk = SigningKey.generate()
    return bytes(sk), bytes(sk.verify_key)


def sign_data(data: bytes, signing_key: bytes) -> bytes:
    """Sign data with Ed25519. Returns 64-byte signature.

    Pairs with verify_signature() for integrity verification of sealed
    payloads, results, or any data that must be attributable to a
    specific keypair holder.
    """
    _require_nacl()
    from nacl.signing import SigningKey
    sk = SigningKey(signing_key)
    return sk.sign(data).signature


def verify_signature(data: bytes, signature: bytes, verify_key: bytes) -> bool:
    """Verify Ed25519 signature. Returns True or raises BadSignatureError.

    Use with the signer's verify key (public) to confirm data was
    produced by the holder of the corresponding signing key.
    """
    _require_nacl()
    from nacl.signing import VerifyKey
    vk = VerifyKey(verify_key)
    vk.verify(data, signature)  # raises nacl.exceptions.BadSignatureError on failure
    return True


def seal_shard(shard: MemoryShard, owner_pubkey: bytes) -> SealedShard:
    """Seal a shard so only the owner can read it.

    The service calls this when storing a shard on behalf of an owner.
    The plaintext exists only in memory during this call.
    """
    _require_nacl()
    plaintext = shard.to_json().encode("utf-8")
    content_hash = _sha256(plaintext)
    sealed = seal_for_owner(plaintext, owner_pubkey)
    return SealedShard(
        shard_id=shard.shard_id,
        scope=shard.scope,
        sealed_payload=sealed,
        owner_pubkey=owner_pubkey,
        atom_count=len(shard.atoms),
        created_at=shard.created_at,
        origin_agent=shard.origin,
        content_hash=content_hash,
    )


def unseal_shard(sealed: SealedShard, owner_private_key: bytes) -> MemoryShard:
    """Unseal a shard using the owner's private key (capability key).

    The owner calls this to read their shard content.
    """
    _require_nacl()
    try:
        plaintext = unseal_as_owner(sealed.sealed_payload, owner_private_key)
    except Exception as e:
        raise UnsealError(f"Unseal failed: {e}") from e
    actual_hash = _sha256(plaintext)
    if actual_hash != sealed.content_hash:
        raise UnsealError("Content hash mismatch — data may be corrupted or tampered")
    return MemoryShard.from_json(plaintext.decode("utf-8"))
