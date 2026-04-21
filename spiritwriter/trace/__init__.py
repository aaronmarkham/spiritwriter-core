"""Deprecated alias for :mod:`spiritwriter.fabric`.

The package was renamed in 0.5.0 because it outgrew its original scope
(provenance tracing). All storage, encryption, distribution, access
control, and provenance code now lives under ``spiritwriter.fabric``.

This shim re-exports the public API and will be removed in 0.6.0.
Update imports:

    from spiritwriter.trace import MemoryShard     # old
    from spiritwriter.fabric import MemoryShard    # new
"""

from __future__ import annotations

import importlib as _importlib
import sys as _sys
import warnings as _warnings

_warnings.warn(
    "spiritwriter.trace is deprecated and will be removed in 0.6.0; "
    "import from spiritwriter.fabric instead.",
    DeprecationWarning,
    stacklevel=2,
)

from spiritwriter.fabric import *  # noqa: F401,F403,E402
from spiritwriter.fabric import __all__  # noqa: E402

_SUBMODULES = (
    "backends",
    "backends.ipfs",
    "canonicalize",
    "crypto",
    "emitter",
    "entitlement",
    "extract",
    "network",
    "sealed",
    "shard",
    "store",
    "studio_job",
    "studio_runner",
    "visualize",
)

for _sub in _SUBMODULES:
    _sys.modules[f"spiritwriter.trace.{_sub}"] = _importlib.import_module(
        f"spiritwriter.fabric.{_sub}"
    )
