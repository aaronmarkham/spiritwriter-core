# `case_only` — Clean Person Entities, Universal Mutations Only

20 hand-curated person entities exercising the harness's universal
mutation families (case, whitespace, typo, unicode, negative control)
against a content set that is *cleaner* than the `people` corpus —
no morphology stress, no two-surname stress, no diacritics.

## What this corpus measures

The cleanest path through the engine: case and whitespace drift on
well-formed person records. This is the corpus to point at when asking
"what does the engine deliver when the data looks like what most
systems actually have."

## What this corpus is for (and not for)

- **For**: characterizing per-family auto-merge recall on clean
  transcription drift. Pairs with the `people` corpus (kitchen-sink
  stress test) for the upper-vs-lower bound view.
- **Not for**: testing morphology, multi-surname patterns, transliteration,
  or any domain-specific drift. Those live in the `people` corpus or in
  future domain-specific corpora.

## Composition

20 entities, deliberately spread across name conventions to avoid
overfitting to one tradition:

- 8 Anglo (Smith, Johnson, Williams, ...)
- 5 Hispanic single-surname (Rodriguez, Gonzalez, Hernandez, ...)
- 3 European compound (O'Connor, MacDonald, Smith-Jones)
- 4 multi-cultural (Chen, Tanaka, Patel, Adebayo)

**Deliberate non-features:**
- **No diacritics** in any entity. The universal `unicode_normalization`
  family will be a no-op on this corpus — that's intentional. If you
  want to test diacritic handling, see a separate corpus (TODO) or the
  `people` corpus which has María / García.
- **No two-surname Hispanic names.** That's the `people` corpus's
  domain-specific territory.
- **All have DOB.** Lets DOB do its job as the strong ESS anchor. Test
  of DOB-missing behavior lives elsewhere.
- **Even gender split** (10F / 10M ish) to avoid one-pole sampling.

## Expected per-family outcome

Marketing-relevant claims come from the **per-family** rows, not the
whole-corpus aggregate. On this corpus:

| Family | Expected recall@T1+T2 | Why |
|---|---|---|
| `case` | 1.000 | Pure normalization handles it |
| `whitespace` | 1.000 | Pure normalization handles it |
| `typo_substitution` | 0.000 (recall@any=1.0) | T3 by design — CMC-Lite surfaces typos for review |
| `typo_insertion` | 0.000 (recall@any=1.0) | T3 by design |
| `unicode_normalization` | n/a | No diacritics in the corpus → no mutations generated |
| `negative_control` | n/a (false-merge=0 expected) | Canary |

Whole-corpus Recall@T1+T2 will land around 0.5 because half the
same-entity pairs the harness generates are typo mutations that
correctly land at T3. That number is a characterization, not a target.

## Sampling biases (known)

- **English/Latin-character bias**: no Cyrillic, Arabic, CJK script,
  Hebrew, Devanagari. Universal mutation families are byte-level
  operations; they assume ASCII-ish letters for typo families and
  Unicode normalization for diacritics. Other-script corpora would
  exercise different behaviors.
- **Year-of-birth range** clusters in 1976–1992. No minors, no centenarians.
- **20-entity sample** is small enough that one or two odd cases can
  shift per-family rates noticeably. Compare with caution.

## Provenance

Entity records are hand-curated by the harness author (2026-05-18) for
benchmark purposes. Not derived from any external dataset, not PII.
