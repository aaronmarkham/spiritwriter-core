"""Tests for spiritwriter.fabric.entitlement."""

import pytest
from datetime import datetime, timezone, timedelta

from spiritwriter.fabric.crypto import generate_job_key
from spiritwriter.fabric.entitlement import (
    Capability, EntitlementToken,
    create_entitlement, validate_capability, validate_scope,
    validate_budget, is_expired, get_shard_key,
    serialize_token, deserialize_token,
    Caveat, CaveatType, KNOWN_CAVEAT_TYPES, UnknownCaveatError,
    validate_caveat, validate_caveats,
    verify_chain as verify_cap_chain,
    authorize_chain, issue_delegated,
)
from spiritwriter.fabric.shard import generate_signing_keypair, pubkey_thumbprint


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


# === Caveat tests =======================================================


class TestCaveat:
    def test_known_types_registered(self):
        assert CaveatType.EXPIRES_AT in KNOWN_CAVEAT_TYPES
        assert CaveatType.SCOPE_LIMIT in KNOWN_CAVEAT_TYPES
        assert CaveatType.MAX_DELEGATION_DEPTH in KNOWN_CAVEAT_TYPES

    def test_caveat_roundtrip(self):
        c = Caveat(CaveatType.EXPIRES_AT, "2099-01-01T00:00:00Z")
        d = c.to_dict()
        restored = Caveat.from_dict(d)
        assert restored == c

    def test_caveat_is_frozen(self):
        c = Caveat(CaveatType.MAX_DELEGATION_DEPTH, 3)
        with pytest.raises(Exception):  # FrozenInstanceError
            c.value = 5  # type: ignore[misc]


class TestValidateCaveat:
    def test_unknown_type_fails_closed(self):
        c = Caveat("nonexistent_caveat_type", "anything")
        with pytest.raises(UnknownCaveatError, match="nonexistent_caveat_type"):
            validate_caveat(c)

    def test_expires_at_future_passes(self):
        c = Caveat(CaveatType.EXPIRES_AT, "2099-01-01T00:00:00Z")
        assert validate_caveat(c) is True

    def test_expires_at_past_fails(self):
        c = Caveat(CaveatType.EXPIRES_AT, "2000-01-01T00:00:00Z")
        assert validate_caveat(c) is False

    def test_expires_at_explicit_now(self):
        c = Caveat(CaveatType.EXPIRES_AT, "2026-06-01T00:00:00Z")
        assert validate_caveat(c, now_iso="2026-05-01T00:00:00Z") is True
        assert validate_caveat(c, now_iso="2026-07-01T00:00:00Z") is False

    def test_scope_limit_match(self):
        c = Caveat(CaveatType.SCOPE_LIMIT, "sw:article:run-abc:*")
        assert validate_caveat(c, scope="sw:article:run-abc:builder-2") is True

    def test_scope_limit_mismatch(self):
        c = Caveat(CaveatType.SCOPE_LIMIT, "sw:article:run-abc:*")
        assert validate_caveat(c, scope="sw:article:run-xyz:builder-2") is False

    def test_scope_limit_no_scope_passes(self):
        """When no scope is being checked, scope_limit is vacuously satisfied."""
        c = Caveat(CaveatType.SCOPE_LIMIT, "sw:article:*")
        assert validate_caveat(c, scope=None) is True

    def test_max_delegation_depth_within_budget(self):
        c = Caveat(CaveatType.MAX_DELEGATION_DEPTH, 2)
        assert validate_caveat(c, delegation_depth=0) is True
        assert validate_caveat(c, delegation_depth=2) is True

    def test_max_delegation_depth_over_budget(self):
        c = Caveat(CaveatType.MAX_DELEGATION_DEPTH, 1)
        assert validate_caveat(c, delegation_depth=2) is False

    def test_validate_caveats_all_must_pass(self):
        caveats = [
            Caveat(CaveatType.EXPIRES_AT, "2099-01-01T00:00:00Z"),
            Caveat(CaveatType.SCOPE_LIMIT, "sw:article:*"),
        ]
        assert validate_caveats(caveats, scope="sw:article:abc") is True
        # Same caveats, different scope
        assert validate_caveats(caveats, scope="sw:other:abc") is False

    def test_validate_caveats_empty_passes(self):
        assert validate_caveats([]) is True

    def test_validate_caveats_unknown_propagates(self):
        caveats = [
            Caveat(CaveatType.EXPIRES_AT, "2099-01-01T00:00:00Z"),
            Caveat("unknown_thing", 0),
        ]
        with pytest.raises(UnknownCaveatError):
            validate_caveats(caveats)


# === Token signing ======================================================


class TestEntitlementSigning:
    def test_unsigned_token_back_compat(self):
        """Tokens without signing fields still create / serialize fine."""
        t = _make_token()
        assert t.signature is None
        assert t.subject_pubkey is None
        assert t.issuer_pubkey is None
        assert t.parent_cap_id is None
        assert t.caveats == []

    def test_sign_sets_signature_and_issuer(self):
        sk, pk = generate_signing_keypair()
        sub_sk, sub_pk = generate_signing_keypair()
        t = _make_token()
        t.subject_pubkey = sub_pk
        t.sign(sk)
        assert t.signature is not None
        assert len(t.signature) == 128
        assert t.issuer_pubkey == pk

    def test_sign_respects_preset_issuer(self):
        sk, pk = generate_signing_keypair()
        t = _make_token()
        t.issuer_pubkey = pk
        t.sign(sk)  # matches — no error
        assert t.signature is not None

    def test_sign_rejects_mismatched_issuer(self):
        sk1, _ = generate_signing_keypair()
        _, pk2 = generate_signing_keypair()
        t = _make_token()
        t.issuer_pubkey = pk2
        with pytest.raises(ValueError, match="issuer_pubkey mismatch"):
            t.sign(sk1)

    def test_verify_valid(self):
        sk, _ = generate_signing_keypair()
        t = _make_token()
        t.sign(sk)
        assert t.verify() is True

    def test_verify_unsigned_raises(self):
        t = _make_token()
        with pytest.raises(ValueError, match="no signature"):
            t.verify()

    def test_verify_no_issuer_pubkey_raises(self):
        t = _make_token()
        t.signature = "00" * 64  # fake hex signature
        with pytest.raises(ValueError, match="no issuer_pubkey"):
            t.verify()

    def test_verify_tampered_field_fails(self):
        from cryptography.exceptions import InvalidSignature
        sk, _ = generate_signing_keypair()
        t = _make_token()
        t.sign(sk)
        t.budget_usd = 999.0  # mutate signed field
        with pytest.raises(InvalidSignature):
            t.verify()

    def test_cap_id_deterministic(self):
        sk, _ = generate_signing_keypair()
        t = EntitlementToken(
            token_id="fixed-id",
            granted_to="x", granted_by="y", shard_keys={},
            scopes=["*"], capabilities=[], secrets=[], budget_usd=1.0,
            created_at="2026-05-10T12:00:00Z",
        )
        t.sign(sk)
        assert t.cap_id == t.cap_id  # stable across calls

    def test_cap_id_changes_with_signed_fields(self):
        sk, _ = generate_signing_keypair()
        common = dict(
            granted_to="x", granted_by="y", shard_keys={},
            scopes=["*"], capabilities=[], secrets=[], budget_usd=1.0,
            created_at="2026-05-10T12:00:00Z",
        )
        t1 = EntitlementToken(token_id="id-1", **common)
        t2 = EntitlementToken(token_id="id-2", **common)
        t1.sign(sk)
        t2.sign(sk)
        assert t1.cap_id != t2.cap_id

    def test_cap_id_independent_of_shard_keys(self):
        """shard_keys is bookkeeping, not in signing payload — must not change cap_id."""
        from spiritwriter.fabric.crypto import serialize_key
        sk, _ = generate_signing_keypair()
        common = dict(
            token_id="fixed-id",
            granted_to="x", granted_by="y",
            scopes=["*"], capabilities=[], secrets=[], budget_usd=1.0,
            created_at="2026-05-10T12:00:00Z",
        )
        t1 = EntitlementToken(
            shard_keys={"a": serialize_key(generate_job_key())},
            **common,
        )
        t2 = EntitlementToken(
            shard_keys={
                "b": serialize_key(generate_job_key()),
                "c": serialize_key(generate_job_key()),
            },
            **common,
        )
        t1.sign(sk)
        t2.sign(sk)
        assert t1.cap_id == t2.cap_id

    def test_signed_token_serialization_roundtrip(self):
        sk, _ = generate_signing_keypair()
        _, sub_pk = generate_signing_keypair()
        t = _make_token()
        t.subject_pubkey = sub_pk
        t.parent_cap_id = "parent-cap-id-abc"
        t.caveats = [
            Caveat(CaveatType.EXPIRES_AT, "2099-01-01T00:00:00Z"),
            Caveat(CaveatType.MAX_DELEGATION_DEPTH, 2),
        ]
        t.sign(sk)
        original_cap_id = t.cap_id

        restored = deserialize_token(serialize_token(t))
        assert restored.subject_pubkey == sub_pk
        assert restored.issuer_pubkey == t.issuer_pubkey
        assert restored.parent_cap_id == "parent-cap-id-abc"
        assert restored.signature == t.signature
        assert len(restored.caveats) == 2
        assert restored.caveats[0].type == CaveatType.EXPIRES_AT
        assert restored.cap_id == original_cap_id
        assert restored.verify() is True

    def test_old_token_format_deserializes(self):
        """Tokens serialized before delegation fields existed still deserialize."""
        import json
        old_format = json.dumps({
            "token_id": "old-token",
            "granted_to": "x",
            "granted_by": "y",
            "shard_keys": {},
            "scopes": ["*"],
            "capabilities": [Capability.SHARD_READ],
            "secrets": [],
            "budget_usd": 1.0,
            "created_at": "2024-01-01T00:00:00Z",
            "expires_at": None,
            "trace_parent": None,
            "constraints": {},
        })
        t = deserialize_token(old_format)
        assert t.signature is None
        assert t.caveats == []
        assert t.parent_cap_id is None


# === Delegation chains ==================================================


def _root_token(sk, pk, *, granted_to="root-agent", caveats=None):
    """Build a self-issued root cap (issuer == subject == root key)."""
    t = create_entitlement(
        granted_to=granted_to,
        granted_by="self",
        shard_keys={},
        scopes=["*"],
        capabilities=[Capability.SHARD_READ, Capability.SHARD_WRITE],
        secrets=[],
        budget_usd=100.0,
    )
    t.subject_pubkey = pk
    t.caveats = list(caveats or [])
    t.sign(sk)
    return t


class TestVerifyChain:
    def test_root_only(self):
        sk, pk = generate_signing_keypair()
        root = _root_token(sk, pk)
        assert verify_cap_chain([root], root_pubkeys=[pk]) is True

    def test_root_unknown_pubkey_rejected(self):
        sk, pk = generate_signing_keypair()
        _, other_pk = generate_signing_keypair()
        root = _root_token(sk, pk)
        with pytest.raises(ValueError, match="not in trust roots"):
            verify_cap_chain([root], root_pubkeys=[other_pk])

    def test_root_with_parent_cap_id_rejected(self):
        sk, pk = generate_signing_keypair()
        root = _root_token(sk, pk)
        root.parent_cap_id = "fake-parent"
        with pytest.raises(ValueError, match="parent_cap_id=None"):
            verify_cap_chain([root], root_pubkeys=[pk])

    def test_two_link_chain(self):
        root_sk, root_pk = generate_signing_keypair()
        child_sk, child_pk = generate_signing_keypair()
        root = _root_token(root_sk, root_pk)
        child = issue_delegated(
            root, root_sk,
            subject_pubkey=child_pk,
            granted_to="child",
        )
        assert verify_cap_chain([root, child], root_pubkeys=[root_pk]) is True

    def test_three_link_chain(self):
        root_sk, root_pk = generate_signing_keypair()
        mid_sk, mid_pk = generate_signing_keypair()
        leaf_sk, leaf_pk = generate_signing_keypair()
        root = _root_token(
            root_sk, root_pk,
            caveats=[Caveat(CaveatType.MAX_DELEGATION_DEPTH, 5)],
        )
        mid = issue_delegated(
            root, root_sk,
            subject_pubkey=mid_pk,
            granted_to="orchestrator",
        )
        leaf = issue_delegated(
            mid, mid_sk,
            subject_pubkey=leaf_pk,
            granted_to="builder",
        )
        assert verify_cap_chain([root, mid, leaf], root_pubkeys=[root_pk]) is True

    def test_broken_issuer_subject_linkage(self):
        root_sk, root_pk = generate_signing_keypair()
        child_sk, child_pk = generate_signing_keypair()
        impostor_sk, impostor_pk = generate_signing_keypair()
        root = _root_token(root_sk, root_pk)
        child = issue_delegated(
            root, root_sk,
            subject_pubkey=child_pk,
            granted_to="child",
        )
        # Tamper: claim impostor signed it instead
        child.issuer_pubkey = impostor_pk
        with pytest.raises(ValueError, match="Chain broken"):
            verify_cap_chain([root, child], root_pubkeys=[root_pk])

    def test_broken_parent_cap_id(self):
        root_sk, root_pk = generate_signing_keypair()
        child_sk, child_pk = generate_signing_keypair()
        root = _root_token(root_sk, root_pk)
        child = issue_delegated(
            root, root_sk,
            subject_pubkey=child_pk,
            granted_to="child",
        )
        # Tamper: point at a different parent
        child.parent_cap_id = "x" * 64
        # Resign so the signature still verifies — the chain check should still reject
        child.signature = None
        child.sign(root_sk)
        with pytest.raises(ValueError, match="parent_cap_id"):
            verify_cap_chain([root, child], root_pubkeys=[root_pk])

    def test_empty_chain_rejected(self):
        with pytest.raises(ValueError, match="Empty chain"):
            verify_cap_chain([], root_pubkeys=[])


class TestAuthorizeChain:
    def test_passes_when_all_caveats_satisfied(self):
        root_sk, root_pk = generate_signing_keypair()
        child_sk, child_pk = generate_signing_keypair()
        root = _root_token(
            root_sk, root_pk,
            caveats=[
                Caveat(CaveatType.MAX_DELEGATION_DEPTH, 3),
                Caveat(CaveatType.EXPIRES_AT, "2099-01-01T00:00:00Z"),
            ],
        )
        child = issue_delegated(
            root, root_sk,
            subject_pubkey=child_pk,
            granted_to="child",
            caveats=[Caveat(CaveatType.SCOPE_LIMIT, "sw:article:*")],
        )
        chain = [root, child]
        assert authorize_chain(chain, scope="sw:article:abc") is True

    def test_fails_on_scope_limit_violation(self):
        root_sk, root_pk = generate_signing_keypair()
        child_sk, child_pk = generate_signing_keypair()
        root = _root_token(root_sk, root_pk)
        child = issue_delegated(
            root, root_sk,
            subject_pubkey=child_pk,
            granted_to="child",
            caveats=[Caveat(CaveatType.SCOPE_LIMIT, "sw:article:*")],
        )
        assert authorize_chain([root, child], scope="sw:secrets:abc") is False

    def test_fails_when_any_link_expired(self):
        """Even if leaf is fresh, an expired ancestor invalidates the chain."""
        root_sk, root_pk = generate_signing_keypair()
        child_sk, child_pk = generate_signing_keypair()
        root = _root_token(
            root_sk, root_pk,
            caveats=[Caveat(CaveatType.EXPIRES_AT, "2020-01-01T00:00:00Z")],
        )
        child = issue_delegated(
            root, root_sk,
            subject_pubkey=child_pk,
            granted_to="child",
            caveats=[Caveat(CaveatType.EXPIRES_AT, "2099-01-01T00:00:00Z")],
        )
        assert authorize_chain([root, child]) is False

    def test_max_delegation_depth_intersection(self):
        """A 3-link chain requires each ancestor's depth budget to cover its descendants."""
        root_sk, root_pk = generate_signing_keypair()
        mid_sk, mid_pk = generate_signing_keypair()
        leaf_sk, leaf_pk = generate_signing_keypair()
        root = _root_token(
            root_sk, root_pk,
            caveats=[Caveat(CaveatType.MAX_DELEGATION_DEPTH, 5)],
        )
        mid = issue_delegated(
            root, root_sk,
            subject_pubkey=mid_pk,
            granted_to="orchestrator",
            # mid auto-gets max_delegation_depth=4 (root's 5 - 1)
        )
        leaf = issue_delegated(
            mid, mid_sk,
            subject_pubkey=leaf_pk,
            granted_to="builder",
            # leaf auto-gets max_delegation_depth=3 (mid's 4 - 1)
        )
        assert authorize_chain([root, mid, leaf]) is True

    def test_unknown_caveat_propagates(self):
        sk, pk = generate_signing_keypair()
        root = _root_token(sk, pk)
        root.caveats = [Caveat("unknown_thing", 0)]
        # Don't re-sign; authorize_chain doesn't require signatures
        with pytest.raises(UnknownCaveatError):
            authorize_chain([root])


class TestIssueDelegated:
    def test_basic_issuance(self):
        root_sk, root_pk = generate_signing_keypair()
        child_sk, child_pk = generate_signing_keypair()
        root = _root_token(root_sk, root_pk)
        child = issue_delegated(
            root, root_sk,
            subject_pubkey=child_pk,
            granted_to="child-agent",
            scopes=["sw:article:*"],
            capabilities=[Capability.SHARD_READ],
        )
        assert child.subject_pubkey == child_pk
        assert child.issuer_pubkey == root_pk
        assert child.parent_cap_id == root.cap_id
        assert child.granted_to == "child-agent"
        assert child.granted_by == root.granted_to
        assert child.signature is not None
        assert child.verify() is True

    def test_inherits_parent_scopes_when_unset(self):
        root_sk, root_pk = generate_signing_keypair()
        _, child_pk = generate_signing_keypair()
        root = _root_token(root_sk, root_pk)
        # root has scopes=["*"] from helper
        child = issue_delegated(
            root, root_sk,
            subject_pubkey=child_pk,
            granted_to="child",
        )
        assert child.scopes == root.scopes
        assert child.capabilities == root.capabilities

    def test_decrement_max_delegation_depth(self):
        root_sk, root_pk = generate_signing_keypair()
        _, child_pk = generate_signing_keypair()
        root = _root_token(
            root_sk, root_pk,
            caveats=[Caveat(CaveatType.MAX_DELEGATION_DEPTH, 3)],
        )
        child = issue_delegated(
            root, root_sk,
            subject_pubkey=child_pk,
            granted_to="child",
        )
        depths = [c.value for c in child.caveats if c.type == CaveatType.MAX_DELEGATION_DEPTH]
        assert depths == [2]

    def test_explicit_child_depth_must_not_exceed_parent_minus_one(self):
        root_sk, root_pk = generate_signing_keypair()
        _, child_pk = generate_signing_keypair()
        root = _root_token(
            root_sk, root_pk,
            caveats=[Caveat(CaveatType.MAX_DELEGATION_DEPTH, 2)],
        )
        with pytest.raises(ValueError, match="exceeds parent's allowance"):
            issue_delegated(
                root, root_sk,
                subject_pubkey=child_pk,
                granted_to="child",
                caveats=[Caveat(CaveatType.MAX_DELEGATION_DEPTH, 5)],
            )

    def test_explicit_child_depth_within_limit_kept(self):
        root_sk, root_pk = generate_signing_keypair()
        _, child_pk = generate_signing_keypair()
        root = _root_token(
            root_sk, root_pk,
            caveats=[Caveat(CaveatType.MAX_DELEGATION_DEPTH, 3)],
        )
        child = issue_delegated(
            root, root_sk,
            subject_pubkey=child_pk,
            granted_to="child",
            caveats=[Caveat(CaveatType.MAX_DELEGATION_DEPTH, 0)],
        )
        depths = [c.value for c in child.caveats if c.type == CaveatType.MAX_DELEGATION_DEPTH]
        assert depths == [0]

    def test_parent_at_depth_zero_cannot_delegate(self):
        root_sk, root_pk = generate_signing_keypair()
        mid_sk, mid_pk = generate_signing_keypair()
        _, leaf_pk = generate_signing_keypair()
        root = _root_token(
            root_sk, root_pk,
            caveats=[Caveat(CaveatType.MAX_DELEGATION_DEPTH, 1)],
        )
        mid = issue_delegated(
            root, root_sk,
            subject_pubkey=mid_pk,
            granted_to="mid",
        )
        # mid now has max_delegation_depth=0 (auto-derived)
        with pytest.raises(ValueError, match="does not permit further delegation"):
            issue_delegated(
                mid, mid_sk,
                subject_pubkey=leaf_pk,
                granted_to="leaf",
            )

    def test_chain_built_via_issue_delegated_verifies(self):
        """End-to-end: build a 3-link chain through issue_delegated, then verify."""
        root_sk, root_pk = generate_signing_keypair()
        mid_sk, mid_pk = generate_signing_keypair()
        leaf_sk, leaf_pk = generate_signing_keypair()
        root = _root_token(
            root_sk, root_pk,
            caveats=[Caveat(CaveatType.MAX_DELEGATION_DEPTH, 5)],
        )
        mid = issue_delegated(
            root, root_sk,
            subject_pubkey=mid_pk,
            granted_to="orchestrator",
        )
        leaf = issue_delegated(
            mid, mid_sk,
            subject_pubkey=leaf_pk,
            granted_to="builder",
            caveats=[Caveat(CaveatType.SCOPE_LIMIT, "sw:article:run-abc:*")],
        )
        chain = [root, mid, leaf]
        verify_cap_chain(chain, root_pubkeys=[root_pk])
        assert authorize_chain(chain, scope="sw:article:run-abc:builder-2") is True
        assert authorize_chain(chain, scope="sw:secrets:anything") is False
