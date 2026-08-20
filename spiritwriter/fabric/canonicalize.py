"""Entity canonicalization engine — domain-agnostic entity resolution.

Resolves entities across records using Entity Sense Signatures (ESS),
tiered confidence scoring, and fuzzy matching. Applications supply
schemas and extractors; this module provides the resolution logic.

Storage: SQLite (WAL mode), one DB per registry.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterator

import logging

from spiritwriter.fabric.shard import _canonical_json, _sha256, _now_iso
from spiritwriter.fabric.emitter import TraceEmitter

logger = logging.getLogger(__name__)


# ── Normalization utilities ──────────────────────────────────────────
#
# Important: the registry does NOT auto-apply any of these to candidate
# fields beyond the baseline `.strip().lower()` that ESS digest
# computation does internally. Anything beyond case + whitespace (e.g.
# punctuation stripping, initial reduction, date format unification)
# must be done by the caller *before* calling resolve()/upsert(). The
# helpers below plus `apply_normalizers()` are the recommended way to
# build that per-domain pre-processing step. See
# "Normalize before you resolve" in docs/entity-resolution.md.


def normalize_name(s: str) -> str:
    """Normalize a name for comparison.

    Uppercase, strip, collapse whitespace, remove punctuation except hyphen.
    """
    s = s.upper().strip()
    # Remove punctuation except hyphen
    s = re.sub(r"[^\w\s-]", "", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s)
    return s


def first_initial(s: str | None) -> str:
    """Reduce a name to its first letter (uppercased).

    Useful when one source emits initials ("K.") and another emits
    full names ("Kazuhiko") for the same person — running both through
    this helper before resolution collapses them to the same ESS digest.

    >>> first_initial("K.")
    'K'
    >>> first_initial("Kazuhiko")
    'K'
    >>> first_initial("  jane  ")
    'J'
    >>> first_initial("")
    ''
    >>> first_initial(None)
    ''
    """
    if not s:
        return ""
    s = s.strip().lstrip(".").strip()
    return s[0].upper() if s else ""


def strip_punctuation(s: str | None) -> str:
    """Strip ASCII punctuation except hyphens (which are name-internal).

    >>> strip_punctuation("K.")
    'K'
    >>> strip_punctuation("Smith-Jones")
    'Smith-Jones'
    >>> strip_punctuation("O'Brien")
    'OBrien'
    >>> strip_punctuation(None)
    ''
    """
    if not s:
        return ""
    return re.sub(r"[^\w\s-]", "", s)


def apply_normalizers(
    candidate: dict[str, Any],
    normalizers: dict[str, Callable[[Any], Any]],
) -> dict[str, Any]:
    """Return a new candidate with per-field normalizers applied.

    Fields without a normalizer pass through unchanged. None values
    are passed to the normalizer; how to handle them is the
    normalizer's choice (the helpers in this module all accept None
    and return "").

    Does NOT mutate the input. The returned dict is suitable to pass
    directly to ``CanonicalRegistry.resolve()`` / ``upsert()``.

    >>> apply_normalizers(
    ...     {"first_name": "K.", "last_name": "Yamamoto", "id": 42},
    ...     {"first_name": first_initial, "last_name": str.upper},
    ... )
    {'first_name': 'K', 'last_name': 'YAMAMOTO', 'id': 42}
    """
    return {
        k: normalizers[k](v) if k in normalizers else v
        for k, v in candidate.items()
    }


def pipeline(*fns: Callable[[Any], Any]) -> Callable[[Any], Any]:
    """Compose normalizer functions left-to-right.

    Useful inside ``apply_normalizers()`` when a field needs multiple
    transformations: ``pipeline(str.strip, str.upper, strip_punctuation)``.

    >>> norm = pipeline(str.strip, str.upper, strip_punctuation)
    >>> norm("  o'brien.  ")
    'OBRIEN'
    """
    def composed(x: Any) -> Any:
        for fn in fns:
            x = fn(x)
        return x
    return composed


_DATE_FORMATS = [
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%m-%d-%Y",
    "%d/%m/%Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%Y%m%d",
    "%m/%d/%y",
]


def normalize_date(s: str) -> str | None:
    """Parse various date formats → ISO 8601 (YYYY-MM-DD) or None."""
    s = s.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def age_to_bucket(age: int, bucket_size: int = 2) -> str:
    """Convert age to bucket string for ESS when DOB unavailable.

    >>> age_to_bucket(42, 2)
    '42-43'
    >>> age_to_bucket(43, 2)
    '42-43'

    .. warning::
       The result is **not** text-sortable — ``'8-9'`` sorts *after*
       ``'42-43'``. That is harmless as an ESS field value, since field
       keys are unique and the value never decides ordering, but do not
       sort, range-scan, or canonicalize on these strings. Zero-pad
       first (see ``fabric.orbit.padded``) if you need order.
    """
    floor = (age // bucket_size) * bucket_size
    return f"{floor}-{floor + bucket_size - 1}"


def fuzzy_score(a: str, b: str) -> float:
    """String similarity score (0.0–1.0).

    Uses SequenceMatcher with normalization pre-applied.
    Considers prefix matching and length ratio filtering.
    """
    a_norm = normalize_name(a)
    b_norm = normalize_name(b)

    if not a_norm or not b_norm:
        return 0.0
    if a_norm == b_norm:
        return 1.0

    # Prefix check first — if one is a prefix of the other (3+ chars), boost
    shorter, longer = sorted([a_norm, b_norm], key=len)
    if longer.startswith(shorter) and len(shorter) >= 3:
        return max(SequenceMatcher(None, a_norm, b_norm).ratio(), 0.85)

    # Length ratio filter — very different lengths are unlikely matches
    ratio = len(a_norm) / len(b_norm) if len(b_norm) > 0 else 0.0
    if ratio < 0.5 or ratio > 2.0:
        return 0.0

    return SequenceMatcher(None, a_norm, b_norm).ratio()


# ── Core types ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class EntitySenseSig:
    """Content-addressed identity anchor.

    An ESS is a hash over a set of defining fields. Two records with
    the same ESS are considered the same entity (T1 match).
    Fields are normalized before hashing: lowered, stripped, sorted.
    """
    fields: tuple[tuple[str, str], ...]  # sorted (key, normalized_value) pairs
    digest: str                          # SHA-256 hex of canonical field repr

    @classmethod
    def compute(cls, **fields: str | None) -> EntitySenseSig:
        """Build ESS from keyword fields. None values are excluded."""
        pairs = sorted(
            (k, v.strip().lower())
            for k, v in fields.items()
            if v is not None
        )
        canon = _canonical_json(pairs)
        digest = _sha256(canon)
        return cls(fields=tuple(pairs), digest=digest)

    def overlap(self, other: EntitySenseSig) -> float:
        """Field overlap ratio (0.0–1.0). Used for T2/T3 tier scoring.

        Compares shared field keys. Returns ratio of matching values
        among shared keys. Fields present in one but not the other
        are ignored (partial records shouldn't penalize).
        """
        self_dict = dict(self.fields)
        other_dict = dict(other.fields)
        shared_keys = set(self_dict) & set(other_dict)
        if not shared_keys:
            return 0.0
        matches = sum(1 for k in shared_keys if self_dict[k] == other_dict[k])
        return matches / len(shared_keys)

    def __eq__(self, other: object) -> bool:
        """Exact ESS match = T1 resolution."""
        if not isinstance(other, EntitySenseSig):
            return NotImplemented
        return self.digest == other.digest

    def __hash__(self) -> int:
        return hash(self.digest)


class ResolutionTier(str, Enum):
    """Confidence tier for entity resolution."""
    T1_EXACT = "t1_exact"        # ESS match — same entity, no question
    T2_STRONG = "t2_strong"      # Most fields match, high confidence
    T3_FUZZY = "t3_fuzzy"        # Fuzzy match on key fields + demographic overlap
    T4_WEAK = "t4_weak"          # Partial signal — flag, don't auto-merge
    NO_MATCH = "no_match"

    @property
    def auto_merge(self) -> bool:
        return self in (ResolutionTier.T1_EXACT, ResolutionTier.T2_STRONG)

    @property
    def confidence(self) -> float:
        return {
            ResolutionTier.T1_EXACT: 0.95,
            ResolutionTier.T2_STRONG: 0.85,
            ResolutionTier.T3_FUZZY: 0.70,
            ResolutionTier.T4_WEAK: 0.50,
            ResolutionTier.NO_MATCH: 0.0,
        }[self]


@dataclass
class ResolutionResult:
    """Outcome of resolving a candidate against a known entity."""
    tier: ResolutionTier
    confidence: float              # may be adjusted beyond tier default
    canonical_id: str | None       # existing entity ID if matched, None if new
    field_matches: dict[str, bool] # per-field match breakdown for debugging
    notes: str = ""                # human-readable explanation


@dataclass
class CanonicalSchema:
    """Defines how a domain's entities are resolved.

    Applications create one of these to configure the engine.

    **Normalization is the caller's responsibility.** This schema is
    declarative only — the registry does NOT auto-apply normalizers to
    the candidate dicts you pass to ``resolve()`` / ``upsert()``. The
    baseline that ESS computation does internally (``.strip().lower()``)
    is the floor. Anything beyond case + whitespace — punctuation
    stripping, name-initial reduction, date format unification — must
    be done by the caller *before* invoking resolution. See the
    helpers ``first_initial``, ``strip_punctuation``,
    ``apply_normalizers``, and ``pipeline`` in this module, and the
    "Normalize before you resolve" section of
    ``docs/entity-resolution.md`` for the recommended pattern.
    """
    name: str                                # e.g. "inmate", "memory_entity"

    # Fields used in ESS computation (order matters for hash stability)
    ess_fields: list[str]                    # e.g. ["last_name", "first_name", "dob"]

    # Fields that support fuzzy matching (T2/T3 tiers)
    fuzzy_fields: dict[str, float]           # field_name → minimum similarity threshold

    # Fields used for contextual resolution (T4 / tiebreaking)
    context_fields: list[str] = field(default_factory=list)

    # Fields stored on the entity but NOT used in resolution or schema_hash
    metadata_fields: list[str] = field(default_factory=list)

    # Optional: age bucketing for when DOB is unavailable
    age_bucket_size: int = 2                 # ±N years counts as same bucket

    # Optional: temporal proximity window for context matching
    temporal_window_days: int = 7            # records within N days are "same window"

    def schema_hash(self) -> str:
        """Deterministic hash of schema config for mismatch detection."""
        return _sha256(_canonical_json({
            "name": self.name,
            "ess_fields": self.ess_fields,
            "fuzzy_fields": self.fuzzy_fields,
            "context_fields": self.context_fields,
        }))


# ── Fold policy & attribute folding ──────────────────────────────────
#
# Resolution decides *which* entity a record belongs to. Folding decides
# what the entity's stored fields look like afterwards. Without it a
# canonical record is frozen at first sight: a later, richer sighting
# contributes nothing and a later contradicting one raises no signal.


@dataclass(frozen=True)
class ResolutionPolicy:
    """Deterministic knobs of a fold (see :func:`fold_entity_fields`).

    Every option is a *total* order or a pure predicate, so two runs over
    the same records in the same order produce byte-identical stored
    fields. Nothing here consults wall-clock time, randomness, or dict
    iteration order.

    Attributes:
        precedence: Who wins a genuine scalar conflict. ``keep-first``
            keeps the value already on the entity — the outcome today's
            engine produces, since it never rewrites a stored field.
            ``richest`` lets the more populated record win, counting
            non-empty non-meta fields, ties broken in favour of the
            stored value. Both are total: ``richest`` degrades to
            ``keep-first`` whenever the counts tie, and the count itself
            is order-independent.
        conflicts: ``keep-first`` reports conflicts on the returned
            :class:`FoldResult` only; ``keep-all`` additionally stores the
            suppressed values under the ``__conflicts__`` key of the
            entity's field blob, as ``{field: [dropped, ...]}`` in
            first-seen order with duplicates dropped.
        combine_fields: Text fields merged by sentence-level dedup rather
            than first-wins. A sentence already present in the stored
            value (compared case- and whitespace-insensitively) is not
            appended twice.
        combine_max_length: Truncation bound for combined text fields.
            Truncation prefers a sentence boundary inside the bound and
            falls back to a hard cut.
        fill_empty: Promote an incoming value into a field the entity has
            empty. On by default: it cannot overwrite anything, so it is
            strictly an enrichment. Note it is not inert for *future*
            resolution — ``_context_resolve`` reads stored context fields
            and ``_fuzzy_resolve`` reads stored fuzzy fields, so a field
            that was empty (and therefore skipped) starts participating
            once filled. ESS computation is unaffected: both paths filter
            the stored blob to ``schema.ess_fields`` before hashing.

    Note:
        Identity fields (``schema.ess_fields``) are never folded. The
        entity's stored ``ess_digest`` is computed from them, so
        rewriting one would desynchronize the digest from the fields it
        hashes. Disagreement on an identity field is reported as a
        conflict with ``reason="identity"`` and otherwise left alone.
    """

    precedence: str = "keep-first"          # "keep-first" | "richest"
    conflicts: str = "keep-first"           # "keep-first" | "keep-all"
    combine_fields: frozenset[str] = frozenset()
    combine_max_length: int = 4096
    fill_empty: bool = True

    _PRECEDENCE = ("keep-first", "richest")
    _CONFLICTS = ("keep-first", "keep-all")

    def __post_init__(self) -> None:
        if self.precedence not in self._PRECEDENCE:
            raise ValueError(
                f"precedence must be one of {self._PRECEDENCE}, got {self.precedence!r}"
            )
        if self.conflicts not in self._CONFLICTS:
            raise ValueError(
                f"conflicts must be one of {self._CONFLICTS}, got {self.conflicts!r}"
            )
        if self.combine_max_length < 1:
            raise ValueError("combine_max_length must be >= 1")
        # Accept any iterable of field names; freeze it so the policy stays
        # hashable and cannot be mutated behind a registry's back.
        if not isinstance(self.combine_fields, frozenset):
            object.__setattr__(self, "combine_fields", frozenset(self.combine_fields))


DEFAULT_POLICY = ResolutionPolicy()

# Keys the fold treats as bookkeeping rather than entity content.
_FOLD_META_KEYS = frozenset({"__conflicts__"})

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class FieldConflict:
    """One field where stored and incoming values genuinely disagree.

    ``kept`` is the value that survives the fold, ``dropped`` the one
    suppressed. ``reason`` is ``"identity"`` for an ``ess_fields``
    disagreement (reported, never folded) or ``"scalar"`` otherwise.
    """

    field: str
    kept: str
    dropped: str
    reason: str = "scalar"

    def as_dict(self) -> dict[str, str]:
        return {
            "field": self.field,
            "kept": self.kept,
            "dropped": self.dropped,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class FoldResult:
    """Outcome of folding one candidate into one stored field blob.

    ``fields`` is a new dict — :func:`fold_entity_fields` never mutates
    its arguments — and ``changed`` says whether it differs from the blob
    it came from, so callers can skip a no-op write.
    """

    fields: dict[str, Any]
    conflicts: tuple[FieldConflict, ...] = ()
    filled: tuple[str, ...] = ()
    combined: tuple[str, ...] = ()
    changed: bool = False


def _is_empty(value: Any) -> bool:
    """True for None, empty string, or whitespace-only string."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def scalars_equivalent(current: Any, incoming: Any) -> bool:
    """Formatting-insensitive scalar equality.

    Values differing only by surrounding whitespace, internal whitespace
    runs, or case are the same value for folding purposes. Deliberately
    looser than ESS equality (exact after ``.strip().lower()``) because
    this compares *stored text*, not identity.

    >>> scalars_equivalent("Ada  Lovelace", "ada lovelace")
    True
    >>> scalars_equivalent("Ada", "Grace")
    False
    """
    if current is None or incoming is None:
        return current is incoming
    a = re.sub(r"\s+", " ", str(current)).strip().lower()
    b = re.sub(r"\s+", " ", str(incoming)).strip().lower()
    return a == b


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def _combine_text(current: str, incoming: str, max_length: int) -> str:
    """Append the sentences of ``incoming`` not already in ``current``.

    Order is preserved and comparison is case/whitespace-insensitive, so
    the result is a pure function of the two inputs.
    """
    seen = {re.sub(r"\s+", " ", s).lower() for s in _sentences(current)}
    out = _sentences(current)
    for sentence in _sentences(incoming):
        key = re.sub(r"\s+", " ", sentence).lower()
        if key not in seen:
            seen.add(key)
            out.append(sentence)
    combined = " ".join(out)
    if len(combined) <= max_length:
        return combined
    truncated = combined[:max_length]
    boundary = max(truncated.rfind(". "), truncated.rfind("! "), truncated.rfind("? "))
    if boundary > 0:
        return truncated[: boundary + 1]
    return truncated


def _richness(fields: Any) -> int:
    """Count non-empty, non-meta values. Order-independent by construction."""
    if not isinstance(fields, dict):
        return 0
    return sum(
        1 for k, v in fields.items() if k not in _FOLD_META_KEYS and not _is_empty(v)
    )


def _foldable_fields(schema: CanonicalSchema) -> list[str]:
    """A schema's declared non-identity fields, in declaration order.

    Identity fields back the stored ESS digest and are never rewritten.
    """
    identity = frozenset(schema.ess_fields)
    return [
        f
        for f in list(schema.context_fields) + list(schema.metadata_fields)
        if f not in identity
    ]


def fold_entity_fields(
    current: dict[str, Any],
    incoming: dict[str, Any],
    schema: CanonicalSchema,
    policy: ResolutionPolicy = DEFAULT_POLICY,
) -> FoldResult:
    """Fold a resolved candidate's fields into an entity's stored blob.

    Pure: neither argument is mutated and the returned ``fields`` is a
    fresh dict. Given the same inputs it always returns the same output —
    no clock, no randomness, no reliance on dict ordering beyond the
    caller's own.

    Rules, in order:

    1. Identity fields (``schema.ess_fields``) are never rewritten; a
       disagreement is reported with ``reason="identity"``.
    2. An empty stored field takes the incoming value when
       ``policy.fill_empty`` (default). This cannot overwrite anything.
    3. Equivalent values (:func:`scalars_equivalent`) are not a conflict
       and change nothing.
    4. ``policy.combine_fields`` are merged by sentence-level dedup.
    5. Anything left is a conflict, resolved by ``policy.precedence`` and
       recorded either way.

    Only schema-declared fields are folded; unknown keys on the candidate
    are ignored, matching what ``_create_entity`` stores.
    """
    folded = dict(current)
    conflicts: list[FieldConflict] = []
    filled: list[str] = []
    combined: list[str] = []

    # Rule 1 — identity fields report, never fold.
    for name in schema.ess_fields:
        if name not in incoming:
            continue
        stored, candidate = current.get(name), incoming[name]
        if _is_empty(stored) or _is_empty(candidate):
            continue
        if not scalars_equivalent(stored, candidate):
            conflicts.append(
                FieldConflict(name, str(stored), str(candidate), reason="identity")
            )

    for name in _foldable_fields(schema):
        if name not in incoming:
            continue
        candidate = incoming[name]
        if _is_empty(candidate):
            continue
        stored = folded.get(name)

        # Rule 2 — fill an empty field.
        if _is_empty(stored):
            if policy.fill_empty:
                folded[name] = str(candidate)
                filled.append(name)
            continue

        # Rule 3 — same value, nothing to do.
        if scalars_equivalent(stored, candidate):
            continue

        # Rule 4 — sentence-level combine.
        if name in policy.combine_fields:
            merged = _combine_text(
                str(stored), str(candidate), policy.combine_max_length
            )
            if merged != str(stored):
                folded[name] = merged
                combined.append(name)
            continue

        # Rule 5 — genuine conflict.
        if policy.precedence == "richest" and _richness(incoming) > _richness(current):
            kept, dropped = str(candidate), str(stored)
            folded[name] = kept
        else:
            kept, dropped = str(stored), str(candidate)
        conflicts.append(FieldConflict(name, kept, dropped, reason="scalar"))

    if policy.conflicts == "keep-all" and conflicts:
        record = dict(folded.get("__conflicts__") or {})
        for conflict in conflicts:
            dropped_values = list(record.get(conflict.field, []))
            if conflict.dropped not in dropped_values:
                dropped_values.append(conflict.dropped)
            record[conflict.field] = dropped_values
        folded["__conflicts__"] = record

    return FoldResult(
        fields=folded,
        conflicts=tuple(conflicts),
        filled=tuple(filled),
        combined=tuple(combined),
        changed=folded != current,
    )


def field_conflicts(
    current: dict[str, Any],
    incoming: dict[str, Any],
    schema: CanonicalSchema,
    policy: ResolutionPolicy = DEFAULT_POLICY,
) -> list[FieldConflict]:
    """Fields where folding ``incoming`` into ``current`` would disagree.

    The non-mutating dry-run twin of :func:`fold_entity_fields`: it
    writes nothing and returns exactly the conflicts a real fold with the
    same arguments would report. Keeping the predicate available
    separately is what lets a dry run be *exact* rather than an
    approximation that drifts from the code that actually runs.
    """
    return list(fold_entity_fields(current, incoming, schema, policy).conflicts)


# ── SQLite schema ────────────────────────────────────────────────────

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS _meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entities (
    canonical_id TEXT PRIMARY KEY,
    ess_digest TEXT NOT NULL,
    ess_fields TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    source_count INTEGER DEFAULT 1,
    merged_from TEXT,
    UNIQUE(ess_digest)
);

CREATE TABLE IF NOT EXISTS sightings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_id TEXT NOT NULL REFERENCES entities(canonical_id),
    source_name TEXT NOT NULL,
    source_id TEXT NOT NULL,
    fields TEXT NOT NULL,
    raw TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(source_name, source_id)
);

CREATE TABLE IF NOT EXISTS merges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keep_id TEXT NOT NULL,
    discard_id TEXT NOT NULL,
    tier TEXT NOT NULL,
    confidence REAL NOT NULL,
    reason TEXT,
    merged_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ess ON entities(ess_digest);
CREATE INDEX IF NOT EXISTS idx_sightings_entity ON sightings(canonical_id);
CREATE INDEX IF NOT EXISTS idx_sightings_source ON sightings(source_name, source_id);
"""


# ── Registry ─────────────────────────────────────────────────────────

class CanonicalRegistry:
    """Persistent entity registry with tier-based resolution.

    Stores canonical entities and their sightings (source-specific records).
    Resolution is schema-driven — the same engine handles inmates, memory
    entities, products, etc.

    Storage: SQLite (WAL mode), one DB per registry.
    """

    def __init__(
        self,
        db_path: str | Path,
        schema: CanonicalSchema,
        *,
        emitter: TraceEmitter | None = None,
        policy: ResolutionPolicy = DEFAULT_POLICY,
    ):
        """Open or create a registry.

        Creates tables on first run. Schema is stored in DB metadata
        so mismatches are caught early.

        ``policy`` governs attribute folding on a T1/T2 match — see
        :class:`ResolutionPolicy`. The default fills fields the entity has
        empty and keeps the stored value on a genuine conflict, so no
        value that exists today is ever overwritten; conflicts are
        reported rather than silently absorbed.
        """
        self.db_path = Path(db_path).expanduser()
        self.schema = schema
        self.policy = policy
        self._emitter = emitter
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_CREATE_SQL)

        self._validate_or_store_schema()

    def _validate_or_store_schema(self) -> None:
        """Store schema on first run, validate on subsequent opens."""
        row = self._conn.execute(
            "SELECT value FROM _meta WHERE key = 'schema_hash'"
        ).fetchone()

        expected = self.schema.schema_hash()

        if row is None:
            now = _now_iso()
            self._conn.executemany(
                "INSERT INTO _meta (key, value) VALUES (?, ?)",
                [
                    ("schema_name", self.schema.name),
                    ("schema_hash", expected),
                    ("created_at", now),
                    ("version", "1"),
                ],
            )
            self._conn.commit()
        else:
            if row["value"] != expected:
                raise ValueError(
                    f"Schema mismatch: DB was created with a different "
                    f"CanonicalSchema (expected hash {expected[:12]}..., "
                    f"got {row['value'][:12]}...)"
                )

    def _compute_ess(self, candidate: dict[str, Any]) -> EntitySenseSig:
        """Build ESS from candidate using schema's ess_fields."""
        fields = {}
        for f in self.schema.ess_fields:
            v = candidate.get(f)
            if v is not None:
                fields[f] = str(v)
        return EntitySenseSig.compute(**fields)

    def resolve(self, candidate: dict[str, Any]) -> ResolutionResult:
        """Resolve a candidate record against the registry.

        1. Compute ESS from candidate's ess_fields
        2. Exact ESS lookup → T1
        3. Fuzzy field scan on fuzzy_fields → T2/T3
        4. Context field check → T4 or NO_MATCH

        Does NOT modify the registry. Call upsert() to persist.

        **`candidate` is NOT auto-normalized.** ESS computation does
        ``.strip().lower()`` internally — that's the only baked-in
        transformation. If two sources emit the same person as
        ``"K. Yamamoto"`` and ``"Kazuhiko Yamamoto"`` and you want them
        to match, pre-normalize each candidate (e.g. via
        ``apply_normalizers(cand, {"first_name": first_initial})``)
        before calling this method. See
        ``docs/entity-resolution.md`` § "Normalize before you resolve".
        """
        ess = self._compute_ess(candidate)

        # T1: exact ESS match
        existing = self.find_by_ess(ess)
        if existing:
            entity = existing[0]
            result = ResolutionResult(
                tier=ResolutionTier.T1_EXACT,
                confidence=ResolutionTier.T1_EXACT.confidence,
                canonical_id=entity["canonical_id"],
                field_matches={f: True for f in self.schema.ess_fields},
                notes="Exact ESS match",
            )
            if self._emitter is not None:
                self._emitter.emit(
                    "candidate_resolved",
                    tier=result.tier.value,
                    confidence=result.confidence,
                    canonical_id=result.canonical_id,
                )
            return result

        # T2/T3: fuzzy matching on fuzzy_fields
        if self.schema.fuzzy_fields:
            result = self._fuzzy_resolve(candidate, ess)
            if result is not None:
                if self._emitter is not None:
                    self._emitter.emit(
                        "candidate_resolved",
                        tier=result.tier.value,
                        confidence=result.confidence,
                        canonical_id=result.canonical_id,
                    )
                return result

        # T4: context field overlap
        if self.schema.context_fields:
            result = self._context_resolve(candidate, ess)
            if result is not None:
                if self._emitter is not None:
                    self._emitter.emit(
                        "candidate_resolved",
                        tier=result.tier.value,
                        confidence=result.confidence,
                        canonical_id=result.canonical_id,
                    )
                return result

        # No match
        result = ResolutionResult(
            tier=ResolutionTier.NO_MATCH,
            confidence=0.0,
            canonical_id=None,
            field_matches={},
            notes="No matching entity found",
        )
        if self._emitter is not None:
            self._emitter.emit(
                "candidate_resolved",
                tier=result.tier.value,
                confidence=result.confidence,
                canonical_id=result.canonical_id,
            )
        return result

    def _fuzzy_resolve(
        self, candidate: dict[str, Any], candidate_ess: EntitySenseSig
    ) -> ResolutionResult | None:
        """Attempt T2/T3 resolution via fuzzy field matching."""
        rows = self._conn.execute(
            "SELECT canonical_id, ess_digest, ess_fields FROM entities"
        ).fetchall()

        # ESS fields not covered by fuzzy matching — these must not
        # contradict between candidate and stored entity.
        non_fuzzy_ess = [
            f for f in self.schema.ess_fields
            if f not in self.schema.fuzzy_fields
        ]

        best_score = 0.0
        best_entity = None
        best_field_matches: dict[str, bool] = {}

        for row in rows:
            stored_fields = json.loads(row["ess_fields"])
            field_matches: dict[str, bool] = {}
            total_score = 0.0
            scored_fields = 0

            for fname, threshold in self.schema.fuzzy_fields.items():
                cand_val = candidate.get(fname)
                stored_val = stored_fields.get(fname)
                if cand_val is None or stored_val is None:
                    continue
                score = fuzzy_score(str(cand_val), str(stored_val))
                field_matches[fname] = score >= threshold
                total_score += score
                scored_fields += 1

            if scored_fields == 0:
                continue

            avg_score = total_score / scored_fields

            # Also check ESS overlap for non-fuzzy fields
            stored_ess = EntitySenseSig.compute(**{
                k: v for k, v in stored_fields.items()
                if k in self.schema.ess_fields
            })
            overlap = candidate_ess.overlap(stored_ess)

            # Check for explicit disagreement on non-fuzzy ESS fields.
            # If an ESS field is present in both candidate and stored
            # entity but has a different value, that's a strong signal
            # they're different entities — reject the match.
            has_contradiction = False
            for nf in non_fuzzy_ess:
                cand_raw = candidate.get(nf)
                stored_raw = stored_fields.get(nf)
                if cand_raw is None or stored_raw is None:
                    continue
                cand_norm = str(cand_raw).strip().lower()
                stored_norm = str(stored_raw).strip().lower()
                if cand_norm and stored_norm and cand_norm != stored_norm:
                    has_contradiction = True
                    logger.debug(
                        "skip %s: %s mismatch %r vs %r",
                        row["canonical_id"][:12], nf, cand_norm, stored_norm,
                    )
                    break

            if has_contradiction:
                continue  # skip — explicit field disagreement

            # Combined score: fuzzy match quality + field overlap
            combined = (avg_score + overlap) / 2

            if combined > best_score:
                best_score = combined
                best_entity = row
                best_field_matches = field_matches

        if best_entity is None:
            return None

        all_fuzzy_pass = all(best_field_matches.values()) if best_field_matches else False

        if all_fuzzy_pass and best_score >= 0.85:
            return ResolutionResult(
                tier=ResolutionTier.T2_STRONG,
                confidence=ResolutionTier.T2_STRONG.confidence,
                canonical_id=best_entity["canonical_id"],
                field_matches=best_field_matches,
                notes=f"Strong fuzzy match (score={best_score:.2f})",
            )
        elif best_score >= 0.65:
            return ResolutionResult(
                tier=ResolutionTier.T3_FUZZY,
                confidence=ResolutionTier.T3_FUZZY.confidence,
                canonical_id=best_entity["canonical_id"],
                field_matches=best_field_matches,
                notes=f"Fuzzy match (score={best_score:.2f})",
            )

        return None

    def _context_resolve(
        self, candidate: dict[str, Any], candidate_ess: EntitySenseSig
    ) -> ResolutionResult | None:
        """Attempt T4 resolution via context field overlap."""
        rows = self._conn.execute(
            "SELECT canonical_id, ess_fields FROM entities"
        ).fetchall()

        for row in rows:
            stored_fields = json.loads(row["ess_fields"])
            context_matches = {}
            matched = 0

            for cf in self.schema.context_fields:
                cand_val = candidate.get(cf)
                stored_val = stored_fields.get(cf)
                if cand_val is not None and stored_val is not None:
                    match = str(cand_val).strip().lower() == str(stored_val).strip().lower()
                    context_matches[cf] = match
                    if match:
                        matched += 1

            # Also need some ESS overlap to consider T4
            stored_ess = EntitySenseSig.compute(**{
                k: v for k, v in stored_fields.items()
                if k in self.schema.ess_fields
            })
            overlap = candidate_ess.overlap(stored_ess)

            if matched > 0 and overlap > 0:
                return ResolutionResult(
                    tier=ResolutionTier.T4_WEAK,
                    confidence=ResolutionTier.T4_WEAK.confidence,
                    canonical_id=row["canonical_id"],
                    field_matches=context_matches,
                    notes=f"Weak context match (overlap={overlap:.2f}, "
                          f"context={matched}/{len(self.schema.context_fields)})",
                )

        return None

    def upsert(
        self,
        candidate: dict[str, Any],
        resolution: ResolutionResult,
        source_name: str,
        source_id: str,
        raw: dict | None = None,
    ) -> str:
        """Insert or update an entity based on resolution.

        - T1/T2: Add sighting to existing canonical entity, update last_seen
        - T3: Merge with logging (records the merge event)
        - T4: Create new entity (don't auto-merge)
        - NO_MATCH: Create new entity

        Returns canonical_id.
        """
        now = _now_iso()
        ess = self._compute_ess(candidate)

        if resolution.tier in (ResolutionTier.T1_EXACT, ResolutionTier.T2_STRONG):
            canonical_id = resolution.canonical_id
            # Fold the candidate's fields into the stored blob before
            # touching the counters: without this the canonical record is
            # whatever the *first* sighting said, forever.
            fold = self._fold_into(canonical_id, candidate)
            if fold is not None and fold.changed:
                self._conn.execute(
                    "UPDATE entities SET ess_fields = ?, last_seen = ?, "
                    "source_count = source_count + 1 WHERE canonical_id = ?",
                    (json.dumps(fold.fields, sort_keys=True), now, canonical_id),
                )
            else:
                self._conn.execute(
                    "UPDATE entities SET last_seen = ?, source_count = source_count + 1 "
                    "WHERE canonical_id = ?",
                    (now, canonical_id),
                )
        elif resolution.tier == ResolutionTier.T3_FUZZY:
            # Create new entity, then record the merge event for review
            canonical_id = self._create_entity(ess, candidate, now)
            if resolution.canonical_id:
                self._conn.execute(
                    "INSERT INTO merges (keep_id, discard_id, tier, confidence, reason, merged_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        resolution.canonical_id,
                        canonical_id,
                        resolution.tier.value,
                        resolution.confidence,
                        resolution.notes,
                        now,
                    ),
                )
        else:
            # T4 or NO_MATCH — create new entity
            canonical_id = self._create_entity(ess, candidate, now)

        # Record sighting
        self._conn.execute(
            "INSERT OR REPLACE INTO sightings "
            "(canonical_id, source_name, source_id, fields, raw, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                canonical_id,
                source_name,
                source_id,
                json.dumps(candidate, sort_keys=True),
                json.dumps(raw, sort_keys=True) if raw else None,
                now,
            ),
        )
        self._conn.commit()
        return canonical_id

    def _stored_fields(self, canonical_id: str | None) -> dict[str, Any] | None:
        """The entity's stored field blob, or None if it does not exist."""
        if not canonical_id:
            return None
        row = self._conn.execute(
            "SELECT ess_fields FROM entities WHERE canonical_id = ?",
            (canonical_id,),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["ess_fields"])

    def _fold_into(
        self, canonical_id: str | None, candidate: dict[str, Any]
    ) -> FoldResult | None:
        """Fold ``candidate`` into a stored entity. Computes only — no write."""
        stored = self._stored_fields(canonical_id)
        if stored is None:
            return None
        return fold_entity_fields(stored, candidate, self.schema, self.policy)

    def plan(
        self, candidate: dict[str, Any], resolution: ResolutionResult
    ) -> FoldResult | None:
        """What :meth:`upsert` would do to the stored fields — without doing it.

        Returns ``None`` when the resolution would create a new entity
        (nothing to fold into), otherwise the exact :class:`FoldResult`
        that ``upsert`` will apply. Because both paths call the same pure
        :func:`fold_entity_fields`, a plan cannot drift from the write.
        """
        if resolution.tier not in (ResolutionTier.T1_EXACT, ResolutionTier.T2_STRONG):
            return None
        return self._fold_into(resolution.canonical_id, candidate)

    def _create_entity(
        self, ess: EntitySenseSig, candidate: dict[str, Any], now: str
    ) -> str:
        """Create a new canonical entity."""
        canonical_id = uuid.uuid4().hex
        ess_fields_dict = {
            f: str(candidate[f])
            for f in self.schema.ess_fields
            if f in candidate and candidate[f] is not None
        }
        # Also store context fields for future resolution
        for f in self.schema.context_fields:
            if f in candidate and candidate[f] is not None:
                ess_fields_dict[f] = str(candidate[f])

        # Store metadata fields (informational only, not used in resolution)
        for f in self.schema.metadata_fields:
            if f in candidate and candidate[f] is not None:
                ess_fields_dict[f] = str(candidate[f])

        self._conn.execute(
            "INSERT INTO entities "
            "(canonical_id, ess_digest, ess_fields, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                canonical_id,
                ess.digest,
                json.dumps(ess_fields_dict, sort_keys=True),
                now,
                now,
            ),
        )
        return canonical_id

    def merge(self, keep_id: str, discard_id: str, reason: str = "") -> None:
        """Manually merge two canonical entities.

        Moves all sightings from discard to keep. Records merge provenance.
        """
        now = _now_iso()

        # Move sightings
        self._conn.execute(
            "UPDATE sightings SET canonical_id = ? WHERE canonical_id = ?",
            (keep_id, discard_id),
        )

        # Update source_count on keep
        count = self._conn.execute(
            "SELECT COUNT(*) as c FROM sightings WHERE canonical_id = ?",
            (keep_id,),
        ).fetchone()["c"]
        self._conn.execute(
            "UPDATE entities SET source_count = ?, last_seen = ? WHERE canonical_id = ?",
            (count, now, keep_id),
        )

        # Record merged_from
        keep_row = self._conn.execute(
            "SELECT merged_from FROM entities WHERE canonical_id = ?",
            (keep_id,),
        ).fetchone()
        merged_list = json.loads(keep_row["merged_from"]) if keep_row["merged_from"] else []
        merged_list.append(discard_id)
        self._conn.execute(
            "UPDATE entities SET merged_from = ? WHERE canonical_id = ?",
            (json.dumps(merged_list), keep_id),
        )

        # Record merge event
        self._conn.execute(
            "INSERT INTO merges (keep_id, discard_id, tier, confidence, reason, merged_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (keep_id, discard_id, "manual", 1.0, reason, now),
        )

        # Remove discarded entity
        self._conn.execute(
            "DELETE FROM entities WHERE canonical_id = ?",
            (discard_id,),
        )

        self._conn.commit()

        if self._emitter is not None:
            self._emitter.emit(
                "entity_merged",
                keep_id=keep_id,
                discard_id=discard_id,
                tier="manual",
                reason=reason,
            )

    def get_entity(self, canonical_id: str) -> dict[str, Any] | None:
        """Fetch canonical entity with all sightings."""
        row = self._conn.execute(
            "SELECT * FROM entities WHERE canonical_id = ?",
            (canonical_id,),
        ).fetchone()
        if row is None:
            return None

        entity = dict(row)
        entity["ess_fields"] = json.loads(entity["ess_fields"])
        if entity["merged_from"]:
            entity["merged_from"] = json.loads(entity["merged_from"])
        entity["sightings"] = [
            {**dict(s), "fields": json.loads(s["fields"]),
             "raw": json.loads(s["raw"]) if s["raw"] else None}
            for s in self._conn.execute(
                "SELECT * FROM sightings WHERE canonical_id = ?",
                (canonical_id,),
            ).fetchall()
        ]
        return entity

    def find_by_ess(self, ess: EntitySenseSig) -> list[dict[str, Any]]:
        """Exact ESS lookup."""
        rows = self._conn.execute(
            "SELECT * FROM entities WHERE ess_digest = ?",
            (ess.digest,),
        ).fetchall()
        return [
            {**dict(r), "ess_fields": json.loads(r["ess_fields"])}
            for r in rows
        ]

    def find_fuzzy(
        self, fields: dict[str, str], limit: int = 10
    ) -> list[ResolutionResult]:
        """Fuzzy search across fuzzy_fields. Uses string similarity."""
        results: list[tuple[float, ResolutionResult]] = []

        rows = self._conn.execute(
            "SELECT canonical_id, ess_fields FROM entities"
        ).fetchall()

        for row in rows:
            stored = json.loads(row["ess_fields"])
            field_matches: dict[str, bool] = {}
            total_score = 0.0
            count = 0

            for fname, value in fields.items():
                stored_val = stored.get(fname)
                if stored_val is None:
                    continue
                score = fuzzy_score(value, stored_val)
                threshold = self.schema.fuzzy_fields.get(fname, 0.8)
                field_matches[fname] = score >= threshold
                total_score += score
                count += 1

            if count == 0:
                continue

            avg = total_score / count
            if avg < 0.5:
                continue

            tier = ResolutionTier.T2_STRONG if avg >= 0.85 else (
                ResolutionTier.T3_FUZZY if avg >= 0.65 else ResolutionTier.T4_WEAK
            )
            results.append((avg, ResolutionResult(
                tier=tier,
                confidence=tier.confidence,
                canonical_id=row["canonical_id"],
                field_matches=field_matches,
                notes=f"Fuzzy search (score={avg:.2f})",
            )))

        results.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in results[:limit]]

    def entities(self, since: str | None = None) -> Iterator[dict[str, Any]]:
        """Iterate canonical entities, optionally filtered by last_seen."""
        if since:
            rows = self._conn.execute(
                "SELECT * FROM entities WHERE last_seen >= ? ORDER BY last_seen DESC",
                (since,),
            )
        else:
            rows = self._conn.execute(
                "SELECT * FROM entities ORDER BY last_seen DESC"
            )

        for row in rows:
            entity = dict(row)
            entity["ess_fields"] = json.loads(entity["ess_fields"])
            if entity["merged_from"]:
                entity["merged_from"] = json.loads(entity["merged_from"])
            yield entity

    def sightings(self, canonical_id: str) -> list[dict[str, Any]]:
        """All source sightings for an entity."""
        rows = self._conn.execute(
            "SELECT * FROM sightings WHERE canonical_id = ? ORDER BY created_at",
            (canonical_id,),
        ).fetchall()
        return [
            {**dict(r), "fields": json.loads(r["fields"]),
             "raw": json.loads(r["raw"]) if r["raw"] else None}
            for r in rows
        ]

    def stats(self) -> dict[str, int]:
        """Registry statistics: total entities, sightings, merges, source breakdown."""
        entities = self._conn.execute("SELECT COUNT(*) as c FROM entities").fetchone()["c"]
        sightings = self._conn.execute("SELECT COUNT(*) as c FROM sightings").fetchone()["c"]
        merges = self._conn.execute("SELECT COUNT(*) as c FROM merges").fetchone()["c"]

        source_rows = self._conn.execute(
            "SELECT source_name, COUNT(*) as c FROM sightings GROUP BY source_name"
        ).fetchall()
        sources = {r["source_name"]: r["c"] for r in source_rows}

        return {
            "entities": entities,
            "sightings": sightings,
            "merges": merges,
            "sources": sources,
        }

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __enter__(self) -> CanonicalRegistry:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


# ── Batch convenience ────────────────────────────────────────────────

@dataclass
class ResolutionReport:
    """Audit record of one :func:`canonicalize_batch` run.

    Deliberately timestamp-free so re-running the same batch over the
    same registry state produces a byte-identical report. That is what
    makes two reports diffable — a normalizer change that quietly moves
    forty records from T2 to T4 shows up as a diff, not as silence.
    """

    records: int = 0
    dry_run: bool = False
    tiers: dict[str, int] = field(default_factory=dict)
    fields_filled: dict[str, int] = field(default_factory=dict)
    fields_combined: dict[str, int] = field(default_factory=dict)
    conflicts: list[dict[str, str]] = field(default_factory=list)
    entities_created: int = 0
    entities_folded: int = 0

    @property
    def identity_conflicts(self) -> int:
        """Conflicts on an ``ess_fields`` value — never folded, always a smell."""
        return sum(1 for c in self.conflicts if c.get("reason") == "identity")

    def to_dict(self) -> dict[str, Any]:
        """Stable, JSON-serializable form with deterministic key order."""
        return {
            "records": self.records,
            "dry_run": self.dry_run,
            "tiers": dict(sorted(self.tiers.items())),
            "fields_filled": dict(sorted(self.fields_filled.items())),
            "fields_combined": dict(sorted(self.fields_combined.items())),
            "conflicts": self.conflicts,
            "entities_created": self.entities_created,
            "entities_folded": self.entities_folded,
        }


def canonicalize_batch(
    records: list[dict[str, Any]],
    registry: CanonicalRegistry,
    source_name: str,
    source_id_field: str = "source_id",
    *,
    dry_run: bool = False,
    report: ResolutionReport | None = None,
) -> list[tuple[dict[str, Any], ResolutionResult]]:
    """Resolve and upsert a batch of records.

    Returns list of (record, resolution) tuples for the caller
    to act on (e.g., build alerts, update state).

    With ``dry_run=True`` nothing is written: every record is resolved and
    its fold computed through the same pure :func:`fold_entity_fields`
    the real path uses, so the plan is exact rather than an estimate.
    Note that a dry run resolves each record against the registry as it
    stands *now* — it does not model records in the same batch resolving
    against each other, since none of them are committed.

    Pass a :class:`ResolutionReport` as ``report`` to collect tier counts,
    filled fields, and conflicts. Reports are timestamp-free and therefore
    diffable across runs.
    """
    results: list[tuple[dict[str, Any], ResolutionResult]] = []

    for record in records:
        resolution = registry.resolve(record)
        source_id = str(record.get(source_id_field, uuid.uuid4().hex))

        if report is not None:
            report.records += 1
            report.dry_run = dry_run
            tier = resolution.tier.value
            report.tiers[tier] = report.tiers.get(tier, 0) + 1
            fold = registry.plan(record, resolution)
            if fold is None:
                report.entities_created += 1
            else:
                if fold.changed:
                    report.entities_folded += 1
                for name in fold.filled:
                    report.fields_filled[name] = report.fields_filled.get(name, 0) + 1
                for name in fold.combined:
                    report.fields_combined[name] = (
                        report.fields_combined.get(name, 0) + 1
                    )
                report.conflicts.extend(c.as_dict() for c in fold.conflicts)

        if not dry_run:
            registry.upsert(record, resolution, source_name, source_id)
        results.append((record, resolution))

    return results
