"""ESS accuracy harness — defends correctness claims about CanonicalRegistry resolution.

See ``docs/benchmarks/ess-accuracy-spec.md`` for the design and which
specific claims each metric defends.
"""

from benchmarks.eval.ess_accuracy.corpus import Corpus, load_corpus
from benchmarks.eval.ess_accuracy.metrics import AccuracyReport, score
from benchmarks.eval.ess_accuracy.mutations import (
    Mutation,
    MutationFamily,
    UNIVERSAL_FAMILIES,
)

__all__ = [
    "AccuracyReport",
    "Corpus",
    "Mutation",
    "MutationFamily",
    "UNIVERSAL_FAMILIES",
    "load_corpus",
    "score",
]
