# Lesson 7: Scaling with Confidence

> The pipeline works on 10 inputs. You watch every run. Now run 1000 a day, unattended — and the signals that earned your trust silently stop working. Confidence doesn't survive scaling automatically; it needs new patterns.

## What you'll learn

After this lesson you'll know how to go from "one well-instrumented pipeline you watch" to "a fleet running unattended" without losing the trust earned in lessons 1–6. Five patterns, ordered as a journey: pilot gating → rollout staging → ensemble verification → fleet-health monitoring → triage sampling.

## Prerequisites

- Completed [Lesson 6](06-audit-failure-modes.md) — you can name your audit's failure modes and have detection patterns for each
- A pipeline that works correctly on a small batch you've manually inspected
- Some appetite for spending a few minutes inspecting aggregate dashboards rather than individual runs

## Concepts

### Why "scaling" needs its own lesson

At pilot scale, your trust signals are **per-run**: you read the trace, you eyeball the report, you spot the regression. Every run gets a human pass. The pipeline is reliable because **you** are.

At fleet scale — hundreds of runs a day, unattended — those signals don't survive translation:

| Pilot signal | What it becomes at scale |
|---|---|
| "I read the trace" | …of one trace out of 800 today |
| "I noticed the report looks shorter" | …if I happen to load that report |
| "The methodology event shows `rz-bin` was used" | …in 99.4% of runs; the 0.6% are silently regex-fallbacks |
| "Cost feels about right" | …averaged across the fleet; outlier costs hide inside |
| "I'd notice if a known-bad input passed clean" | …if I knew to look at that specific run |

Scaling shifts the unit of attention from the run to the **fleet**. The patterns below are about preserving the trust signals — not abandoning per-run rigor, but augmenting it with fleet-level instrumentation so trust survives the volume.

## Pattern 1: Representative-sample pilots, not convenient ones

**Scenario.** Your pilot ran 10 inputs through the pipeline. Eight passed clean, two flagged for review — about what you'd expect. You scale to 1000. Now 600 flag for review. The pilot didn't predict production because the 10 pilot inputs were the ones you happened to have on disk — the easy ones.

**Why this matters.** Pilot results predict production only if the pilot distribution matches production. Convenient pilots ("inputs from one source / one date / one team") systematically underrepresent the failure modes that show up at scale.

**Pattern.**

1. **Stratify your pilot.** Across whatever your inputs vary on — source, region, vendor, content type, size — sample the pilot to cover each stratum, not the most-available inputs. If production hits 5 jurisdictions, the pilot hits 5 jurisdictions.

2. **Include adversarial samples.** A few pilot inputs should be from your known-bad regression corpus (lesson 6) and a few should be deliberately edge-case (very large, very small, weird encoding, mixed language). If the pilot only contains the median input shape, you'll miss the tails — and tails are where scale finds you.

3. **Compute production-equivalent error rates.** "8/10 passed" isn't 80%; with N=10 the 95% confidence interval is ~44–97%. Pilots at this size mostly tell you the pipeline isn't broken — not what its production rate will be. Either pilot bigger (≥100) or treat the pilot as smoke test and reserve the confidence claims for the rollout phase.

## Pattern 2: Staged rollout with abort criteria

**Scenario.** Pilot looked fine; you flipped the new methodology to 100% of production overnight. Tuesday's batch produced 47 false-positives the pilot didn't have, and you've already shipped 600 reports.

**Why this matters.** Going from 10 inputs (pilot) to 1000 (full prod) in one step gives you no signal between "this works" and "this is loose in the world." You need percentages in between, and you need defined abort criteria — not "look at the numbers and decide."

**Pattern.**

1. **Stages of 10% → 50% → 100%** (or 1 region → 3 regions → all). Hold at each stage long enough to see at least one batch run complete and one human review cycle. "Hold" means new runs go to the new methodology, while older runs continue on the previous one — so divergence is observable.

2. **Define abort criteria before the stage starts.** Concretely: "abort the 50% stage if false-positive rate exceeds 2× the pilot rate, OR per-run cost exceeds 1.5× pilot, OR any single run hits the budget cap." Pre-committed thresholds beat in-flight judgment calls every time — the latter drift toward whatever decision feels least disruptive in the moment.

3. **Emit a `rollout_stage_started` trace event** at each transition with the stage's percentage, the abort thresholds, and the prior-stage measured baseline. When the post-mortem asks "what did we think we were testing?" the event answers.

4. **Plan the abort.** "We stopped the rollout" only matters if there's a defined backstop — restore the previous methodology version, mark already-emitted reports under the new methodology as `superseded_by` (lesson 6) the re-run under the previous one, communicate to downstream consumers. If you can't list the backstop in advance, you don't actually have an abort.

## Pattern 3: Ensemble verification — disagreement as a signal

**Scenario.** A finding ships. A downstream consumer challenges it. You re-run the audit and get a different result. Now you have one report saying X and one saying Y — which is right? You don't know, because you only ran the audit once each time.

**Why this matters.** A single audit is a sample of size 1 from a noisy distribution (LLM, fuzzy matching, network conditions). At pilot scale you re-run by hand when something looks off. At fleet scale you need that disagreement signal *before* a consumer asks for it.

**Pattern.**

1. **Run N independent audits in parallel; treat agreement as the strong signal, disagreement as a flag.** Two runs that produce identical findings are evidence the methodology is stable on that input. Two runs that disagree don't tell you who's right — but they tell you *not to ship either as high-confidence* until a higher-quality pass adjudicates.

2. **Pick N for cost.** N=2 is the cheapest meaningful ensemble (you only learn about disagreement, not "majority opinion"). N=3 lets you majority-vote at 1.5× cost. N=5 buys diminishing returns. Most production fleets run N=2 by default and escalate to N=3 only for inputs the first pair disagreed on.

3. **Vary what makes the runs independent.** Same prompt run twice on the same input with `temperature=0` is roughly one sample, not two. To get the disagreement signal you need either temperature variation, prompt variation, model variation, or input perturbation. The cheapest is temperature; the most informative is model.

4. **Trace the ensemble.** Each run gets its own trace; each run emits an `ensemble_member` event tagging it with the ensemble id and member index. A `verify_chain`-able audit of "show me run 2 of ensemble abc-123" is the basic accountability primitive.

## Pattern 4: Fleet-health signals (when individual review doesn't scale)

**Scenario.** You're running 1000 audits a day. Yesterday's batch was fine. Today's batch is fine. Tomorrow's batch is fine. Three weeks pass; you do a spot-check on a random batch and 12% are subtly worse than the equivalent batch three months ago — a slow drift in finding quality nobody noticed because every individual day looked fine.

**Why this matters.** Fleet-scale degradation often shows up as a slope, not a step. Individual runs cross no obvious threshold; the aggregate quietly slides. The signals that catch this are statistical, computed across the fleet rather than per-run.

**Pattern.** Track at least these per-batch aggregates, plot them over time, and treat sustained trend changes as their own incidents (not just outliers):

1. **Findings-per-input rate.** Trending down? Either inputs got cleaner or methodology got blinder. The known-bad regression corpus from lesson 6 disambiguates — if known-bad inputs still surface their planted findings, methodology held; if not, you've lost coverage.

2. **Tier distribution.** What fraction of findings ship at each confidence tier? A drift from "60% T1, 30% T2, 10% T3+" toward "40% T1, 30% T2, 30% T3+" means the methodology is producing more uncertain findings — could be content change, could be drift.

3. **Cost per input.** A drift up means inputs got harder OR methodology got more expensive. Either is worth knowing. A drift *down* is suspicious — if the methodology silently got cheaper, what got skipped?

4. **Per-source / per-tenant distribution.** Aggregating across the whole fleet hides single-source regressions. "Our overall false-positive rate is 3%" masks "source X has 18% false-positive rate; source Y has 0%." Faceted dashboards prevent the hiding.

5. **Trace event volume per finding.** Stable methodology produces a stable number of trace events per finding (chunking events, extraction events, resolution events…). A drop suggests the methodology silently shortened — exactly the agent-failure mode from lesson 2, now showing up as a slope rather than a single observation.

These five live in the dashboard layer, not the spiritwriter primitive layer — but they're computed from the trace JSONL the runs produce. The primitives feed the dashboard; the dashboard is where the fleet-health story is told.

## Pattern 5: Triage sampling for human review

**Scenario.** You can't read 1000 traces a day. You also can't trust the fleet without *anyone* reading them. So you sample — but if the sampling is uniformly random, you mostly read boring runs and miss the interesting ones.

**Why this matters.** Human review time is the scarcest resource. Spent randomly, most of it lands on the median run that didn't need it. Spent on triage signals, it lands on the runs most likely to surface problems — without inflating to "review everything."

**Pattern.**

1. **Stratified sampling, not uniform.** Sample N runs per batch, but stratify by triage signal: take some from "high cost," some from "low confidence," some from "ensemble disagreement," some from "new source," and a small slice from "random" (so you also calibrate that your triage signals aren't themselves drifting).

2. **Surface the triage reason on the review surface.** "Sampled because: ensemble disagreement (member 1 found 3, member 2 found 5)" tells the reviewer where to look. Without the reason, the reviewer reads the report cold and the triage signal's information is wasted.

3. **Close the loop.** Every reviewed run produces a `meta_audit_decision` trace event (lesson 6 pattern 5). Aggregate those decisions to compute precision per triage signal — "the 'ensemble disagreement' triage caught a real issue 40% of the time; 'high cost' caught one 12% of the time." Re-tune the sampling weights to favor signals with better hit rates.

4. **Keep the random slice.** It's small (5–10% of the sample) but load-bearing. Without it your review pipeline only sees what your triage signals think is interesting — and you can't detect when the triage signals themselves go wrong.

## Putting it together

The five patterns are a journey, not a flat menu:

| Stage | Pattern | Question it answers |
|---|---|---|
| Pre-rollout | 1. Representative-sample pilots | "Does pilot success predict production success?" |
| Rolling out | 2. Staged rollout with abort criteria | "Can we stop if it goes wrong, before it goes too wrong?" |
| In production | 3. Ensemble verification | "Is this run's result reproducible?" |
| In production | 4. Fleet-health signals | "Is the fleet drifting?" |
| In production | 5. Triage sampling | "Where should the scarce human-review time go?" |

Trace events feed all five. The primitives from lessons 1–5 don't change at scale; what changes is which trace events you aggregate over and what dashboards you build on top.

## Checklist

- [ ] Build a pilot that's stratified across your input variation (source, region, content type, size) — not the inputs that were convenient
- [ ] Include known-bad and edge-case inputs in the pilot batch
- [ ] Define rollout stages (10% → 50% → 100% or equivalent) with pre-committed abort criteria for each
- [ ] Emit `rollout_stage_started` trace events at each transition with the stage's thresholds + prior-stage baseline
- [ ] Document the abort backstop *before* starting the rollout
- [ ] Run ensemble verification (N≥2) by default; escalate to N=3 only on first-pair disagreement
- [ ] Vary what makes ensemble runs independent (temperature, prompt, model, input perturbation)
- [ ] Compute and chart fleet-health aggregates per batch: findings-per-input rate, tier distribution, cost per input, per-source faceting, trace events per finding
- [ ] Sample for human review by triage signal (high cost, low confidence, ensemble disagreement, new source) plus a small random slice
- [ ] Surface the triage reason on the review surface
- [ ] Emit `meta_audit_decision` events from reviews; re-tune sampling weights from precision-per-triage-signal

## Where this leaves you

You started this workshop with "run agent and hope" and got to "audit a few inputs and trust the report" by Lesson 5. Lesson 6 added the patterns to detect when the audit is wrong even when the pipeline ran clean. Lesson 7 turned the trust signals into ones that survive volume.

The pieces that hold the whole thing together haven't changed: hash-chained trace events, structured findings, content-addressed inputs, pinned methodology. The discipline is the same; the surfaces you watch grow with the work.

There's no Lesson 8. From here it's running the practices and tuning them — your dashboards, your abort criteria, your triage weights become specific to your domain, and the workshop's job is done. Go build.

---

Previous: [Lesson 6: Audit Failure Modes](06-audit-failure-modes.md)
Back to: [Workshop Overview](README.md)
