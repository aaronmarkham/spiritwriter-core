"""Spiritwriter Core — shared foundation for AI content pipelines."""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("spiritwriter-core")
except PackageNotFoundError:
    # Running from source without `pip install -e .`
    __version__ = "0.0.0+unknown"
