"""Typed extension seams for source extraction and locator resolution."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Protocol, Sequence, cast, runtime_checkable

from .canonical import MAX_SAFE_INTEGER, CanonicalJsonError, validate_json_value
from .models import EvidenceUnit, Locator, SourceSnapshot


@runtime_checkable
class SourceAdapter(Protocol):
    """Extract evidence units from bytes matching one source snapshot.

    Implementations must be deterministic for the same snapshot and bytes.
    They should verify the source bytes before extraction and return units in
    source order; ``EvidencePack`` applies canonical ID ordering on the wire.
    Every returned unit must carry this adapter's ``adapter_id`` and
    ``normalization_profile`` so extraction semantics participate in identity.
    """

    @property
    def adapter_id(self) -> str:
        """Return a stable, versioned adapter identifier."""
        ...

    @property
    def normalization_profile(self) -> str:
        """Return the stable identifier for excerpt normalization rules."""
        ...

    def extract(
        self, source: SourceSnapshot, content: bytes
    ) -> Sequence[EvidenceUnit]:
        """Return evidence units derived from the supplied source bytes."""
        ...


@dataclass(frozen=True)
class ResolvedSpan:
    """Half-open byte range obtained by resolving one source-native locator."""

    start_byte: int
    end_byte: int

    def __post_init__(self) -> None:
        for name, value in (
            ("start_byte", self.start_byte),
            ("end_byte", self.end_byte),
        ):
            if type(value) is not int or value < 0 or value > MAX_SAFE_INTEGER:
                raise ProtocolValidationError(
                    f"ResolvedSpan.{name} must be an integer from 0 through "
                    f"{MAX_SAFE_INTEGER}"
                )
        if self.end_byte < self.start_byte:
            raise ProtocolValidationError(
                "ResolvedSpan.end_byte must not precede start_byte"
            )


@runtime_checkable
class LocatorResolver(Protocol):
    """Resolve one supported locator to a range in immutable source bytes."""

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
    ) -> ResolvedSpan:
        """Resolve ``locator`` to a half-open range in ``content``."""
        ...


@runtime_checkable
class NormalizationProfile(Protocol):
    """Apply one stable normalization profile to a resolved raw span."""

    @property
    def normalization_profile(self) -> str:
        """Return the stable normalization profile identifier."""
        ...

    def normalize(self, raw_span: bytes) -> str:
        """Return deterministic normalized text for ``raw_span``."""
        ...


class ProtocolValidationError(ValueError):
    """An adapter, resolver, or normalizer violates its runtime contract."""


def _read_member(owner: str, member: str, candidate: object) -> object:
    try:
        return getattr(candidate, member, None)
    except Exception as error:
        raise ProtocolValidationError(
            f"{owner}.{member} could not be read: {type(error).__name__}: {error}"
        ) from error


def _require_identifier(owner: str, member: str, value: object) -> str:
    if type(value) is not str or not value:
        raise ProtocolValidationError(f"{owner}.{member} must be a non-empty string")
    try:
        validate_json_value(value, path=f"{owner}.{member}")
    except CanonicalJsonError as error:
        raise ProtocolValidationError(str(error)) from error
    return value


def _require_method_signature(
    owner: str,
    member: str,
    candidate: object,
    parameter_names: tuple[str, ...],
) -> None:
    if not callable(candidate):
        raise ProtocolValidationError(f"{owner}.{member} must be callable")
    if inspect.iscoroutinefunction(candidate) or inspect.isasyncgenfunction(candidate):
        raise ProtocolValidationError(f"{owner}.{member} must be synchronous")
    try:
        signature = inspect.signature(candidate)
    except Exception as error:
        raise ProtocolValidationError(
            f"{owner}.{member} must expose an inspectable signature"
        ) from error
    parameters = tuple(signature.parameters.values())
    if (
        tuple(parameter.name for parameter in parameters) != parameter_names
        or any(
            parameter.kind is not inspect.Parameter.POSITIONAL_OR_KEYWORD
            or parameter.default is not inspect.Parameter.empty
            for parameter in parameters
        )
    ):
        expected = ", ".join(parameter_names)
        raise ProtocolValidationError(
            f"{owner}.{member} must have exact bound signature ({expected})"
        )


@dataclass(frozen=True)
class _LocatorResolverBinding:
    resolver: LocatorResolver
    locator_scheme: str
    locator_version: str


@dataclass(frozen=True)
class _NormalizationBinding:
    normalizer: NormalizationProfile
    normalization_profile: str


def _bind_locator_resolver(candidate: object) -> _LocatorResolverBinding:
    """Validate and snapshot resolver metadata exactly once."""

    scheme = _require_identifier(
        "LocatorResolver",
        "locator_scheme",
        _read_member("LocatorResolver", "locator_scheme", candidate),
    )
    version = _require_identifier(
        "LocatorResolver",
        "locator_version",
        _read_member("LocatorResolver", "locator_version", candidate),
    )
    _require_method_signature(
        "LocatorResolver",
        "resolve",
        _read_member("LocatorResolver", "resolve", candidate),
        ("source", "locator", "content"),
    )
    return _LocatorResolverBinding(
        resolver=cast(LocatorResolver, candidate),
        locator_scheme=scheme,
        locator_version=version,
    )


def _bind_normalizer(candidate: object) -> _NormalizationBinding:
    """Validate and snapshot normalization metadata exactly once."""

    profile = _require_identifier(
        "NormalizationProfile",
        "normalization_profile",
        _read_member("NormalizationProfile", "normalization_profile", candidate),
    )
    _require_method_signature(
        "NormalizationProfile",
        "normalize",
        _read_member("NormalizationProfile", "normalize", candidate),
        ("raw_span",),
    )
    return _NormalizationBinding(
        normalizer=cast(NormalizationProfile, candidate),
        normalization_profile=profile,
    )


def require_source_adapter(candidate: object) -> SourceAdapter:
    """Validate a source adapter's identifiers and exact bound signature."""

    _require_identifier(
        "SourceAdapter",
        "adapter_id",
        _read_member("SourceAdapter", "adapter_id", candidate),
    )
    _require_identifier(
        "SourceAdapter",
        "normalization_profile",
        _read_member("SourceAdapter", "normalization_profile", candidate),
    )
    _require_method_signature(
        "SourceAdapter",
        "extract",
        _read_member("SourceAdapter", "extract", candidate),
        ("source", "content"),
    )
    return cast(SourceAdapter, candidate)


def require_locator_resolver(candidate: object) -> LocatorResolver:
    """Validate a locator resolver's identifiers and exact bound signature."""

    return _bind_locator_resolver(candidate).resolver


def require_normalization_profile(candidate: object) -> NormalizationProfile:
    """Validate a normalization profile's ID and exact bound signature."""

    return _bind_normalizer(candidate).normalizer
