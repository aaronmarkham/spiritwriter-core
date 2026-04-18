"""Spiritwriter integrations — pluggable memory system bridges.

Auto-discovers installed memory providers at import time.
Install order doesn't matter: if both spiritwriter-core and a
memory provider (MemPalace, Mem0, etc.) are installed, the
integration activates automatically.

Usage:
    from spiritwriter.integrations import available_providers, get_provider

    # See what's installed
    print(available_providers())  # {"mempalace": <MemPalaceProvider>, ...}

    # Get a specific provider
    mp = get_provider("mempalace")
"""

from __future__ import annotations

from typing import Any

from spiritwriter.integrations.base import (
    RetrievalProvider,
    StorageProvider,
    EntityProvider,
    ProviderCapability,
)

_providers: dict[str, RetrievalProvider] = {}
_discovered = False


def _discover() -> None:
    """Probe for installed memory providers. Safe to call multiple times.

    Checks whether the third-party package is importable (not just the
    spiritwriter integration module, which ships with spiritwriter-core).
    A provider is only registered if its backing package is installed.
    """
    global _discovered
    if _discovered:
        return
    _discovered = True

    # MemPalace — only register if the mempalace package is installed
    try:
        import mempalace  # noqa: F401 — probe only
        from spiritwriter.integrations.mempalace import MemPalaceProvider
        _providers["mempalace"] = MemPalaceProvider()
    except ImportError:
        pass

    # Future providers register here:
    # try:
    #     from spiritwriter.integrations.mem0 import Mem0Provider
    #     _providers["mem0"] = Mem0Provider()
    # except ImportError:
    #     pass


def available_providers() -> dict[str, RetrievalProvider]:
    """Return all discovered providers. Keys are provider names."""
    _discover()
    return dict(_providers)


def get_provider(name: str) -> RetrievalProvider | None:
    """Get a specific provider by name, or None if not installed."""
    _discover()
    return _providers.get(name)


def register_provider(name: str, provider: RetrievalProvider) -> None:
    """Manually register a provider (for custom / third-party integrations)."""
    _providers[name] = provider


__all__ = [
    "RetrievalProvider",
    "StorageProvider",
    "EntityProvider",
    "ProviderCapability",
    "available_providers",
    "get_provider",
    "register_provider",
]
