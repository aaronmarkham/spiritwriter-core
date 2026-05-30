# `examples/atoms/` — Worked examples for the ShardAtom primitive

Short, runnable Python modules — one use case per file — that show what
an atom looks like in different shapes and what fields you'd fill in
for each. The companion explainer is [`docs/atoms.md`](../../docs/atoms.md);
the spec that scopes which use cases get covered is at
[`docs/cleanup/atom-examples.md`](../../docs/cleanup/atom-examples.md).

Each script is self-contained: builds the atom(s), puts them in a
temp `ShardStore`, prints what got stored + the hydrated context.
Run any one with `python examples/atoms/<file>.py` (no flags, no
setup beyond `pip install -e .`).

| # | File | AtomKind(s) | What it demonstrates |
|---|---|---|---|
| 01 | [`01_fact.py`](01_fact.py) | FACT | Knowledge ingestion — atomize one sentence into multiple FACT atoms about an entity |
| 02 | [`02_decision.py`](02_decision.py) | DECISION | Decision + rationale capture |
| 03 | [`03_preference.py`](03_preference.py) | PREFERENCE | Structured user config |
| 04 | [`04_convention.py`](04_convention.py) | CONVENTION | Behavioral rule with no entity/key/value |
| 05 | [`05_context.py`](05_context.py) | CONTEXT | Prompt-engineering / free-form context |
| 06 | [`06_checkpoint.py`](06_checkpoint.py) | CHECKPOINT | Pipeline resume-point (base) |
| 07 | [`07_checkpoint_with_trace.py`](07_checkpoint_with_trace.py) | CHECKPOINT + trace | Closing the loop — checkpoint pinned to a hash-chained trace event |
| 08 | [`08_instruction.py`](08_instruction.py) | INSTRUCTION | Sub-agent instruction (base) |
| 09 | [`09_instruction_delegation.py`](09_instruction_delegation.py) | INSTRUCTION + jobs + traces | Closing the loop — full package/hydrate/settle delegation with cap chain and trace events |
| 10 | [`10_entity.py`](10_entity.py) | ENTITY | Canonical entity record for resolution |
| 11 | [`11_mixed_kind.py`](11_mixed_kind.py) | FACT + DECISION + CONVENTION + CONTEXT | Real-world mixed-kind shard composition |
| 12 | [`12_minimal.py`](12_minimal.py) | (default CONTEXT) | The absolute-minimum atom — just `text` |
| 13 | [`13_lineage_variants.py`](13_lineage_variants.py) | FACT + ENTITY | Parent atom with bias-rewritten child variants (zeitghost pattern) |

## What's flexible vs not

See [`docs/atoms.md`](../../docs/atoms.md) for the explainer. Short
version:

- **Flexible**: most ShardAtom fields are optional (only `text` is
  required); kinds can mix freely in one shard; scope is a free-form
  string; you can have 1 atom or 100.
- **Not flexible**: the AtomKind enum is closed (8 values); the
  dataclass shape is fixed (changing fields breaks content-addressing);
  atoms within a shard don't FK to each other (relationships happen
  via shared entity strings or shard-level `parent_shard_id`).

## Regression coverage

Each example has a corresponding test at
[`tests/test_atom_examples.py`](../../tests/test_atom_examples.py)
that imports the script, runs its `build()` (or `main()`) function,
and verifies the atom(s) parse, hash deterministically, and round-trip
through `ShardStore`. The tests exist so the docs don't drift from the
runtime — if the API changes underneath, the tests fail and the docs
get updated.

## Running

```bash
pip install -e .

# Run a specific example
python examples/atoms/01_fact.py

# Run them all (no flags needed)
for f in examples/atoms/*.py; do echo "=== $f ==="; python "$f"; done

# Test the examples
pytest tests/test_atom_examples.py -v
```
