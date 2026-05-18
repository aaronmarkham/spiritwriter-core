# People — Example Corpus

A ~50-entity hand-curated corpus exercising name-resolution drift modes
that show up in jail roster data (the original driving use case for
`spiritwriter.fabric.canonicalize`).

**This corpus is illustrative, not authoritative.** It exists to (a)
demonstrate the harness end-to-end, and (b) make the per-mutation-family
recall numbers in the report attributable to specific drift modes. Build
your own corpus for your own domain; see the eval suite's main README.

## Composition

| Group | Count | Why included |
|---|---:|---|
| Hispanic two-surname canonicals (Garcia Lopez, Hernandez Martinez, ...) | ~12 | Real-world frio drift: paternal+maternal surnames handled inconsistently across roster systems |
| Hispanic single-surname canonicals (Paten, Rodriguez, Jimenez, ...) | ~13 | Source of `surname_duplication` and `surname_hyphenate_duplicate` drift |
| English single-surname (Smith, Johnson, ...) | ~8 | Baseline; should resolve cleanly under universal mutations |
| European compound (O'Brien, MacDonald, Smith-Jones, St-Pierre) | ~5 | Apostrophes and hyphens exercise tokenization edges |
| Multi-cultural (Chen, Tanaka, Patel, Mohammed, Adebayo) | ~5 | Diversity check; mostly exercises universal families |
| Near-collision pairs (Carlos Garcia vs Carlos Sanchez, Maria Lopez sharing DOB with Maria Paten) | several | Negative-control sanity; ESS field divergence should keep them separate |

## Schema

```json
{
  "ess_fields":    ["last_name", "first_name", "dob"],
  "fuzzy_fields":  {"last_name": 0.85, "first_name": 0.80},
  "context_fields":["gender"]
}
```

Thresholds intentionally a touch tighter than the inmate schema in
[`bench_entity_resolution.py`](../../../bench_entity_resolution.py) (0.85
vs 0.90 on last_name) so the surname mutation families have a fair
chance to land at T2/T3 rather than being filtered out at the threshold.

## Known biases

- **English-language bias.** Diminutives map covers Spanish + English only.
  Transliteration and non-Latin scripts not exercised.
- **Hispanic-name overrepresentation.** Half the corpus is Spanish-surnamed
  because that's where the most interesting drift modes are. Don't read
  per-family numbers as "ESS handles Spanish names well/poorly" —
  read them as "ESS handles this *kind of drift* well/poorly."
- **DOB always present.** Real rosters are missing DOB ~20% of the time.
  Phase 2 corpus from real csp KBs will exercise the DOB-missing path.
- **No transliteration.** "Müller" → "Mueller" is a real drift mode
  not currently exercised.

## How to extend

Add a new record to `entities.json`. Re-run the harness. If your new
record exposes a drift mode the existing universal + people families
don't cover, add a generator to `mutations.py` and re-run.

To rebaseline (e.g. after tightening fuzzy thresholds), run the harness
and commit the new `results/people-*` directory alongside a note in the
PR explaining what shifted.
