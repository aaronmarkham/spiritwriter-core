# benchmarks/eval — Accuracy / Correctness Measurements

These are **correctness** benchmarks — recall, precision, false-merge
rate, calibration. Distinct from the perf benches in the parent
`benchmarks/` directory, which measure ops/sec and latency.

The audience is different too: perf benches inform "can this handle the
load?" decisions; eval suites produce artifacts that defend correctness
claims to peer reviewers.

## Where the docs are

| If you're asking... | Read |
|---|---|
| What numbers do we cite (per-corpus measurements, marketing claims) | [`docs/benchmarks/runs-log.md`](../../docs/benchmarks/runs-log.md) — the campaign report |
| How does the harness work? What invariants does it assert? | [`docs/benchmarks/ess-accuracy-spec.md`](../../docs/benchmarks/ess-accuracy-spec.md) — the design doc |
| How do I orient between everything in /docs/benchmarks/? | [`docs/benchmarks/README.md`](../../docs/benchmarks/README.md) |
| How do I add a new corpus? | continue reading this file |

This README covers *how to invoke* the suites and *how to add a corpus*.
The docs in `/docs/benchmarks/` cover *why the harness exists* and
*what we measured*.

## Suites

| Suite | Validates | Code | Design | Campaign report |
|---|---|---|---|---|
| `ess_accuracy/` | Entity Sense Signature resolution accuracy on a per-corpus basis | [ess_accuracy/](ess_accuracy/) | [docs/benchmarks/ess-accuracy-spec.md](../../docs/benchmarks/ess-accuracy-spec.md) | [docs/benchmarks/runs-log.md](../../docs/benchmarks/runs-log.md) |

## Running an eval suite

```bash
# Built-in example corpus
python -m benchmarks.eval.ess_accuracy.runner --corpus people

# Your own corpus (any directory with schema.json + entities.json [+ mutations.py])
python -m benchmarks.eval.ess_accuracy.runner --corpus /path/to/your-domain
```

Each run writes a timestamped directory under
`benchmarks/eval/<suite>/results/`:

```
report.md       — human-readable summary (the citable artifact)
results.json    — machine-readable summary (CI / trend tracking)
pairs.tsv       — per-pair detail, tab-separated (spreadsheet inspection)
```

## Bring-your-own-corpus (ess_accuracy)

1. Create `<your-corpus>/schema.json`:

   ```json
   {
     "name": "your_entity",
     "ess_fields": ["primary", "secondary"],
     "fuzzy_fields": {"primary": 0.85, "secondary": 0.80},
     "context_fields": ["category"]
   }
   ```

2. Create `<your-corpus>/entities.json` — a JSON array of canonical
   records matching the schema.

3. (Optional) Create `<your-corpus>/mutations.py` exporting a
   module-level `FAMILIES` list of `MutationFamily` objects for
   domain-specific drift modes. Universal families (case, whitespace,
   typo, unicode, negative control) are always applied — your file
   adds on top.

   **Security note.** `mutations.py` is imported and executed. For
   shipped corpora under `benchmarks/eval/ess_accuracy/data/` this is
   automatic. For corpora outside that tree, you must pass
   `--allow-untrusted-mutations` to opt in. Only enable for corpora
   you trust (your own, or content you've reviewed).

4. Run:
   ```bash
   python -m benchmarks.eval.ess_accuracy.runner --corpus /path/to/<your-corpus>
   ```

See [data/people/](ess_accuracy/data/people/) as a worked example
including a `README.md` documenting composition, known biases, and
how to extend.

## Why under `benchmarks/`?

Two angles on the same question (correctness, performance) live next
to each other so anyone looking for "how do we measure X?" finds both.
The `eval/` vs flat-`bench_*` split inside `benchmarks/` tells you
which kind of measurement you're looking at.
