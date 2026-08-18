# Structural accuracy — all

Generated 2026-08-18T12:45:05+00:00

Pairs of records whose variation is structural, not textual. Ground
truth is known: a rotated ring *is* the same ring; a ring with two
members swapped is *not*. Each pair is decided twice — by canonical
digest, and by a similarity threshold over the text as written.

## Headline

| | Caught (of same-structure pairs) | Wrong merges |
|---|---|---|
| **Rule** | 114/114 (100%) | 0 |
| **Best threshold making no wrong merges** (t=0.95) | 0/114 (0%) | 0 |
| **Best threshold overall** (t=0.95, chosen knowing the answers) | 0/114 (0%) | 0 |

## Why no threshold works

- Weakest same-structure pair scores **0.281**
- Strongest different-structure pair scores **0.926**
- Different-structure pairs scoring at or above the weakest same-structure pair: **86 of 86**

The classes are **not separable** by any threshold on this signal. Similarity is not merely a weak signal here — it is inverted. Rewriting a structure changes the text a great deal, while altering the structure changes it hardly at all, so the score ranks the wrong pairs highest.

## Threshold sweep

| Threshold | Caught | Recall | Wrong merges |
|---|---|---|---|
| 0.50 | 97/114 | 85% | 83/86 |
| 0.55 | 75/114 | 66% | 82/86 |
| 0.60 | 64/114 | 56% | 78/86 |
| 0.65 | 40/114 | 35% | 64/86 |
| 0.70 | 34/114 | 30% | 58/86 |
| 0.75 | 24/114 | 21% | 37/86 |
| 0.80 | 11/114 | 10% | 29/86 |
| 0.85 | 1/114 | 1% | 6/86 |
| 0.90 | 0/114 | 0% | 1/86 |
| 0.95 | 0/114 | 0% | 0/86 |
| 1.00 | 0/114 | 0% | 0/86 |

## By relation

| Relation | Pairs | Settled by rule | Mean score |
|---|---|---|---|
| rotation | 26 | 26 | 0.690 |
| reflection | 19 | 19 | 0.546 |
| permutation | 69 | 69 | 0.610 |
| sibling *(must stay apart)* | 86 | 0 | 0.729 |

## Reading this

The rule's recall is 100% by construction — canonicalization is exact,
and a result below 100% would be a bug, not a tuning problem. The
number that carries information is the wrong-merge column: a rule that
collapsed distinct structures would score perfect recall and be
useless. Both must hold at once.

This measures only structural variation. Textual drift — case,
whitespace, typos, unicode — is the ESS harness's subject, and
similarity scoring handles much of it well. The two are complementary;
neither number belongs in the other's sentence.
