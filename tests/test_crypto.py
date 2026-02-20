"""Tests for spiritwriter.trace.crypto."""

import pytest
from spiritwriter.trace.shard import MemoryShard, ShardAtom, AtomKind
from spiritwriter.trace.crypto import (
    generate_job_key, encrypt_shard, decrypt_shard,
    serialize_key, deserialize_key, DecryptionError,
)


def _make_shard():
    return MemoryShard(
        atoms=[
            ShardAtom(text="test fact", kind=AtomKind.FACT, entity="foo", key="bar", value="baz"),
            ShardAtom(text="another atom"),
        ],
        scope="project:test",
        origin="test-agent",
    )


def test_generate_key_length():
    assert len(generate_job_key()) == 32


def test_generate_key_unique():
    assert generate_job_key() != generate_job_key()


def test_encrypt_decrypt_roundtrip():
    shard = _make_shard()
    key = generate_job_key()
    enc = encrypt_shard(shard, key)
    dec = decrypt_shard(enc, key)
    assert len(dec.atoms) == len(shard.atoms)
    assert dec.atoms[0].text == "test fact"
    assert dec.atoms[0].value == "baz"
    assert dec.shard_id == shard.shard_id


def test_wrong_key_fails():
    shard = _make_shard()
    enc = encrypt_shard(shard, generate_job_key())
    with pytest.raises(DecryptionError):
        decrypt_shard(enc, generate_job_key())


def test_tampered_payload_fails():
    shard = _make_shard()
    key = generate_job_key()
    enc = encrypt_shard(shard, key)
    enc.encrypted_payload = enc.encrypted_payload[:-1] + bytes([enc.encrypted_payload[-1] ^ 0xFF])
    with pytest.raises(DecryptionError):
        decrypt_shard(enc, key)


def test_content_hash_verified():
    shard = _make_shard()
    key = generate_job_key()
    enc = encrypt_shard(shard, key)
    enc.content_hash = "0" * 64
    with pytest.raises(DecryptionError, match="hash mismatch"):
        decrypt_shard(enc, key)


def test_scope_preserved_plaintext():
    shard = _make_shard()
    key = generate_job_key()
    enc = encrypt_shard(shard, key)
    assert enc.scope == "project:test"


def test_key_serialization_roundtrip():
    key = generate_job_key()
    assert deserialize_key(serialize_key(key)) == key


def test_invalid_key_length():
    with pytest.raises(ValueError):
        deserialize_key(serialize_key(b"short"))
