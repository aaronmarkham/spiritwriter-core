# benchmarks/eval — Accuracy / Correctness Measurements

These are **correctness** benchmarks — recall, precision, false-merge
rate, calibration. Distinct from the perf benches in the parent
`benchmarks/` directory, which measure ops/sec and latency.

The audience is different too: perf benches inform "can this handle the
load?" decisions; eval suites produce artifacts that defend correctness
claims to peer reviewers.

## Suites

| Suite | Validates | Doc |
|---|---|---|
| `ess_accuracy/` | Entity Sense Signature resolution accuracy on a per-corpus basis | [docs/benchmarks/ess-accuracy-spec.md](../../docs/benchmarks/ess-accuracy-spec.md) |

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
pairs.csv       — per-pair detail (spreadsheet inspection)
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
