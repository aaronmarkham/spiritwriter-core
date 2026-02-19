"""Spiritwriter trace & memory shard system.

Content-addressed memory shards with DHT-ready distribution,
provenance tracking, and scoped entitlements.
"""

from spiritwriter.trace.shard import MemoryShard, ShardAtom, ShardRef
from spiritwriter.trace.store import ShardStore
from spiritwriter.trace.emitter import TraceEmitter

__all__ = [
    "MemoryShard",
    "ShardAtom",
    "ShardRef",
    "ShardStore",
    "TraceEmitter",
]
