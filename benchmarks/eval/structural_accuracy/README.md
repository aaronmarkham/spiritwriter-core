# structural_accuracy — canonicalization vs. similarity scoring

Measures what a similarity threshold can achieve on records whose
variation is **structural** rather than textual, and what
canonicalization achieves on the same pairs.

This is the companion to [`ess_accuracy/`](../ess_accuracy/), not a
replacement. That suite mutates fields — case, whitespace, typos,
unicode — and similarity scoring does well on much of it. This suite
mutates *shape*, and the result is different in kind.

## Running

```bash
python -m benchmarks.eval.structural_accuracy.runner --corpus subject_rings
python -m benchmarks.eval.structural_accuracy.runner --corpus all
```

Writes `report.md`, `results.json`, and `pairs.tsv` to a timestamped
directory under `results/`.

Corpora: `subject_rings` (undirected see-also loops), `pipelines`
(directed cycles where reflection is a *different* structure),
`coauthors` (unordered groupings with no principal member).

## What it does

Every pair carries known ground truth and is decided twice:

- **by rule** — compare canonical digests (`cycle_digest`,
  `orbit_digest`); equal means the same structure.
- **by score** — `fuzzy_score` over the recordings as written, merged
  when it clears a threshold.

The baseline is given every advantage. The reported best threshold is
chosen *after* seeing the answers, which no deployed system can do, and
it still loses.

## The negatives are the point

The hard negatives are structures over the **same members** in a
different arrangement — a ring with two headings swapped, not an
unrelated ring. They share every token with the original, so a text
score rates them highly while the rule correctly keeps them apart.

Negatives drawn from unrelated structures would make the baseline look
far better than it is, and would measure nothing. If you add a corpus,
keep the negatives hard.

## Result as of 0.10.1 (`--corpus all`, 200 pairs)

| | Caught (of 114 same-structure pairs) | Wrong merges (of 86) |
|---|---|---|
| Rule | 114 (100%) | 0 |
| Best threshold with no wrong merges (t=0.95) | 0 (0%) | 0 |
| Best threshold overall, chosen knowing the answers | 0 (0%) | 0 |

The classes are not separable by any threshold, and the reason is in the
per-relation means:

| Relation | Pairs | Settled by rule | Mean score |
|---|---|---|---|
| rotation | 26 | 26 | 0.690 |
| reflection | 19 | 19 | 0.546 |
| permutation | 69 | 69 | 0.610 |
| sibling *(must stay apart)* | 86 | 0 | **0.729** |

The pairs that must stay apart score **higher** than every class that
must merge. Similarity here is not a weak signal to be tuned — it is
anti-correlated with the truth, because rewriting a structure changes
the text a great deal while altering the structure changes it hardly at
all. No cutoff fixes a signal pointing the wrong way.

## Reading the rule's column honestly

The rule's 100% recall is true by construction: canonicalization is
exact, and anything below 100% would be a bug rather than a tuning
problem. The informative column is wrong merges — a rule that collapsed
distinct structures would also post perfect recall and be useless. Both
numbers only mean something together.

Do not quote this suite's numbers alongside `ess_accuracy`'s as if they
measured one thing. They are different problems, and the honest summary
is that textual drift mostly wants scoring and structural variance
cannot use it at all.
