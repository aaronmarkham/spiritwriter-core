"""Typed extension seams for source extraction and locator resolution."""

from __future__ import annotations

from typing import Protocol, Sequence, cast, runtime_checkable

from .models import EvidenceUnit, Locator, SourceSnapshot


@runtime_checkable
class SourceAdapter(Protocol):
    """Extract evidence units from bytes matching one source snapshot.

    Implementations must be deterministic for the same snapshot and bytes.
    They should verify the source bytes before extraction and return units in
    source order; ``EvidencePack`` applies canonical ID ordering on the wire.
    """

    @property
    def adapter_id(self) -> str:
        """Return a stable, versioned adapter identifier."""
        ...

    def extract(
        self, source: SourceSnapshot, content: bytes
    ) -> Sequence[EvidenceUnit]:
        """Return evidence units derived from the supplied source bytes."""
        ...


@runtime_checkable
class LocatorResolver(Protocol):
    """Resolve one supported locator against immutable source bytes."""

    @property
    def locator_scheme(self) -> str:
        """Return the supported locator scheme."""
        ...

    @property
    def locator_version(self) -> str:
        """Return the supported locator version."""
        ...

    def resolve(
        self,
        source: SourceSnapshot,
        locator: Locator,
        content: bytes,
    ) -> str:
        """Return the normalized excerpt addressed by ``locator``."""
        ...


class ProtocolValidationError(ValueError):
    """An adapter or resolver does not expose the required runtime surface."""


def require_source_adapter(candidate: object) -> SourceAdapter:
    """Validate the runtime members that structural protocol checks cannot."""

    adapter_id = getattr(candidate, "adapter_id", None)
    extract = getattr(candidate, "extract", None)
    if type(adapter_id) is not str or not adapter_id:
        raise ProtocolValidationError("SourceAdapter.adapter_id must be a non-empty string")
    if not callable(extract):
        raise ProtocolValidationError("SourceAdapter.extract must be callable")
    return cast(SourceAdapter, candidate)


def require_locator_resolver(candidate: object) -> LocatorResolver:
    """Validate the resolver's identifiers and callable runtime member."""

    scheme = getattr(candidate, "locator_scheme", None)
    version = getattr(candidate, "locator_version", None)
    resolve = getattr(candidate, "resolve", None)
    if type(scheme) is not str or not scheme:
        raise ProtocolValidationError(
            "LocatorResolver.locator_scheme must be a non-empty string"
        )
    if type(version) is not str or not version:
        raise ProtocolValidationError(
            "LocatorResolver.locator_version must be a non-empty string"
        )
    if not callable(resolve):
        raise ProtocolValidationError("LocatorResolver.resolve must be callable")
    return cast(LocatorResolver, candidate)
