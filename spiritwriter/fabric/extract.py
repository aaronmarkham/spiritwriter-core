"""Knowledge extraction from conversations → shard atoms.

Converts raw conversation text into structured ShardAtoms.
Uses the same ingestion philosophy as KB pipeline but optimized
for conversational decisions, preferences, and facts.

This replaces the regex-based extraction from the clawdboss article
with a pipeline that aligns with spiritwriter's KB atomization.
"""

from __future__ import annotations

import re
from typing import Sequence

from spiritwriter.fabric.shard import ShardAtom, AtomKind, DecayClass


# === Decision Patterns ===
# These detect explicit decision language in conversation

DECISION_PATTERNS = [
    # "We decided to use X because Y"
    re.compile(
        r"(?:we\s+)?(?:decided|chose|picked|went\s+with|selected|choosing)"
        r"\s+(?:to\s+)?(?:use\s+)?(.+?)"
        r"(?:\s+(?:because|since|for|due\s+to|over)\s+(.+?))?"
        r"\.?\s*$",
        re.IGNORECASE,
    ),
    # "X over Y because Z"
    re.compile(
        r"(?:use|using|chose|prefer|picked)\s+(.+?)"
        r"\s+(?:over|instead\s+of|rather\s+than)\s+(.+?)"
        r"(?:\s+(?:because|since|for|due\s+to)\s+(.+?))?"
        r"\.?\s*$",
        re.IGNORECASE,
    ),
]

CONVENTION_PATTERNS = [
    # "Always/never do X"
    re.compile(
        r"(?:always|never|must|should\s+always|should\s+never)\s+(.+?)\.?\s*$",
        re.IGNORECASE,
    ),
]

FACT_PATTERNS = [
    # "X's Y is Z"
    re.compile(
        r"(?:(\w+(?:\s+\w+)?)'s|[Mm]y)\s+(.+?)\s+(?:is|are|was)\s+(.+?)\.?\s*$",
    ),
    # "I prefer X"
    re.compile(
        r"[Ii]\s+(prefer|like|love|hate|want|need|use)\s+(.+?)\.?\s*$",
    ),
]


def extract_decisions(text: str) -> list[ShardAtom]:
    """Extract decision atoms from text."""
    atoms = []

    for pattern in DECISION_PATTERNS:
        m = pattern.search(text)
        if m:
            groups = m.groups()
            what = groups[0].strip() if groups[0] else None
            why = groups[1].strip() if len(groups) > 1 and groups[1] else "no rationale recorded"

            # Handle "X over Y because Z" pattern
            if len(groups) > 2 and groups[2]:
                what = f"{groups[0].strip()} over {groups[1].strip()}"
                why = groups[2].strip()

            if what:
                atoms.append(ShardAtom(
                    text=text.strip(),
                    kind=AtomKind.DECISION,
                    entity="decision",
                    key=what[:100],
                    value=why[:200] if why else None,
                ))
            break

    for pattern in CONVENTION_PATTERNS:
        m = pattern.search(text)
        if m:
            rule = m.group(1).strip()
            is_never = "never" in text.lower()
            atoms.append(ShardAtom(
                text=text.strip(),
                kind=AtomKind.CONVENTION,
                entity="convention",
                key=rule[:100],
                value="never" if is_never else "always",
            ))
            break

    return atoms


def extract_facts(text: str) -> list[ShardAtom]:
    """Extract structured fact atoms from text."""
    atoms = []

    for pattern in FACT_PATTERNS:
        m = pattern.search(text)
        if m:
            groups = m.groups()
            if len(groups) == 3:
                # "X's Y is Z" pattern
                entity = groups[0] or "user"
                key = groups[1].strip()
                value = groups[2].strip()
                atoms.append(ShardAtom(
                    text=text.strip(),
                    kind=AtomKind.FACT,
                    entity=entity,
                    key=key,
                    value=value,
                ))
            elif len(groups) == 2:
                # "I prefer X" pattern
                atoms.append(ShardAtom(
                    text=text.strip(),
                    kind=AtomKind.PREFERENCE,
                    entity="user",
                    key=groups[0].strip(),
                    value=groups[1].strip(),
                ))
            break

    return atoms


def classify_decay(atom: ShardAtom) -> DecayClass:
    """Classify the decay class of an atom based on its content.

    This is more nuanced than keyword matching — we use the
    structured fields when available.
    """
    # Decisions and conventions are always permanent
    if atom.kind in (AtomKind.DECISION, AtomKind.CONVENTION):
        return DecayClass.PERMANENT

    text_lower = atom.text.lower()
    key_lower = (atom.key or "").lower()

    # Permanent: identity facts, architectural decisions
    permanent_signals = [
        "name", "email", "birthday", "born", "phone",
        "api_endpoint", "architecture", "language", "location",
    ]
    if any(s in key_lower for s in permanent_signals):
        return DecayClass.PERMANENT

    # Session-level: transient context
    session_signals = [
        "currently debugging", "right now", "this session",
        "current_file", "working_on_right_now",
    ]
    if any(s in text_lower or s in key_lower for s in session_signals):
        return DecayClass.SESSION

    # Active: tasks and work-in-progress
    active_signals = [
        "working on", "need to", "todo", "blocker",
        "sprint", "task", "wip", "branch",
    ]
    if any(s in text_lower or s in key_lower for s in active_signals):
        return DecayClass.ACTIVE

    # Checkpoint
    if atom.kind == AtomKind.CHECKPOINT:
        return DecayClass.CHECKPOINT

    # Default: stable
    return DecayClass.STABLE


def extract_atoms(text: str) -> list[ShardAtom]:
    """Extract all possible atoms from a text string.

    Runs decision extraction first (higher priority),
    falls back to fact extraction.
    """
    atoms = extract_decisions(text)
    if not atoms:
        atoms = extract_facts(text)

    # Classify decay for all extracted atoms
    for atom in atoms:
        # Don't override if already set to something specific
        pass  # decay is classified at shard creation time

    return atoms


def extract_from_conversation(
    messages: Sequence[dict[str, str]],
    source_ref: str | None = None,
) -> list[ShardAtom]:
    """Extract atoms from a sequence of conversation messages.

    Args:
        messages: List of {"role": "user"|"assistant", "text": "..."}
        source_ref: Optional trace reference for provenance

    Returns:
        Deduplicated list of extracted atoms.
    """
    all_atoms: list[ShardAtom] = []
    seen_hashes: set[str] = set()

    for msg in messages:
        text = msg.get("text", "")
        if not text or len(text) < 10 or len(text) > 500:
            continue

        # Skip obvious non-extractable content
        if text.startswith("<") and "</" in text:
            continue
        if "**" in text and "\n-" in text:
            continue  # Likely formatted output, not extractable facts

        atoms = extract_atoms(text)
        for atom in atoms:
            h = atom.content_hash
            if h not in seen_hashes:
                seen_hashes.add(h)
                if source_ref:
                    atom.source_ref = source_ref
                all_atoms.append(atom)

    return all_atoms
