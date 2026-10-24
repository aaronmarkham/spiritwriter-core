"""Provider-neutral shingled extraction with complete audit records.

The caller owns extraction and parsing.  This module only plans overlapping
source windows, invokes a synchronous callback, records every observation, and
derives exact-key N-of-K consensus dispositions.  It deliberately has no LLM
or provider dependency.

Persisted identifiers use the canonical JSON and SHA-256 primitives already
used by the fabric layer.  The domain tags below are versioned so this module
can evolve without changing existing shard identifiers.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Protocol

from spiritwriter.fabric.shard import _canonical_json, _sha256


__all__ = [
    "CandidateRecord",
    "CandidateValue",
    "ConsensusDisposition",
    "ConsensusStatus",
    "CoverageRecord",
    "ExtractionAttempt",
    "ExtractionOutcome",
    "ExtractionRecipe",
    "ExtractionRequest",
    "ExtractionResponse",
    "ExtractionRun",
    "GapRecord",
    "PlannedChunk",
    "RunStatus",
    "ShingledExtractor",
    "plan_chunks",
    "run_shingled_extraction",
]


_DOMAIN_PREFIX = "spiritwriter.ingest.shingled"


def _stable_id(kind: str, value: Any) -> str:
    """Return a domain-separated ID using the fabric's stable primitives."""
    domain = f"{_DOMAIN_PREFIX}/{kind}/v1\0".encode("utf-8")
    return _sha256(domain + _canonical_json(value))


def _json_text(value: Any, *, field_name: str) -> str:
    """Snapshot a JSON value as canonical text, with a useful error."""
    try:
        return _canonical_json(value).decode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{field_name} must be JSON-serializable") from exc


def _normalize_json_text(raw: str, *, field_name: str) -> str:
    if not isinstance(raw, str):
        raise TypeError(f"{field_name} must be a JSON string")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_name} must contain valid JSON") from exc
    return _json_text(value, field_name=field_name)


def _verify_id(data: Mapping[str, Any], key: str, actual: str) -> None:
    stored = data.get(key)
    if stored is not None and stored != actual:
        raise ValueError(f"{key} mismatch: stored={stored}, computed={actual}")


def _source_identity(source_ref: str, text: str) -> tuple[str, str]:
    source_hash = _sha256(text.encode("utf-8"))
    source_id = _stable_id(
        "source",
        {"source_ref": source_ref, "source_hash": source_hash, "length": len(text)},
    )
    return source_id, source_hash


class ExtractionOutcome(str, Enum):
    """Outcome reported for one chunk/pass extraction attempt."""

    COMPLETE = "complete"
    TRUNCATED = "truncated"
    MALFORMED = "malformed"
    SKIPPED = "skipped"
    ERROR = "error"


class ConsensusStatus(str, Enum):
    """Derived disposition for a candidate group."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    INELIGIBLE = "ineligible"


class RunStatus(str, Enum):
    """Aggregate state of an extraction run."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class ExtractionRecipe:
    """Immutable, content-addressed extraction configuration.

    ``extractor_id`` and ``extractor_version`` are caller declarations.  They
    should identify the prompt/parser/tool behavior behind the callback.
    Provider-specific settings belong in ``extractor_config_json``.
    """

    extractor_id: str
    target_chars: int = 2000
    overlap_chars: int = 400
    passes: int = 2
    consensus_threshold: int = 2
    extractor_version: str = ""
    extractor_config_json: str = "{}"

    def __post_init__(self) -> None:
        if not self.extractor_id:
            raise ValueError("extractor_id must not be empty")
        if self.target_chars < 1:
            raise ValueError("target_chars must be >= 1")
        if self.overlap_chars < 0:
            raise ValueError("overlap_chars must be >= 0")
        if self.overlap_chars >= self.target_chars:
            raise ValueError("overlap_chars must be smaller than target_chars")
        if self.passes < 1:
            raise ValueError("passes must be >= 1")
        if not 1 <= self.consensus_threshold <= self.passes:
            raise ValueError("consensus_threshold must be between 1 and passes")
        object.__setattr__(
            self,
            "extractor_config_json",
            _normalize_json_text(
                self.extractor_config_json, field_name="extractor_config_json"
            ),
        )

    @classmethod
    def create(
        cls,
        *,
        extractor_id: str,
        target_chars: int = 2000,
        overlap_chars: int = 400,
        passes: int = 2,
        consensus_threshold: int = 2,
        extractor_version: str = "",
        extractor_config: Any = None,
    ) -> "ExtractionRecipe":
        return cls(
            extractor_id=extractor_id,
            target_chars=target_chars,
            overlap_chars=overlap_chars,
            passes=passes,
            consensus_threshold=consensus_threshold,
            extractor_version=extractor_version,
            extractor_config_json=_json_text(
                {} if extractor_config is None else extractor_config,
                field_name="extractor_config",
            ),
        )

    @property
    def recipe_id(self) -> str:
        return _stable_id("recipe", self._content_dict())

    @property
    def extractor_config(self) -> Any:
        """Return a detached copy of the provider-neutral configuration."""
        return json.loads(self.extractor_config_json)

    def _content_dict(self) -> dict[str, Any]:
        return {
            "extractor_id": self.extractor_id,
            "extractor_version": self.extractor_version,
            "extractor_config": self.extractor_config,
            "target_chars": self.target_chars,
            "overlap_chars": self.overlap_chars,
            "passes": self.passes,
            "consensus_threshold": self.consensus_threshold,
        }

    def to_dict(self) -> dict[str, Any]:
        return {"recipe_id": self.recipe_id, **self._content_dict()}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExtractionRecipe":
        recipe = cls.create(
            extractor_id=data["extractor_id"],
            extractor_version=data.get("extractor_version", ""),
            extractor_config=data.get("extractor_config", {}),
            target_chars=data.get("target_chars", 2000),
            overlap_chars=data.get("overlap_chars", 400),
            passes=data.get("passes", 2),
            consensus_threshold=data.get("consensus_threshold", 2),
        )
        _verify_id(data, "recipe_id", recipe.recipe_id)
        return recipe


@dataclass(frozen=True)
class PlannedChunk:
    """One deterministic source slice with half-open character offsets."""

    source_id: str
    index: int
    start_offset: int
    end_offset: int
    text: str

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("chunk index must be >= 0")
        if self.start_offset < 0 or self.end_offset < self.start_offset:
            raise ValueError("invalid chunk offsets")
        if self.end_offset - self.start_offset != len(self.text):
            raise ValueError("chunk offsets must span exactly len(text) characters")

    @property
    def text_hash(self) -> str:
        return _sha256(self.text.encode("utf-8"))

    @property
    def chunk_id(self) -> str:
        return _stable_id("chunk", self._content_dict())

    def _content_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "index": self.index,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "text": self.text,
            "text_hash": self.text_hash,
        }

    def to_dict(self) -> dict[str, Any]:
        return {"chunk_id": self.chunk_id, **self._content_dict()}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PlannedChunk":
        chunk = cls(
            source_id=data["source_id"],
            index=data["index"],
            start_offset=data["start_offset"],
            end_offset=data["end_offset"],
            text=data["text"],
        )
        if data.get("text_hash") not in (None, chunk.text_hash):
            raise ValueError("text_hash mismatch")
        _verify_id(data, "chunk_id", chunk.chunk_id)
        return chunk


def plan_chunks(
    text: str,
    *,
    source_ref: str,
    target_chars: int = 2000,
    overlap_chars: int = 400,
) -> tuple[PlannedChunk, ...]:
    """Plan fixed-size overlapping windows over ``text``.

    Offsets are zero-based, half-open Python character offsets.  Fixed windows
    avoid tokenizer and line-ending dependencies, making the same Unicode text
    produce the same plan on every platform.
    """
    if target_chars < 1:
        raise ValueError("target_chars must be >= 1")
    if overlap_chars < 0:
        raise ValueError("overlap_chars must be >= 0")
    if overlap_chars >= target_chars:
        raise ValueError("overlap_chars must be smaller than target_chars")
    if not isinstance(text, str):
        raise TypeError("text must be str")
    if not isinstance(source_ref, str) or not source_ref:
        raise ValueError("source_ref must be a non-empty string")
    if not text:
        return ()

    source_id, _ = _source_identity(source_ref, text)
    chunks: list[PlannedChunk] = []
    start = 0
    while start < len(text):
        end = min(start + target_chars, len(text))
        chunks.append(
            PlannedChunk(
                source_id=source_id,
                index=len(chunks),
                start_offset=start,
                end_offset=end,
                text=text[start:end],
            )
        )
        if end == len(text):
            break
        start = end - overlap_chars
    return tuple(chunks)


@dataclass(frozen=True)
class CandidateValue:
    """Immutable callback output before source provenance is attached."""

    payload_json: str
    consensus_key: str | None = None
    confidence: float | None = None
    metadata_json: str = "{}"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "payload_json",
            _normalize_json_text(self.payload_json, field_name="payload_json"),
        )
        object.__setattr__(
            self,
            "metadata_json",
            _normalize_json_text(self.metadata_json, field_name="metadata_json"),
        )
        if self.consensus_key is not None:
            if not isinstance(self.consensus_key, str):
                raise TypeError("consensus_key must be str or None")
            if not self.consensus_key:
                raise ValueError("consensus_key must be non-empty or None")
        if self.confidence is not None:
            if isinstance(self.confidence, bool) or not isinstance(
                self.confidence, (int, float)
            ):
                raise TypeError("confidence must be a number or None")
            if not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
                raise ValueError("confidence must be finite and between 0 and 1")

    @classmethod
    def create(
        cls,
        payload: Any,
        *,
        consensus_key: str | None = None,
        confidence: float | None = None,
        metadata: Any = None,
    ) -> "CandidateValue":
        return cls(
            payload_json=_json_text(payload, field_name="payload"),
            consensus_key=consensus_key,
            confidence=confidence,
            metadata_json=_json_text(
                {} if metadata is None else metadata, field_name="metadata"
            ),
        )

    @property
    def payload(self) -> Any:
        return json.loads(self.payload_json)

    @property
    def metadata(self) -> Any:
        return json.loads(self.metadata_json)

    def to_dict(self) -> dict[str, Any]:
        data = {"payload": self.payload, "metadata": self.metadata}
        if self.consensus_key is not None:
            data["consensus_key"] = self.consensus_key
        if self.confidence is not None:
            data["confidence"] = self.confidence
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CandidateValue":
        return cls.create(
            data["payload"],
            consensus_key=data.get("consensus_key"),
            confidence=data.get("confidence"),
            metadata=data.get("metadata", {}),
        )


@dataclass(frozen=True)
class ExtractionResponse:
    """One callback response, including incomplete and malformed output."""

    outcome: ExtractionOutcome = ExtractionOutcome.COMPLETE
    candidates: tuple[CandidateValue, ...] = ()
    raw_output: str | None = None
    message: str | None = None
    metadata_json: str = "{}"

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, ExtractionOutcome):
            object.__setattr__(self, "outcome", ExtractionOutcome(self.outcome))
        if not isinstance(self.candidates, tuple):
            object.__setattr__(self, "candidates", tuple(self.candidates))
        if not all(isinstance(item, CandidateValue) for item in self.candidates):
            raise TypeError("candidates must contain CandidateValue records")
        if self.raw_output is not None and not isinstance(self.raw_output, str):
            raise TypeError("raw_output must be str or None")
        if self.message is not None and not isinstance(self.message, str):
            raise TypeError("message must be str or None")
        object.__setattr__(
            self,
            "metadata_json",
            _normalize_json_text(self.metadata_json, field_name="metadata_json"),
        )

    @classmethod
    def create(
        cls,
        *,
        outcome: ExtractionOutcome = ExtractionOutcome.COMPLETE,
        candidates: Iterable[CandidateValue] = (),
        raw_output: str | None = None,
        message: str | None = None,
        metadata: Any = None,
    ) -> "ExtractionResponse":
        return cls(
            outcome=outcome,
            candidates=tuple(candidates),
            raw_output=raw_output,
            message=message,
            metadata_json=_json_text(
                {} if metadata is None else metadata, field_name="metadata"
            ),
        )

    @property
    def metadata(self) -> Any:
        return json.loads(self.metadata_json)


@dataclass(frozen=True)
class ExtractionRequest:
    """Immutable input passed to the caller-supplied extractor."""

    recipe_id: str
    source_ref: str
    source_id: str
    chunk: PlannedChunk
    pass_index: int

    @property
    def request_id(self) -> str:
        return _stable_id(
            "request",
            {
                "recipe_id": self.recipe_id,
                "source_ref": self.source_ref,
                "source_id": self.source_id,
                "chunk_id": self.chunk.chunk_id,
                "pass_index": self.pass_index,
            },
        )


class ShingledExtractor(Protocol):
    """Synchronous provider-neutral extraction callback."""

    def __call__(self, request: ExtractionRequest) -> ExtractionResponse:
        ...


@dataclass(frozen=True)
class CandidateRecord:
    """A candidate sighting with source, chunk, pass, and ordinal provenance."""

    request_id: str
    source_ref: str
    source_id: str
    chunk_id: str
    chunk_index: int
    start_offset: int
    end_offset: int
    pass_index: int
    outcome: ExtractionOutcome
    ordinal: int
    payload_json: str
    consensus_key: str | None = None
    confidence: float | None = None
    metadata_json: str = "{}"

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, ExtractionOutcome):
            object.__setattr__(self, "outcome", ExtractionOutcome(self.outcome))
        if not isinstance(self.source_ref, str) or not self.source_ref:
            raise ValueError("source_ref must be a non-empty string")
        for name in ("request_id", "source_id", "chunk_id"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError(f"{name} must be a non-empty string")
        if self.chunk_index < 0:
            raise ValueError("chunk_index must be >= 0")
        if self.start_offset < 0 or self.end_offset < self.start_offset:
            raise ValueError("invalid candidate source offsets")
        if self.pass_index < 1:
            raise ValueError("pass_index must be >= 1")
        if self.ordinal < 0:
            raise ValueError("ordinal must be >= 0")
        if self.consensus_key is not None:
            if not isinstance(self.consensus_key, str):
                raise TypeError("consensus_key must be str or None")
            if not self.consensus_key:
                raise ValueError("consensus_key must be non-empty or None")
        if self.confidence is not None:
            if isinstance(self.confidence, bool) or not isinstance(
                self.confidence, (int, float)
            ):
                raise TypeError("confidence must be a number or None")
            if not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
                raise ValueError("confidence must be finite and between 0 and 1")
        object.__setattr__(
            self,
            "payload_json",
            _normalize_json_text(self.payload_json, field_name="payload_json"),
        )
        object.__setattr__(
            self,
            "metadata_json",
            _normalize_json_text(self.metadata_json, field_name="metadata_json"),
        )

    @property
    def candidate_id(self) -> str:
        return _stable_id("candidate", self._content_dict())

    @property
    def payload(self) -> Any:
        return json.loads(self.payload_json)

    @property
    def metadata(self) -> Any:
        return json.loads(self.metadata_json)

    def _content_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "request_id": self.request_id,
            "source_ref": self.source_ref,
            "source_id": self.source_id,
            "chunk_id": self.chunk_id,
            "chunk_index": self.chunk_index,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "pass_index": self.pass_index,
            "outcome": self.outcome.value,
            "ordinal": self.ordinal,
            "payload": self.payload,
            "metadata": self.metadata,
        }
        if self.consensus_key is not None:
            data["consensus_key"] = self.consensus_key
        if self.confidence is not None:
            data["confidence"] = self.confidence
        return data

    def to_dict(self) -> dict[str, Any]:
        return {"candidate_id": self.candidate_id, **self._content_dict()}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CandidateRecord":
        candidate = cls(
            request_id=data["request_id"],
            source_ref=data["source_ref"],
            source_id=data["source_id"],
            chunk_id=data["chunk_id"],
            chunk_index=data["chunk_index"],
            start_offset=data["start_offset"],
            end_offset=data["end_offset"],
            pass_index=data["pass_index"],
            outcome=ExtractionOutcome(data["outcome"]),
            ordinal=data["ordinal"],
            payload_json=_json_text(data["payload"], field_name="payload"),
            consensus_key=data.get("consensus_key"),
            confidence=data.get("confidence"),
            metadata_json=_json_text(data.get("metadata", {}), field_name="metadata"),
        )
        _verify_id(data, "candidate_id", candidate.candidate_id)
        return candidate


@dataclass(frozen=True)
class ExtractionAttempt:
    """Complete record of one callback invocation."""

    request_id: str
    chunk_id: str
    chunk_index: int
    start_offset: int
    end_offset: int
    pass_index: int
    outcome: ExtractionOutcome
    candidate_ids: tuple[str, ...] = ()
    raw_output: str | None = None
    message: str | None = None
    metadata_json: str = "{}"

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, ExtractionOutcome):
            object.__setattr__(self, "outcome", ExtractionOutcome(self.outcome))
        for name in ("request_id", "chunk_id"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError(f"{name} must be a non-empty string")
        if self.chunk_index < 0:
            raise ValueError("chunk_index must be >= 0")
        if self.start_offset < 0 or self.end_offset < self.start_offset:
            raise ValueError("invalid attempt source offsets")
        if self.pass_index < 1:
            raise ValueError("pass_index must be >= 1")
        if not isinstance(self.candidate_ids, tuple):
            object.__setattr__(self, "candidate_ids", tuple(self.candidate_ids))
        if not all(isinstance(item, str) and item for item in self.candidate_ids):
            raise ValueError("candidate_ids must contain non-empty strings")
        if self.raw_output is not None and not isinstance(self.raw_output, str):
            raise TypeError("raw_output must be str or None")
        if self.message is not None and not isinstance(self.message, str):
            raise TypeError("message must be str or None")
        object.__setattr__(
            self,
            "metadata_json",
            _normalize_json_text(self.metadata_json, field_name="metadata_json"),
        )

    @property
    def attempt_id(self) -> str:
        return _stable_id("attempt", self._content_dict())

    @property
    def metadata(self) -> Any:
        return json.loads(self.metadata_json)

    def _content_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "chunk_id": self.chunk_id,
            "chunk_index": self.chunk_index,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "pass_index": self.pass_index,
            "outcome": self.outcome.value,
            "candidate_ids": list(self.candidate_ids),
            "raw_output": self.raw_output,
            "message": self.message,
            "metadata": self.metadata,
        }

    def to_dict(self) -> dict[str, Any]:
        return {"attempt_id": self.attempt_id, **self._content_dict()}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExtractionAttempt":
        attempt = cls(
            request_id=data["request_id"],
            chunk_id=data["chunk_id"],
            chunk_index=data["chunk_index"],
            start_offset=data["start_offset"],
            end_offset=data["end_offset"],
            pass_index=data["pass_index"],
            outcome=ExtractionOutcome(data["outcome"]),
            candidate_ids=tuple(data.get("candidate_ids", ())),
            raw_output=data.get("raw_output"),
            message=data.get("message"),
            metadata_json=_json_text(data.get("metadata", {}), field_name="metadata"),
        )
        _verify_id(data, "attempt_id", attempt.attempt_id)
        return attempt


@dataclass(frozen=True)
class ConsensusDisposition:
    """Derived N-of-K decision; candidate records remain untouched."""

    consensus_key: str | None
    candidate_ids: tuple[str, ...]
    observed_passes: tuple[int, ...]
    votes: int
    required_votes: int
    status: ConsensusStatus
    reason: str

    @property
    def disposition_id(self) -> str:
        return _stable_id("disposition", self._content_dict())

    def _content_dict(self) -> dict[str, Any]:
        return {
            "consensus_key": self.consensus_key,
            "candidate_ids": list(self.candidate_ids),
            "observed_passes": list(self.observed_passes),
            "votes": self.votes,
            "required_votes": self.required_votes,
            "status": self.status.value,
            "reason": self.reason,
        }

    def to_dict(self) -> dict[str, Any]:
        return {"disposition_id": self.disposition_id, **self._content_dict()}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ConsensusDisposition":
        disposition = cls(
            consensus_key=data.get("consensus_key"),
            candidate_ids=tuple(data["candidate_ids"]),
            observed_passes=tuple(data["observed_passes"]),
            votes=data["votes"],
            required_votes=data["required_votes"],
            status=ConsensusStatus(data["status"]),
            reason=data["reason"],
        )
        _verify_id(data, "disposition_id", disposition.disposition_id)
        return disposition


@dataclass(frozen=True)
class CoverageRecord:
    """Per-chunk accounting across all configured extraction passes."""

    chunk_id: str
    chunk_index: int
    start_offset: int
    end_offset: int
    required_passes: int
    attempted_passes: tuple[int, ...]
    complete_passes: tuple[int, ...]
    candidate_ids: tuple[str, ...]

    @property
    def fully_covered(self) -> bool:
        return len(self.complete_passes) == self.required_passes

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "chunk_index": self.chunk_index,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "required_passes": self.required_passes,
            "attempted_passes": list(self.attempted_passes),
            "complete_passes": list(self.complete_passes),
            "candidate_ids": list(self.candidate_ids),
            "fully_covered": self.fully_covered,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CoverageRecord":
        record = cls(
            chunk_id=data["chunk_id"],
            chunk_index=data["chunk_index"],
            start_offset=data["start_offset"],
            end_offset=data["end_offset"],
            required_passes=data["required_passes"],
            attempted_passes=tuple(data["attempted_passes"]),
            complete_passes=tuple(data["complete_passes"]),
            candidate_ids=tuple(data.get("candidate_ids", ())),
        )
        if data.get("fully_covered") not in (None, record.fully_covered):
            raise ValueError("fully_covered mismatch")
        return record


@dataclass(frozen=True)
class GapRecord:
    """An explicit incomplete, malformed, skipped, or failed source window."""

    outcome: ExtractionOutcome
    start_offset: int
    end_offset: int
    reason: str
    chunk_id: str | None = None
    chunk_index: int | None = None
    pass_index: int | None = None
    attempt_id: str | None = None

    @property
    def gap_id(self) -> str:
        return _stable_id("gap", self._content_dict())

    def _content_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "reason": self.reason,
            "chunk_id": self.chunk_id,
            "chunk_index": self.chunk_index,
            "pass_index": self.pass_index,
            "attempt_id": self.attempt_id,
        }

    def to_dict(self) -> dict[str, Any]:
        return {"gap_id": self.gap_id, **self._content_dict()}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GapRecord":
        gap = cls(
            outcome=ExtractionOutcome(data["outcome"]),
            start_offset=data["start_offset"],
            end_offset=data["end_offset"],
            reason=data["reason"],
            chunk_id=data.get("chunk_id"),
            chunk_index=data.get("chunk_index"),
            pass_index=data.get("pass_index"),
            attempt_id=data.get("attempt_id"),
        )
        _verify_id(data, "gap_id", gap.gap_id)
        return gap


@dataclass(frozen=True)
class ExtractionRun:
    """Immutable extraction result and audit envelope."""

    recipe: ExtractionRecipe
    source_ref: str
    source_id: str
    source_hash: str
    source_length: int
    chunks: tuple[PlannedChunk, ...]
    attempts: tuple[ExtractionAttempt, ...]
    candidates: tuple[CandidateRecord, ...]
    dispositions: tuple[ConsensusDisposition, ...]
    coverage: tuple[CoverageRecord, ...]
    gaps: tuple[GapRecord, ...]
    status: RunStatus

    def __post_init__(self) -> None:
        for name in (
            "chunks",
            "attempts",
            "candidates",
            "dispositions",
            "coverage",
            "gaps",
        ):
            value = getattr(self, name)
            if not isinstance(value, tuple):
                object.__setattr__(self, name, tuple(value))
        if not isinstance(self.status, RunStatus):
            object.__setattr__(self, "status", RunStatus(self.status))
        self._validate_consistency()

    def _validate_consistency(self) -> None:
        if not isinstance(self.recipe, ExtractionRecipe):
            raise TypeError("recipe must be ExtractionRecipe")
        if not isinstance(self.source_ref, str) or not self.source_ref:
            raise ValueError("source_ref must be a non-empty string")
        if self.source_length < 0:
            raise ValueError("source_length must be >= 0")

        if [chunk.index for chunk in self.chunks] != list(range(len(self.chunks))):
            raise ValueError("chunk indexes must be contiguous from zero")
        if any(chunk.source_id != self.source_id for chunk in self.chunks):
            raise ValueError("every chunk must reference run source_id")

        reconstructed: list[str | None] = [None] * self.source_length
        for chunk in self.chunks:
            if chunk.end_offset > self.source_length:
                raise ValueError("chunk extends past source_length")
            for offset, character in enumerate(chunk.text, chunk.start_offset):
                current = reconstructed[offset]
                if current is not None and current != character:
                    raise ValueError("overlapping chunks disagree on source text")
                reconstructed[offset] = character
        if any(character is None for character in reconstructed):
            raise ValueError("chunk plan does not cover the complete source")
        source_text = "".join(character or "" for character in reconstructed)
        expected_source_id, expected_source_hash = _source_identity(
            self.source_ref, source_text
        )
        if self.source_hash != expected_source_hash:
            raise ValueError("source_hash does not match planned chunk text")
        if self.source_id != expected_source_id:
            raise ValueError("source_id does not match source_ref and content")
        expected_chunks = plan_chunks(
            source_text,
            source_ref=self.source_ref,
            target_chars=self.recipe.target_chars,
            overlap_chars=self.recipe.overlap_chars,
        )
        if self.chunks != expected_chunks:
            raise ValueError("chunks do not match the deterministic recipe plan")

        chunks_by_id = {chunk.chunk_id: chunk for chunk in self.chunks}
        if len(chunks_by_id) != len(self.chunks):
            raise ValueError("chunk IDs must be unique")
        attempts_by_request = {attempt.request_id: attempt for attempt in self.attempts}
        if len(attempts_by_request) != len(self.attempts):
            raise ValueError("request IDs must be unique")
        if len(self.attempts) != len(self.chunks) * self.recipe.passes:
            raise ValueError("attempts must cover every planned chunk and pass")
        for attempt in self.attempts:
            chunk = chunks_by_id.get(attempt.chunk_id)
            if chunk is None:
                raise ValueError("attempt references an unknown chunk")
            if not 1 <= attempt.pass_index <= self.recipe.passes:
                raise ValueError("attempt pass_index is outside the recipe")
            expected_request_id = ExtractionRequest(
                recipe_id=self.recipe.recipe_id,
                source_ref=self.source_ref,
                source_id=self.source_id,
                chunk=chunk,
                pass_index=attempt.pass_index,
            ).request_id
            if attempt.request_id != expected_request_id:
                raise ValueError("attempt request_id does not match its provenance")
            if (
                attempt.chunk_index,
                attempt.start_offset,
                attempt.end_offset,
            ) != (chunk.index, chunk.start_offset, chunk.end_offset):
                raise ValueError("attempt provenance does not match its chunk")

        candidates_by_id = {
            candidate.candidate_id: candidate for candidate in self.candidates
        }
        if len(candidates_by_id) != len(self.candidates):
            raise ValueError("candidate IDs must be unique")
        referenced_candidate_ids = tuple(
            candidate_id
            for attempt in self.attempts
            for candidate_id in attempt.candidate_ids
        )
        if referenced_candidate_ids != tuple(
            candidate.candidate_id for candidate in self.candidates
        ):
            raise ValueError("attempt candidate IDs must match candidates in run order")
        for candidate in self.candidates:
            attempt = attempts_by_request.get(candidate.request_id)
            if attempt is None:
                raise ValueError("candidate references an unknown request")
            if candidate.candidate_id not in attempt.candidate_ids:
                raise ValueError("candidate is not listed by its extraction attempt")
            if (
                candidate.source_ref,
                candidate.source_id,
                candidate.chunk_id,
                candidate.chunk_index,
                candidate.start_offset,
                candidate.end_offset,
                candidate.pass_index,
                candidate.outcome,
            ) != (
                self.source_ref,
                self.source_id,
                attempt.chunk_id,
                attempt.chunk_index,
                attempt.start_offset,
                attempt.end_offset,
                attempt.pass_index,
                attempt.outcome,
            ):
                raise ValueError("candidate provenance does not match its attempt")

        expected_dispositions = _derive_dispositions(
            self.candidates, self.recipe.consensus_threshold
        )
        if self.dispositions != expected_dispositions:
            raise ValueError("dispositions do not match candidate consensus")
        expected_coverage = _derive_coverage(
            self.chunks, self.attempts, self.recipe.passes
        )
        if self.coverage != expected_coverage:
            raise ValueError("coverage does not match chunks and attempts")
        if self.status != _derive_status(self.chunks, self.attempts, self.candidates):
            raise ValueError("status does not match extraction outcomes")

        if not self.chunks:
            expected_gaps = (
                GapRecord(
                    outcome=ExtractionOutcome.SKIPPED,
                    start_offset=0,
                    end_offset=0,
                    reason="source is empty; no chunks were planned",
                ),
            )
        else:
            expected_gaps = tuple(
                GapRecord(
                    outcome=attempt.outcome,
                    start_offset=attempt.start_offset,
                    end_offset=attempt.end_offset,
                    reason=attempt.message or f"{attempt.outcome.value} response",
                    chunk_id=attempt.chunk_id,
                    chunk_index=attempt.chunk_index,
                    pass_index=attempt.pass_index,
                    attempt_id=attempt.attempt_id,
                )
                for attempt in self.attempts
                if attempt.outcome != ExtractionOutcome.COMPLETE
            )
        if self.gaps != expected_gaps:
            raise ValueError("gaps do not match incomplete extraction attempts")

    @property
    def run_id(self) -> str:
        return _stable_id("run", self._content_dict())

    def _content_dict(self) -> dict[str, Any]:
        return {
            "recipe": self.recipe.to_dict(),
            "source_ref": self.source_ref,
            "source_id": self.source_id,
            "source_hash": self.source_hash,
            "source_length": self.source_length,
            "chunks": [item.to_dict() for item in self.chunks],
            "attempts": [item.to_dict() for item in self.attempts],
            "candidates": [item.to_dict() for item in self.candidates],
            "dispositions": [item.to_dict() for item in self.dispositions],
            "coverage": [item.to_dict() for item in self.coverage],
            "gaps": [item.to_dict() for item in self.gaps],
            "status": self.status.value,
        }

    def to_dict(self) -> dict[str, Any]:
        return {"run_id": self.run_id, **self._content_dict()}

    def to_json(self) -> str:
        """Serialize deterministically using fabric canonical JSON."""
        return _canonical_json(self.to_dict()).decode("utf-8")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExtractionRun":
        run = cls(
            recipe=ExtractionRecipe.from_dict(data["recipe"]),
            source_ref=data["source_ref"],
            source_id=data["source_id"],
            source_hash=data["source_hash"],
            source_length=data["source_length"],
            chunks=tuple(PlannedChunk.from_dict(item) for item in data["chunks"]),
            attempts=tuple(
                ExtractionAttempt.from_dict(item) for item in data["attempts"]
            ),
            candidates=tuple(
                CandidateRecord.from_dict(item) for item in data["candidates"]
            ),
            dispositions=tuple(
                ConsensusDisposition.from_dict(item)
                for item in data["dispositions"]
            ),
            coverage=tuple(
                CoverageRecord.from_dict(item) for item in data["coverage"]
            ),
            gaps=tuple(GapRecord.from_dict(item) for item in data["gaps"]),
            status=RunStatus(data["status"]),
        )
        _verify_id(data, "run_id", run.run_id)
        return run

    @classmethod
    def from_json(cls, raw: str) -> "ExtractionRun":
        return cls.from_dict(json.loads(raw))


def _response_or_failure(
    extractor: ShingledExtractor, request: ExtractionRequest
) -> ExtractionResponse:
    try:
        response = extractor(request)
    except Exception as exc:  # callback failures are evidence, not lost work
        return ExtractionResponse.create(
            outcome=ExtractionOutcome.ERROR,
            message=f"{type(exc).__module__}.{type(exc).__qualname__}: {exc}",
        )
    if not isinstance(response, ExtractionResponse):
        result_type = f"{type(response).__module__}.{type(response).__qualname__}"
        return ExtractionResponse.create(
            outcome=ExtractionOutcome.MALFORMED,
            message=f"extractor returned {result_type}; expected ExtractionResponse",
        )
    return response


def _derive_dispositions(
    candidates: tuple[CandidateRecord, ...], threshold: int
) -> tuple[ConsensusDisposition, ...]:
    groups: dict[str, list[CandidateRecord]] = {}
    unkeyed: list[CandidateRecord] = []
    for candidate in candidates:
        if candidate.consensus_key is None:
            unkeyed.append(candidate)
        else:
            groups.setdefault(candidate.consensus_key, []).append(candidate)

    dispositions: list[ConsensusDisposition] = []
    for key in sorted(groups):
        members = groups[key]
        voting_members = [
            item
            for item in members
            if item.outcome
            in (
                ExtractionOutcome.COMPLETE,
                ExtractionOutcome.TRUNCATED,
                ExtractionOutcome.MALFORMED,
            )
        ]
        observed_passes = tuple(sorted({item.pass_index for item in voting_members}))
        votes = len(observed_passes)
        accepted = votes >= threshold
        if not voting_members:
            status = ConsensusStatus.INELIGIBLE
            reason = "no candidates from an eligible extraction outcome"
        elif accepted:
            status = ConsensusStatus.ACCEPTED
            reason = "consensus threshold met"
        else:
            status = ConsensusStatus.REJECTED
            reason = "insufficient distinct-pass votes"
        dispositions.append(
            ConsensusDisposition(
                consensus_key=key,
                candidate_ids=tuple(item.candidate_id for item in members),
                observed_passes=observed_passes,
                votes=votes,
                required_votes=threshold,
                status=status,
                reason=reason,
            )
        )

    for candidate in unkeyed:
        dispositions.append(
            ConsensusDisposition(
                consensus_key=None,
                candidate_ids=(candidate.candidate_id,),
                observed_passes=(candidate.pass_index,),
                votes=1,
                required_votes=threshold,
                status=ConsensusStatus.INELIGIBLE,
                reason="candidate has no consensus_key",
            )
        )
    return tuple(dispositions)


def _derive_coverage(
    chunks: tuple[PlannedChunk, ...],
    attempts: tuple[ExtractionAttempt, ...],
    passes: int,
) -> tuple[CoverageRecord, ...]:
    by_chunk: dict[str, list[ExtractionAttempt]] = {}
    for attempt in attempts:
        by_chunk.setdefault(attempt.chunk_id, []).append(attempt)

    records = []
    for chunk in chunks:
        members = by_chunk.get(chunk.chunk_id, [])
        records.append(
            CoverageRecord(
                chunk_id=chunk.chunk_id,
                chunk_index=chunk.index,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
                required_passes=passes,
                attempted_passes=tuple(sorted(item.pass_index for item in members)),
                complete_passes=tuple(
                    sorted(
                        item.pass_index
                        for item in members
                        if item.outcome == ExtractionOutcome.COMPLETE
                    )
                ),
                candidate_ids=tuple(
                    candidate_id
                    for item in members
                    for candidate_id in item.candidate_ids
                ),
            )
        )
    return tuple(records)


def _derive_status(
    chunks: tuple[PlannedChunk, ...],
    attempts: tuple[ExtractionAttempt, ...],
    candidates: tuple[CandidateRecord, ...],
) -> RunStatus:
    if not chunks:
        return RunStatus.SKIPPED
    if attempts and all(
        item.outcome == ExtractionOutcome.COMPLETE for item in attempts
    ):
        return RunStatus.COMPLETE
    if any(item.outcome == ExtractionOutcome.COMPLETE for item in attempts):
        return RunStatus.PARTIAL
    if candidates or any(
        item.outcome == ExtractionOutcome.TRUNCATED for item in attempts
    ):
        return RunStatus.PARTIAL
    if attempts and all(
        item.outcome == ExtractionOutcome.SKIPPED for item in attempts
    ):
        return RunStatus.SKIPPED
    return RunStatus.FAILED


def run_shingled_extraction(
    *,
    source_ref: str,
    text: str,
    recipe: ExtractionRecipe,
    extractor: ShingledExtractor,
) -> ExtractionRun:
    """Run all planned chunk/pass calls and retain every result.

    Consensus is exact on the opaque ``consensus_key`` supplied with each
    candidate.  Repeated sightings within one pass count as one vote; sightings
    in distinct passes count toward the recipe's N-of-K threshold.  No
    candidate is removed, including rejected or ineligible candidates and
    candidates salvaged from incomplete responses.
    """
    if not isinstance(recipe, ExtractionRecipe):
        raise TypeError("recipe must be ExtractionRecipe")
    chunks = plan_chunks(
        text,
        source_ref=source_ref,
        target_chars=recipe.target_chars,
        overlap_chars=recipe.overlap_chars,
    )
    source_id, source_hash = _source_identity(source_ref, text)

    attempts: list[ExtractionAttempt] = []
    candidates: list[CandidateRecord] = []
    gaps: list[GapRecord] = []

    if not chunks:
        gaps.append(
            GapRecord(
                outcome=ExtractionOutcome.SKIPPED,
                start_offset=0,
                end_offset=0,
                reason="source is empty; no chunks were planned",
            )
        )

    for pass_index in range(1, recipe.passes + 1):
        for chunk in chunks:
            request = ExtractionRequest(
                recipe_id=recipe.recipe_id,
                source_ref=source_ref,
                source_id=source_id,
                chunk=chunk,
                pass_index=pass_index,
            )
            response = _response_or_failure(extractor, request)
            attempt_candidates: list[CandidateRecord] = []
            for ordinal, value in enumerate(response.candidates):
                record = CandidateRecord(
                    request_id=request.request_id,
                    source_ref=source_ref,
                    source_id=source_id,
                    chunk_id=chunk.chunk_id,
                    chunk_index=chunk.index,
                    start_offset=chunk.start_offset,
                    end_offset=chunk.end_offset,
                    pass_index=pass_index,
                    outcome=response.outcome,
                    ordinal=ordinal,
                    payload_json=value.payload_json,
                    consensus_key=value.consensus_key,
                    confidence=value.confidence,
                    metadata_json=value.metadata_json,
                )
                attempt_candidates.append(record)
                candidates.append(record)

            attempt = ExtractionAttempt(
                request_id=request.request_id,
                chunk_id=chunk.chunk_id,
                chunk_index=chunk.index,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
                pass_index=pass_index,
                outcome=response.outcome,
                candidate_ids=tuple(item.candidate_id for item in attempt_candidates),
                raw_output=response.raw_output,
                message=response.message,
                metadata_json=response.metadata_json,
            )
            attempts.append(attempt)
            if response.outcome != ExtractionOutcome.COMPLETE:
                gaps.append(
                    GapRecord(
                        outcome=response.outcome,
                        start_offset=chunk.start_offset,
                        end_offset=chunk.end_offset,
                        reason=response.message or f"{response.outcome.value} response",
                        chunk_id=chunk.chunk_id,
                        chunk_index=chunk.index,
                        pass_index=pass_index,
                        attempt_id=attempt.attempt_id,
                    )
                )

    candidate_tuple = tuple(candidates)
    attempt_tuple = tuple(attempts)
    disposition_tuple = _derive_dispositions(
        candidate_tuple, recipe.consensus_threshold
    )
    coverage_tuple = _derive_coverage(chunks, attempt_tuple, recipe.passes)

    return ExtractionRun(
        recipe=recipe,
        source_ref=source_ref,
        source_id=source_id,
        source_hash=source_hash,
        source_length=len(text),
        chunks=chunks,
        attempts=attempt_tuple,
        candidates=candidate_tuple,
        dispositions=disposition_tuple,
        coverage=coverage_tuple,
        gaps=tuple(gaps),
        status=_derive_status(chunks, attempt_tuple, candidate_tuple),
    )
