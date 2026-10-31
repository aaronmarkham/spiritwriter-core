"""Encryption primitives for shard content protection.

Uses AES-256-GCM for authenticated encryption. Shards are encrypted
before storage (local or DHT). Only agents with the entitlement key
can decrypt.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from spiritwriter.fabric.shard import MemoryShard, _canonical_json, _sha256


class DecryptionError(Exception):
    pass


@dataclass
class EncryptedShard:
    shard_id: str
    scope: str
    encrypted_payload: bytes
    nonce: bytes
    atom_count: int
    created_at: str
    origin_agent: str
    content_hash: str

    def to_dict(self) -> dict:
        return {
            "shard_id": self.shard_id,
            "scope": self.scope,
            "encrypted_payload": base64.b64encode(self.encrypted_payload).decode(),
            "nonce": base64.b64encode(self.nonce).decode(),
            "atom_count": self.atom_count,
            "created_at": self.created_at,
            "origin_agent": self.origin_agent,
            "content_hash": self.content_hash,
        }

    @classmethod
    def from_dict(cls, d: dict) -> EncryptedShard:
        return cls(
            shard_id=d["shard_id"],
            scope=d["scope"],
            encrypted_payload=base64.b64decode(d["encrypted_payload"]),
            nonce=base64.b64decode(d["nonce"]),
            atom_count=d["atom_count"],
            created_at=d["created_at"],
            origin_agent=d["origin_agent"],
            content_hash=d["content_hash"],
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> EncryptedShard:
        return cls.from_dict(json.loads(raw))


def generate_job_key() -> bytes:
    return os.urandom(32)


def encrypt_shard(shard: MemoryShard, key: bytes) -> EncryptedShard:
    plaintext = shard.to_json().encode("utf-8")
    content_hash = _sha256(plaintext)
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    return EncryptedShard(
        shard_id=shard.shard_id,
        scope=shard.scope,
        encrypted_payload=ciphertext,
        nonce=nonce,
        atom_count=len(shard.atoms),
        created_at=shard.created_at,
        origin_agent=shard.origin,
        content_hash=content_hash,
    )


def decrypt_shard(encrypted: EncryptedShard, key: bytes) -> MemoryShard:
    aesgcm = AESGCM(key)
    try:
        plaintext = aesgcm.decrypt(encrypted.nonce, encrypted.encrypted_payload, None)
    except Exception as e:
        raise DecryptionError(f"Decryption failed: {e}") from e
    actual_hash = _sha256(plaintext)
    if actual_hash != encrypted.content_hash:
        raise DecryptionError("Content hash mismatch — data may be corrupted")
    return MemoryShard.from_json(plaintext.decode("utf-8"))


def serialize_key(key: bytes) -> str:
    return base64.urlsafe_b64encode(key).decode()


def deserialize_key(s: str) -> bytes:
    key = base64.urlsafe_b64decode(s)
    if len(key) != 32:
        raise ValueError(f"Expected 32-byte key, got {len(key)}")
    return key
