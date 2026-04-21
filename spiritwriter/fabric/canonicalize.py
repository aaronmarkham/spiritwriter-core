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
from typing import Any, Iterator

import logging

from spiritwriter.fabric.shard import _canonical_json, _sha256, _now_iso
from spiritwriter.fabric.emitter import TraceEmitter

logger = logging.getLogger(__name__)


# ── Normalization utilities ──────────────────────────────────────────

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

    def __init__(self, db_path: str | Path, schema: CanonicalSchema, *, emitter: TraceEmitter | None = None):
        """Open or create a registry.

        Creates tables on first run. Schema is stored in DB metadata
        so mismatches are caught early.
        """
        self.db_path = Path(db_path).expanduser()
        self.schema = schema
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
            # Update last_seen and source_count
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

def canonicalize_batch(
    records: list[dict[str, Any]],
    registry: CanonicalRegistry,
    source_name: str,
    source_id_field: str = "source_id",
) -> list[tuple[dict[str, Any], ResolutionResult]]:
    """Resolve and upsert a batch of records.

    Returns list of (record, resolution) tuples for the caller
    to act on (e.g., build alerts, update state).
    """
    results: list[tuple[dict[str, Any], ResolutionResult]] = []

    for record in records:
        resolution = registry.resolve(record)
        source_id = str(record.get(source_id_field, uuid.uuid4().hex))
        registry.upsert(record, resolution, source_name, source_id)
        results.append((record, resolution))

    return results
