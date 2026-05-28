# `publications` — Academic Paper Citation Drift

30 hand-curated academic papers from the LLM / agent-memory /
entity-resolution / security / classic-ML neighborhood, exercising
citation-style drift modes that show up across reading lists,
bibliographies, and informal references.

## What this corpus measures

Tests the engine on a **structurally different schema** from the
person corpora — `(title, first_author_last, year)` instead of
`(last_name, first_name, dob)` — to validate the "domain-agnostic"
claim. Different field shapes, different fuzzy thresholds, different
drift modes, same engine.

## Schema

```
ess_fields:    [title, first_author_last, year]
fuzzy_fields:  {title: 0.85, first_author_last: 0.90}
context_fields:[venue]
jaccard_fields:[title, first_author_last]
```

- `title` carries most of the entropy and most of the drift potential.
- `first_author_last` is the most stable identifier (rare to misspell, and
  short strings get a stricter fuzzy threshold).
- `year` is small-cardinality (4 chars) but anchors the ESS.
- `venue` is contextual; not in ESS but available for tiebreaking.

## What's included (per-corpus mutation families)

| Family | Drift it models |
|---|---|
| `title_subtitle_drop` | "BERT: Pre-training of..." → "BERT" — very common in informal citations |
| `title_subtitle_add` | Reverse — short title gets a plausible subtitle |
| `year_missing` | Bibliographic record omits year |
| `first_author_initial` | "Devlin" → "J. Devlin" — initial leaks into surname field |

Plus universal families (`case`, `whitespace`, `typo_substitution`,
`typo_insertion`, `unicode_normalization`, `negative_control`).

## Composition (the actual 30 papers)

Three subgroups for coverage:

**LLM / agent-memory neighborhood (15 papers)**
BERT, GPT-3, Attention Is All You Need, T5, RoBERTa, ELECTRA, LoRA,
Chain-of-Thought, ReAct, Toolformer, MemGPT, Generative Agents, Voyager,
Constitutional AI, HuggingGPT.

**Entity resolution / capability / spiritwriter ancestors (9 papers)**
Macaroons, Zep, SimpleMem, EMem, EDC, Probabilistic Signatures
(Zhang 2018), Fellegi-Sunter, McCallum canopy clustering, Köpcke ER
frameworks survey.

**Classic deep learning anchors (6 papers)**
ResNet, AlexNet (Krizhevsky), LSTM (Hochreiter), Word2Vec, AlphaGo, AlphaFold.

**Deliberate composition choices:**
- Two papers with **first_author_last = "Liu"** (RoBERTa 2019, SimpleMem 2026)
  to ensure the engine has to use year + title to disambiguate, not just
  surname.
- Two papers with **first_author_last = "Zhang"** (EDC 2024, Probabilistic
  Signatures 2018) — same test.
- Mix of NeurIPS / ICLR / arXiv / EMNLP / Nature / classic venues
  so the venue context field has variety.

## What's deliberately NOT here

- **Non-Latin titles** (CJK, Cyrillic, Arabic-script). Universal mutation
  families are byte-level operations; testing those scripts would
  exercise different behaviors and warrants its own corpus.
- **Citations with et al. expansion** — we only have first author, so
  the "et al." vs full-author-list drift can't be modeled at this schema.
  Workable in a richer schema (`authors: list[str]`) — future work.
- **Duplicate papers with the same canonical title but different first
  authors** — that would test homonym disambiguation but is rare in this
  domain.

## Expected per-family outcome

- `case`, `whitespace` → 100% recall@T1+T2 on title + first_author_last
- `title_subtitle_drop` → likely T3 (depends on length ratio after drop)
- `title_subtitle_add` → likely T3 (similar)
- `year_missing` → T3 (year is ESS-affecting; title fuzzy might rescue)
- `first_author_initial` → T3 ("J. Devlin" vs "Devlin" fuzzy)
- `typo_*` → 0% T1+T2, 100% any-tier (T3 by design)
- `negative_control` → 0% false-merge

Whole-corpus Recall@T1+T2 will land below 50% because the corpus is
specifically designed to exercise non-universal drift modes that
deliberately surface at T3 rather than auto-merge.

## Sampling biases (known)

- **Anglo / Latin-character / English-language bias** in title text.
- **AI/ML/security overweight** vs general academia. Reflects the
  spiritwriter ecosystem's actual citation neighborhood rather than
  a random sample.
- **First-author surname diversity is modest** — most surnames are
  Western or East-Asian-Romanized. No Cyrillic, Arabic, Devanagari.
- **30-entity sample** is small. Per-family rates may shift with
  larger samples; compare with the same caution as the other corpora.

## Provenance

Hand-curated by the harness author (2026-05-19). All entries are real
papers with verifiable arXiv / DOI / publication records; this corpus
is not synthetic. No PII (academic publishing is public record).
