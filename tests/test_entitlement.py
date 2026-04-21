"""Tests for spiritwriter.fabric.entitlement."""

import pytest
from datetime import datetime, timezone, timedelta

from spiritwriter.fabric.crypto import generate_job_key
from spiritwriter.fabric.entitlement import (
    Capability, EntitlementToken,
    create_entitlement, validate_capability, validate_scope,
    validate_budget, is_expired, get_shard_key,
    serialize_token, deserialize_token,
)


def _make_token(**kw):
    defaults = dict(
        granted_to="sub-agent-1",
        granted_by="parent-agent",
        shard_keys={"shard-abc": generate_job_key()},
        scopes=["project:csp", "project:*"],
        capabilities=[Capability.SHARD_READ, Capability.WEB_SEARCH],
        secrets=["OPENAI_API_KEY"],
        budget_usd=5.0,
    )
    defaults.update(kw)
    return create_entitlement(**defaults)


def test_create_entitlement():
    t = _make_token()
    assert t.token_id
    assert t.granted_to == "sub-agent-1"
    assert t.created_at
    assert len(t.shard_keys) == 1


def test_validate_capability_pass():
    assert validate_capability(_make_token(), Capability.SHARD_READ)


def test_validate_capability_fail():
    assert not validate_capability(_make_token(), Capability.EXEC_RUN)


def test_validate_scope_exact():
    assert validate_scope(_make_token(), "project:csp")


def test_validate_scope_wildcard():
    assert validate_scope(_make_token(), "project:spiritwriter")


def test_validate_scope_fail():
    assert not validate_scope(_make_token(), "admin:secrets")


def test_validate_budget_pass():
    assert validate_budget(_make_token(), 3.0)


def test_validate_budget_fail():
    assert not validate_budget(_make_token(), 6.0)


def test_is_expired_not_expired():
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    assert not is_expired(_make_token(expires_at=future))


def test_is_expired_expired():
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert is_expired(_make_token(expires_at=past))


def test_is_expired_no_expiry():
    assert not is_expired(_make_token())


def test_get_shard_key_success():
    key = generate_job_key()
    t = _make_token(shard_keys={"shard-x": key})
    assert get_shard_key(t, "shard-x") == key


def test_get_shard_key_not_entitled():
    with pytest.raises(KeyError):
        get_shard_key(_make_token(), "nonexistent")


def test_token_serialization_roundtrip():
    t = _make_token()
    s = serialize_token(t)
    t2 = deserialize_token(s)
    assert t2.token_id == t.token_id
    assert t2.shard_keys == t.shard_keys
    assert t2.capabilities == t.capabilities
    assert t2.budget_usd == t.budget_usd
