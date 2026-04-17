"""CMC-Lite entity resolution bridge for MemPalace.

MemPalace's entity_registry.py does regex-based name disambiguation
("is 'Max' a person or a common word?"). CMC-Lite adds tiered
confidence resolution across conversations:

- T1 (exact): Same name + context → same entity (0.95 confidence)
- T2 (strong): Fuzzy name match + overlap → merge (0.85)
- T3 (fuzzy): Partial match → flag for review (0.70)
- T4 (weak): Context only → new entity (0.50)

Usage:
    from spiritwriter.integrations.mempalace import EntityBridge

    bridge = EntityBridge("~/.mempalace/entities.db")

    # Resolve a person across conversations
    result = bridge.resolve_person("Max", context={
        "wing": "wing_alice", "room": "swimming",
        "relationship": "son",
    })
    print(result.canonical_id)   # stable ID across sessions
    print(result.tier)           # T1_EXACT, T2_STRONG, etc.
    print(result.confidence)     # 0.95

    # Same person, different conversation context
    result2 = bridge.resolve_person("Max", context={
        "wing": "wing_alice", "room": "chess",
        "relationship": "son",
    })
    assert result2.canonical_id == result.canonical_id  # linked!
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from spiritwriter.trace.canonicalize import (
    CanonicalRegistry,
    CanonicalSchema,
    ResolutionResult,
    ResolutionTier,
    normalize_name,
    fuzzy_score,
)

logger = logging.getLogger(__name__)

# Default schema for conversational entity resolution
PERSON_SCHEMA = CanonicalSchema(
    name="person",
    ess_fields=["name", "relationship"],
    fuzzy_fields={"name": 0.85},
    context_fields=["wing", "room", "role"],
    metadata_fields=["first_mention", "mention_count"],
)

PROJECT_SCHEMA = CanonicalSchema(
    name="project",
    ess_fields=["name"],
    fuzzy_fields={"name": 0.90},
    context_fields=["wing", "domain"],
    metadata_fields=["first_mention", "mention_count"],
)


class EntityBridge:
    """Bridges MemPalace entity detection with CMC-Lite resolution.

    MemPalace detects entities in text (regex patterns). This bridge
    resolves them into canonical entities with stable IDs, so "Max"
    in session 1 and "Max" in session 47 are the same person.
    """

    def __init__(
        self,
        db_path: str | Path = "~/.mempalace/entities.db",
        person_schema: CanonicalSchema | None = None,
        project_schema: CanonicalSchema | None = None,
    ):
        """Initialize entity registries.

        Args:
            db_path: Base path for SQLite databases. Two databases are
                created using the stem as a prefix:
                  db_path="~/.mempalace/entities.db"
                  -> ~/.mempalace/entities_persons.db
                  -> ~/.mempalace/entities_projects.db
            person_schema: Custom CanonicalSchema for person resolution.
            project_schema: Custom CanonicalSchema for project resolution.
        """
        db_path = Path(db_path).expanduser()

        self._person_registry = CanonicalRegistry(
            db_path.parent / f"{db_path.stem}_persons.db",
            person_schema or PERSON_SCHEMA,
        )
        self._project_registry = CanonicalRegistry(
            db_path.parent / f"{db_path.stem}_projects.db",
            project_schema or PROJECT_SCHEMA,
        )

    def resolve_person(
        self,
        name: str,
        context: dict[str, Any] | None = None,
        source_name: str = "mempalace",
        source_id: str | None = None,
        persist: bool = True,
    ) -> ResolutionResult:
        """Resolve a person name to a canonical entity.

        Args:
            name: The detected name (e.g., "Max", "Riley").
            context: Optional context fields (wing, room, relationship, role).
            source_name: Source of detection (default "mempalace").
            source_id: Unique ID for this sighting (e.g., drawer_id).
            persist: If True, upsert the resolution into the registry.

        Returns:
            ResolutionResult with tier, confidence, and canonical_id.
        """
        ctx = context or {}
        candidate = {
            "name": normalize_name(name),
            "relationship": ctx.get("relationship", ""),
            "wing": ctx.get("wing", ""),
            "room": ctx.get("room", ""),
            "role": ctx.get("role", ""),
        }

        result = self._person_registry.resolve(candidate)

        if persist:
            import uuid
            sid = source_id or uuid.uuid4().hex
            self._person_registry.upsert(
                candidate, result,
                source_name=source_name,
                source_id=sid,
            )

        return result

    def resolve_project(
        self,
        name: str,
        context: dict[str, Any] | None = None,
        source_name: str = "mempalace",
        source_id: str | None = None,
        persist: bool = True,
    ) -> ResolutionResult:
        """Resolve a project/organization name to a canonical entity."""
        ctx = context or {}
        candidate = {
            "name": normalize_name(name),
            "wing": ctx.get("wing", ""),
            "domain": ctx.get("domain", ""),
        }

        result = self._project_registry.resolve(candidate)

        if persist:
            import uuid
            sid = source_id or uuid.uuid4().hex
            self._project_registry.upsert(
                candidate, result,
                source_name=source_name,
                source_id=sid,
            )

        return result

    def resolve_from_mempalace_registry(
        self,
        mp_entity: dict[str, Any],
    ) -> ResolutionResult:
        """Bridge from MemPalace's entity_registry format to CMC-Lite.

        MemPalace entity_registry returns dicts like:
            {"type": "person", "confidence": 1.0, "source": "onboarding",
             "name": "Max", "relationship": "son"}

        This converts to a CMC-Lite resolution.
        """
        entity_type = mp_entity.get("type", "person")
        name = mp_entity.get("name", "")

        if entity_type == "person":
            return self.resolve_person(name, context=mp_entity)
        elif entity_type in ("project", "organization"):
            return self.resolve_project(name, context=mp_entity)
        else:
            # Default to person resolution
            return self.resolve_person(name, context=mp_entity)

    def get_canonical_entity(self, canonical_id: str) -> dict[str, Any] | None:
        """Get a canonical entity by ID. Checks both registries."""
        entity = self._person_registry.get_entity(canonical_id)
        if entity:
            entity["entity_type"] = "person"
            return entity

        entity = self._project_registry.get_entity(canonical_id)
        if entity:
            entity["entity_type"] = "project"
            return entity

        return None

    def find_similar(
        self, name: str, entity_type: str = "person", limit: int = 5
    ) -> list[ResolutionResult]:
        """Fuzzy search for similar entities."""
        registry = (
            self._person_registry if entity_type == "person"
            else self._project_registry
        )
        return registry.find_fuzzy({"name": normalize_name(name)}, limit=limit)

    def stats(self) -> dict[str, Any]:
        """Combined stats from both registries."""
        return {
            "persons": self._person_registry.stats(),
            "projects": self._project_registry.stats(),
        }

    def close(self) -> None:
        self._person_registry.close()
        self._project_registry.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
