"""Shared pytest configuration for the spiritwriter test suite.

The async tests in ``test_llm_anthropic.py`` need ``pytest-asyncio``, which
ships in the ``dev`` extra rather than the base install. Without it pytest
collects the coroutines, never awaits them, and reports 20 opaque failures
that look like real breakage in the LLM provider. Detect the missing plugin
once here and turn those into skips that name the fix.
"""

import pytest

try:  # pytest-asyncio lives in the `dev` extra
    import pytest_asyncio  # noqa: F401

    _ASYNCIO_PLUGIN = True
except ImportError:
    _ASYNCIO_PLUGIN = False


_MISSING_ASYNCIO = (
    "pytest-asyncio not installed — run: pip install -e \".[dev,sealed,network]\""
)


def pytest_collection_modifyitems(config, items):
    if _ASYNCIO_PLUGIN:
        return
    skip_async = pytest.mark.skip(reason=_MISSING_ASYNCIO)
    for item in items:
        if item.get_closest_marker("asyncio"):
            item.add_marker(skip_async)
