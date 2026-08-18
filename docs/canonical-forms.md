# Canonical forms under declared symmetry

Two records can describe the same structure and differ only by something
the structure allows: a ring of see-also references read from a
different starting point, an edge list walked backwards, a set of
co-equal authors listed in another order. Comparing those by similarity
is a threshold decision. Comparing their canonical forms is a byte
comparison.

`spiritwriter.fabric.orbit` supplies the exact half of that problem.
Given a structure and the symmetry it is allowed to have, it produces
the unique lexicographically-minimal representative of its equivalence
class, plus a content-addressed digest of that representative.

This page covers when to reach for it, when not to, and what it measures
at. For the function signatures see
[api-reference.md](api-reference.md#spiritwriterfabricorbit).

## The problem it solves

Consider a loop of five subject headings, each pointing to the next and
the last back to the first. Nothing in the loop says where to begin or
which way to read, so two catalogues can record the same loop and
produce different strings — ten of them, for five headings.

```python
from spiritwriter.fabric import cycle_digest

a = ["Cartography", "Map projections", "Geodesy", "Surveying", "Topographic mapping"]
b = ["Geodesy", "Surveying", "Topographic mapping", "Cartography", "Map projections"]

cycle_digest(a) == cycle_digest(b)      # True — same loop, different starting point
```

The digest is computed, not assigned. Two systems that have never
communicated arrive at the same value because there is only one value to
arrive at — no registry to consult, no identifier service, no prior
agreement beyond the operations themselves.

## Choosing the symmetry

The caller declares what "the same" means. That choice is the whole
interface, and getting it wrong is the only way to get a wrong answer.

| Structure | Symmetry | Call |
|---|---|---|
| Undirected ring — traversal direction is an artifact | rotation + reflection | `cycle_digest(members)` |
| Directed cycle — direction carries meaning | rotation only | `cycle_digest(members, reflect=False)` |
| Distinguished element — a root, a subject, an owner | rotate it to front | `anchor_cycle(members, anchor)` |
| Co-equal members, no principal one | any permutation | `orbit_digest(members, generators)` |

`reflect=False` is not a micro-optimization. A pipeline
`build → test → stage` and its mirror are different pipelines, and a
system that merged them would be losing information it cannot recover.

For `canonical_under` and `orbit_digest`, `perms` is a **generating
set** — the symmetries you can name — and the library closes it under
composition. That closure is what makes the guarantee hold. Feeding a
generating set to a canonicalizer that only scans the permutations it
was handed sends two members of one orbit to different representatives,
with no error raised:

```python
from spiritwriter.fabric import canonical_under, permutation_closure

rotate = [1, 2, 3, 0]                       # one generator; generates C4, is not C4
len(permutation_closure([rotate], 4))       # 4 — the group it stands for

canonical_under(items, [rotate])            # closed first: one form per orbit
canonical_under(items, [rotate], close=False)   # not closed: three forms, silently
```

`close=False` exists for a group you have already closed yourself. It is
a footgun everywhere else.

## What it measures at

`benchmarks/eval/structural_accuracy/` compares the rule against
similarity scoring on 200 pairs across three corpora — undirected rings,
directed cycles, unordered groupings. Every pair carries known ground
truth and is decided twice.

| | Caught (of 114 same-structure pairs) | Wrong merges (of 86) |
|---|---|---|
| Rule | 114 (100%) | 0 |
| Best threshold with no wrong merges (t=0.95) | 0 (0%) | 0 |
| Best threshold overall, chosen knowing the answers | 0 (0%) | 0 |

No threshold separates the classes, and the per-relation means say why:

| Relation | Mean similarity |
|---|---|
| rotation *(must merge)* | 0.690 |
| reflection *(must merge)* | 0.546 |
| permutation *(must merge)* | 0.610 |
| sibling *(must stay apart)* | **0.729** |

The pairs that must stay apart score *higher* than every class that must
merge. Rewriting a structure changes the text a great deal; altering the
structure changes it hardly at all. Similarity here is not a weak signal
to be tuned — it points the wrong way, and no cutoff repairs that.

The rule's 100% is true by construction and carries no information on
its own; canonicalization is exact, so anything less would be a bug
rather than a tuning problem. The column that means something is wrong
merges, because a rule that collapsed distinct structures would also
post perfect recall and be useless.

## Where it stops

**It reads no meaning.** It will never know that Twain and Clemens are
one person. It collapses exactly the differences you can state in
advance, and nothing else — everything past that still needs scoring, or
judgment, or a person. This is a floor under the fuzzy layer, not a
replacement for it.

**It runs one way.** Every rotation can be undone, but normalization in
general cannot. Folding case or dropping a field discards something it
cannot put back, so this moves from variant toward canonical and never
back out.

**Textual drift is a different problem.** Case, whitespace, typos, and
unicode variation are [`ess_accuracy`](../benchmarks/eval/ess_accuracy/)'s
subject, and similarity scoring handles much of it well. Do not quote
the two suites' numbers in one sentence — they measure different things,
and the honest summary is that textual drift mostly wants scoring while
structural variance cannot use it at all.

**Elements must be JSON-serializable**, since ordering and hashing go
through the same canonical JSON the shard layer uses. Anything else —
`bytes`, `datetime`, a domain object — needs a `key=` surrogate, the way
`sorted` takes one. Two inherited consequences are worth knowing: JSON
coerces dict keys to strings, so `{1: "a"}` and `{"1": "a"}` share a
form; and ordering is lexicographic over the encoded bytes, so numbers
sort as text (`10` before `9`) unless you pass `padded(width)`.

## Stability is a promise

A computed identifier is only useful while the computation is fixed.
Digests carry a versioned domain prefix, and
`tests/test_orbit.py` pins the full chain — canonical algorithm, JSON
encoding, prefix, hash — with golden vectors.

Changing any of it changes what every other system will compute and
breaks agreement with anything already deployed. That is a versioned
act: bump the domain tag, do not edit the constant.

## Prior art

Computing one agreed form for a structure is not a new technique.
Separate fields arrived at it independently over about fifty years:
canonical SMILES and InChI in chemistry; canonical labelling in graph
theory (nauty, Traces); RDF dataset canonicalization on the semantic
web; Booth's least-rotation algorithm (1980), which `least_rotation`
implements; and, oldest by a wide margin, citation order and
Ranganathan's facet formula in cataloguing.

Every one of those adoptions happened under the same condition: two
parties needing to agree on identity without coordinating first. That
condition is also the marker for when you do *not* need this. If a
registry exists, everyone can reach it, and somebody maintains it,
assign an identifier instead — a registry knows things no computed form
ever will, starting with Twain and Clemens.
