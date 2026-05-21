# ess_accuracy — Entity Sense Signature Resolution Accuracy

The harness that measures whether `spiritwriter.fabric.canonicalize`
(CMC-Lite) does what it claims to do: refuse to auto-merge entities
that aren't actually the same, and surface ambiguous cases for review
instead of silently dropping them.

## Where to start (by intent)

| If you want to... | Read |
|---|---|
| See what numbers we measured + cite | [`docs/benchmarks/runs-log.md`](../../../docs/benchmarks/runs-log.md) |
| Understand what the harness asserts as pass/fail | [`docs/benchmarks/ess-accuracy-spec.md`](../../../docs/benchmarks/ess-accuracy-spec.md) |
| Run the harness against the shipped example corpora | see "Running" below |
| Run the harness against your own corpus | see [`../README.md`](../README.md) "Bring-your-own-corpus" |
| Read the runner code | [`runner.py`](runner.py), [`metrics.py`](metrics.py), [`corpus.py`](corpus.py), [`mutations.py`](mutations.py) |

## What's in this directory

| Path | What it is |
|---|---|
| [`runner.py`](runner.py) | CLI entry — `python -m benchmarks.eval.ess_accuracy.runner --corpus <name>` |
| [`corpus.py`](corpus.py) | Loads a corpus's schema, entities, optional domain-specific mutations |
| [`mutations.py`](mutations.py) | Universal mutation families (case, whitespace, typo, unicode, negative_control, garbled_all_fields) applied to every corpus |
| [`baselines.py`](baselines.py) | Jaccard-on-tokens baseline for ESS-vs-baseline comparisons |
| [`metrics.py`](metrics.py) | Per-tier calibration, per-family attribution, pass/fail invariant computation, report rendering |
| [`csp_kb_trial.py`](csp_kb_trial.py) | Phase 2 — runs the harness against a `csp` `knowledge_graph.json` (real LLM-extracted entities, no synthetic mutations) |
| [`data/`](data/) | Per-corpus seed data — schema.json, entities.json, optional mutations.py, README.md |
| [`results/`](results/) | Pinned per-run artifacts (force-added past the gitignore for canonical runs; ephemeral runs gitignored) |

## Running

```bash
# Built-in example corpora
python -m benchmarks.eval.ess_accuracy.runner --corpus case_only
python -m benchmarks.eval.ess_accuracy.runner --corpus inmate_clean
python -m benchmarks.eval.ess_accuracy.runner --corpus people
python -m benchmarks.eval.ess_accuracy.runner --corpus publications

# Phase 2 — against a csp knowledge graph (real LLM-extracted entities)
python -m benchmarks.eval.ess_accuracy.csp_kb_trial \
    --kg /path/to/csp/artifacts/kb/kb_<id>/knowledge_graph.json
```

Each run produces a timestamped subdir under `results/` with `report.md`,
`results.json`, and `pairs.tsv` for inspection.

## Example corpora shipped here

| Corpus | What it tests | README |
|---|---|---|
| `case_only` | Engine's cleanest path — universal mutations on clean entities | [data/case_only/README.md](data/case_only/README.md) |
| `inmate_clean` | Realistic frio production drift, no stress-test modes | [data/inmate_clean/README.md](data/inmate_clean/README.md) |
| `people` | Kitchen-sink stress test — all drift modes including rare adversarial ones | [data/people/README.md](data/people/README.md) |
| `publications` | Different schema (`title, first_author_last, year`) — validates domain-agnostic claim | [data/publications/README.md](data/publications/README.md) |

## Tests

Unit tests live at [`tests/test_ess_accuracy.py`](../../../tests/test_ess_accuracy.py)
(23 tests as of campaign close). Run with `pytest tests/test_ess_accuracy.py -v`.
