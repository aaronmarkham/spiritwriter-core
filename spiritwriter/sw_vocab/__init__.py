"""Spiritwriter terminology canonicalization — dogfooding CanonicalRegistry.

Catches terminology drift in AI-generated docs ("Entity Sense Signature"
→ "Entity Semantic Scoring") before publication. Same engine as
spiritwriter.audit, applied to our own vocabulary.

Typical flow::

    from spiritwriter.sw_vocab import load_registry, validate_doc

    registry = load_registry()
    issues = validate_doc("docs/some-ai-draft.md", registry)
    for issue in issues:
        print(issue)

See ``skills/sw-vocab/SKILL.md`` for workflow.
"""

from spiritwriter.sw_vocab.registry import (
    VOCAB_TERM_SCHEMA,
    canonical_term_list,
    extract_candidates,
    load_registry,
    validate_candidate,
    validate_doc,
    validate_text,
)

__all__ = [
    "VOCAB_TERM_SCHEMA",
    "canonical_term_list",
    "extract_candidates",
    "load_registry",
    "validate_candidate",
    "validate_doc",
    "validate_text",
]
