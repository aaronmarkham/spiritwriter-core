"""MemPalace integration for spiritwriter-core.

Bridges MemPalace's semantic retrieval with spiritwriter's trust layer
(content addressing, encryption, entity resolution, provenance).

Works regardless of install order:
- If only spiritwriter-core is installed: this module exists but
  MemPalaceProvider.is_available() returns False
- If only mempalace is installed: spiritwriter isn't imported
- If both are installed: full integration activates automatically

Install:
    pip install spiritwriter-core[mempalace]
    # or just: pip install spiritwriter-core mempalace

Three integration surfaces:
1. MemPalaceProvider — use MemPalace as a retrieval backend for spiritwriter
2. ShardBackend — use spiritwriter ShardStore as a storage backend for MemPalace
3. EntityBridge — use CMC-Lite entity resolution inside MemPalace
"""

from spiritwriter.integrations.mempalace.provider import MemPalaceProvider
from spiritwriter.integrations.mempalace.backend import ShardBackend
from spiritwriter.integrations.mempalace.entities import EntityBridge

__all__ = ["MemPalaceProvider", "ShardBackend", "EntityBridge"]
