---
name: sw-vocab
description: Validate spiritwriter's own terminology in docs and AI-generated drafts against a canonical registry. Catches drift ("Entity Sense Signature" → "Entity Semantic Scoring"), invented terms ("SW-CAP"), and deferred-but-claimed terms ("trust epochs") before publication.
---

<!-- vocab: allow-all -->

# Skill: Terminology Validation

Run a terminology audit of any markdown/text document against
spiritwriter's canonical vocabulary registry. Produces a list of
issues for review.

This skill dogfoods `spiritwriter.fabric.canonicalize` — the same
engine that powers Phalanx (entity resolution) and the audit findings
registry — applied to our own vocabulary so AI-generated drafts can't
silently drift terms past peer review.

## When to Use

- Reviewing a draft doc, blog post, or spec written by an AI agent
- Pre-commit hook on `docs/` or `README.md`
- Auditing a CFP submission or paper draft for accidental hallucinations
- Whenever you suspect terminology has drifted between conversations

## Install

```bash
pip install -e /path/to/spiritwriter-core
python -m spiritwriter.sw_vocab.seed      # one-time: populate canonical terms DB
```

## Concepts

| Concept | What it is |
|---|---|
| **Canonical term** | The authoritative spelling of a spiritwriter primitive/module/concept (e.g. "Entity Sense Signature", "CanonicalRegistry", "Phalanx"). |
| **Alias** | Known drift form of a canonical term. "shingles" → "Phalanx", "Entity Semantic Scoring" → "Entity Sense Signature". |
| **Invented term** | Phrase hallucinated by an LLM that has no referent in code or docs (e.g. "SW-CAP", "capability shards", "dual-key sealed-box"). Listed in the registry with category `invented` so the validator flags any future appearance. |
| **Deferred term** | Feature documented as future work but NOT implemented (e.g. "trust epochs", "revocation sets"). Listed so claims of implementation get caught. |

## Issue Types

| Issue | Meaning | Action |
|---|---|---|
| `known_drift` | Term matches a recorded alias of a canonical | Rename to canonical |
| `fuzzy_drift` | Term is a near-miss to a canonical (T2/T3 fuzzy match) | Likely rename; if genuinely different, add as canonical or alias |
| `invented_term` | Term is on the invented blacklist | Remove or rewrite — does not exist |
| `deferred_term` | Term is on the deferred blacklist | Either implement or remove the claim |
| `unknown_term` | Not in registry | New legitimate term → seed it; typo → fix; noise → leave |

## Workflow

### Step 1 — Validate a doc

```python
from spiritwriter.sw_vocab import load_registry, validate_doc

registry = load_registry()
issues = validate_doc("docs/some-draft.md", registry)
for i in issues:
    print(i)
```

Or, from a string:

```python
from spiritwriter.sw_vocab import load_registry, validate_text

issues = validate_text(open("draft.md").read(), load_registry())
```

### Step 2 — Validate a single candidate

When debugging or building a custom integration:

```python
from spiritwriter.sw_vocab import load_registry, validate_candidate

issue = validate_candidate("Entity Semantic Scoring", load_registry())
# {'term': 'Entity Semantic Scoring', 'issue': 'known_drift',
#  'canonical': 'Entity Sense Signature', 'note': '...'}
```

### Step 3 — Seed a new canonical term

Edit `spiritwriter/sw_vocab/data/canonical_terms.json` and re-seed:

```json
{
  "term": "MyNewPrimitive",
  "category": "primitive",
  "definition": "Brief plain-English explanation.",
  "defined_in": "spiritwriter/path/to/file.py",
  "aliases": ["my new primitive", "MyNewPrim"]
}
```

```bash
python -m spiritwriter.sw_vocab.seed
```

Idempotent — re-seeding doesn't duplicate existing terms.

### Step 4 — Generate the prompt-ready term list

When prompting an AI agent to write spiritwriter docs, paste the
canonical list into the system prompt so the agent uses approved
terminology from the start:

```python
from spiritwriter.sw_vocab import load_registry, canonical_term_list

print(canonical_term_list(load_registry()))
```

Invented and deferred terms appear first in the listing, so the agent
sees what NOT to write before what TO write.

## What Gets Scanned

The validator scans text in two passes:

1. **Bolded and inline-code candidates**: anything matching `**Foo**` or
   `` `Foo` `` is treated as an explicit terminology marker. These are
   validated against the canonical registry.
2. **Substring scan for invented + deferred terms**: anywhere the
   blacklisted phrases appear in prose (case-insensitive, word-boundary
   matched), they get flagged. This catches drift in unmarked text.

Free-form prose that doesn't bold or code-format terms is otherwise
NOT scanned — that produces too much noise. If you want every
mention of a canonical term validated, mark it with `**` or `` ` ``
in your writing.

## Categories in the Seed Data

| Category | Examples |
|---|---|
| `primitive` | EntitySenseSig, MemoryShard, capability, EncryptedShard |
| `module` | CanonicalRegistry, TraceEmitter |
| `system` | Phalanx, CMC-Lite, Spiritwriter Substrate |
| `function` | verify_chain |
| `field` | decay_class |
| `tool` | rz-bin |
| `invented` | SW-CAP, capability shards, OpenTelemetry span attributes |
| `deferred` | trust epochs, revocation sets, shards.spiritwriter.ai protocol |

## Limits

- **Plain-text only.** PDF/DOCX/HTML need to be converted first.
- **Markdown-aware, not Markdown-parsing.** Code blocks are NOT
  excluded — code samples may flag false positives. Wrap them
  appropriately or use the markdown linter approach (extract code
  blocks first, validate the rest).
- **No semantic understanding.** "Entity Sense Signature" used
  correctly in prose and "Entity Sense Signature" used incorrectly
  both pass the validator. The validator catches drift in *spelling*,
  not in *meaning*.
- **Not a substitute for human review.** Final say is yours. The
  validator surfaces issues; it doesn't decide them.

## Wire It to CI

```yaml
# .github/workflows/docs-vocab.yml
- name: Validate docs vocabulary
  run: |
    python -m spiritwriter.sw_vocab.seed
    python -c "
    from spiritwriter.sw_vocab import load_registry, validate_doc
    from pathlib import Path
    reg = load_registry()
    failed = False
    for doc in Path('docs').rglob('*.md'):
        issues = validate_doc(doc, reg)
        invented = [i for i in issues if i['issue'] in ('invented_term', 'deferred_term')]
        if invented:
            print(f'{doc}: {len(invented)} invented/deferred term(s)')
            for i in invented:
                print(f'  {i}')
            failed = True
    exit(1 if failed else 0)
    "
```

Failing on `invented_term` and `deferred_term` is the conservative
gate. Failing on `fuzzy_drift` / `known_drift` / `unknown_term`
produces more noise — keep those as warnings until the registry is
fully seeded with project-specific terminology.

## Do not

- Seed invented or deferred terms without an explanation in the
  `definition` field — future readers need to know *why* a term is
  blacklisted.
- Add aliases that collide with another term's canonical name (the
  schema test will catch this).
- Treat this as a security boundary. It's a hygiene tool, not a gate.
