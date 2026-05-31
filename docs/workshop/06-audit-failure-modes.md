# Lesson 6: Audit Failure Modes

> The pipeline runs clean, the trace verifies, every finding is well-formed — and the audit is still wrong. Audit failure isn't the same as agent failure; it has its own modes, and the ones built up in lessons 1–5 don't catch them.

## What you'll learn

After this lesson you'll be able to recognize the five most common ways an audit pipeline fails *as an audit* (separate from the agent failing as a worker), and add the detection patterns that catch each one before it ships a misleading report.

## Prerequisites

- Completed [Lesson 4](04-trace-as-verification-layer.md) — you have hash-chained trace events
- Completed [Lesson 5](05-self-improving-pipelines.md) — you're scoring runs against a baseline
- You're running the pipeline often enough to have a corpus of past results to compare against

## Concepts

### Agent failure vs. audit failure

Lessons 1–5 covered failures of the **agent**: missing tools (lesson 2), prompt ambiguity (lesson 3), runs that drift between executions (lesson 5). Those produce *visibly broken* outputs — fewer findings, wrong format, hit turn limits.

Audit failures are different. The agent runs cleanly. The trace verifies. The output validates against schema. And the **audit is still wrong** — not as a worker product, but as a claim about the system it audited:

| Agent failure | Audit failure |
|---|---|
| "The pipeline broke" | "The pipeline ran and lied" |
| Discoverable via trace event types | Often invisible until ground truth disagrees |
| Mitigated by lessons 1–5 | Needs separate detection |
| Costs you re-runs | Costs you trust |

The five modes below are all "pipeline ran clean, audit still wrong" — and each is caught by a different pattern.

## Failure mode 1: False negatives

**Scenario.** Your APK audit returns "no exposed credentials found" — and the trace verifies, the methodology atom shows `rz-bin` was used, the report is well-formed. A month later, a security researcher finds an AWS key hardcoded in a string resource your extractor never read.

**Why it's audit-specific.** Every gate in lessons 1–5 was satisfied. The agent did exactly what you told it to. The failure is in what you told it to look at: your extractor reads DEX bytecode and `res/values/strings.xml`, but the key was in `assets/config.json`. The audit doesn't claim "no key in `assets/`" — it claims "no key found" — and the claim is too broad for what the methodology actually covers.

**Detection.**

1. **Maintain a known-bad regression corpus.** Keep 5–20 APKs (or whatever your inputs are) with known issues at known locations. Run every prompt or methodology change against this corpus. A regression to false-negative is the loudest signal you can build.

2. **Score against ground truth, not against prior runs.** Lesson 5's A/B scoring measures "did the new prompt match the baseline's findings count?" That's necessary but not sufficient — if the baseline already had blind spots, the new prompt will too, and the score will look fine. The known-bad corpus is your ground truth.

3. **Document methodology scope explicitly in each report.** A `methodology_scope` atom listing exactly what was inspected ("DEX classes, AndroidManifest, res/values/strings.xml") turns the false negative from "we missed it" into "we never looked there" — same outcome, very different remediation.

**Recovery.**

When a false negative is found in production, the immediate fix is broaden coverage. The structural fix is to add the missed location to the known-bad corpus so the next regression of this kind fails the gate.

## Failure mode 2: Evidence drift

**Scenario.** The audit ran on Tuesday. The report shipped on Friday. The APK on the device store on Friday is a different binary than the one audited on Tuesday — the vendor pushed an update. Your report is now a claim about a binary nobody can install.

**Why it's audit-specific.** This isn't your pipeline's fault — the input changed underneath you. But your report doesn't *say* that. It says "AcmeJail v2.4.1 was audited and found to contain..." when v2.4.1 was replaced by v2.4.2 mid-flight.

**Detection.**

1. **Content-address the input.** Hash the artifact (`sha256sum apk.apk`) at fetch time. Store the hash as an `input_hash` atom on the audit shard. Re-verify before publishing the report: if `sha256sum` of the file at the source URL no longer matches, the report's about a stale artifact.

2. **Pin source metadata.** Record the URL, fetch timestamp, and ETag (if available) on the input atom. A report can then say "audited 2026-05-30T14:00:00Z; sha256:abc123…; source ETag at fetch: def456…" instead of just naming the artifact.

3. **Re-verify at publish time.** Before a report leaves the pipeline, fetch the head of the source URL and compare. If they don't match, hold the report and re-audit, or publish with a clear "input has changed since audit" annotation.

**Recovery.**

When evidence drift is detected after publish, the report needs an explicit `input_superseded` atom recording the new hash and the audit's continued validity (or not). Silent re-issue without acknowledging the drift trains downstream consumers to ignore your version stamps.

## Failure mode 3: Findings rot

**Scenario.** A high-confidence finding from three months ago — "method X.foo() calls unsafe API Y" — comes up in a customer support thread today. You re-run the audit on the same APK with the same prompt and you can't reproduce the finding. Did you have it right then, or now?

**Why it's audit-specific.** Both runs may be internally consistent (trace verifies, agent did the right thing). The disagreement is between past-you and present-you. The model version drifted, the prompt got rewritten, the methodology evolved — but the *artifact* didn't. You can no longer answer "is this still true?" because the basis of the earlier claim isn't reproducible.

**Detection.**

1. **Pin the toolchain version on every finding.** Trace atoms for the audit should record: model id (`claude-sonnet-4-6` or whatever was actually called), prompt content hash, agent skill version, every external tool version (`rz-bin -v`). The trace chain is your bill-of-materials for the claim.

2. **Re-audit a sample on toolchain change.** When you upgrade a model or rewrite a prompt, run the new version against 10–20 historical reports. Any finding that changes is your delta — investigate before promoting.

3. **Mark findings with their basis hash.** A finding atom should carry the content hash of `(prompt + model_id + tool_versions + input_hash)`. Reproducibility is "can I recompute the basis hash?" — if not, the finding's basis has rotted away.

**Recovery.**

When findings rot is identified, the original report stays as a witness to "what we believed then" — don't retroactively rewrite it. New reports get pinned to the new toolchain. A `superseded_by` atom on the old report points at the re-audit that revisited the finding.

## Failure mode 4: Trust calibration drift

**Scenario.** Every finding ships with a confidence score: `high`, `medium`, `low`. Three months ago, `high` meant ~95% true. Today, after a model upgrade and a prompt rewrite, `high` empirically lands around 75% true — but the label is still `high`. Downstream consumers (your dashboard, an alerting rule, a customer's auto-remediation script) treat both the same.

**Why it's audit-specific.** Each individual finding's confidence may be self-consistent — the prompt asked for a 0–1 score and the agent gave one. The failure is at the population level: the label has *drifted* from what it used to predict.

**Detection.**

1. **Hold-out a labeled set.** Maintain a small corpus of inputs with hand-verified expected findings. Run it monthly. Bin findings by reported confidence; compute actual precision in each bin. If `high`-confidence findings are landing at 75% precision when they used to land at 95%, the label has drifted.

2. **Track confidence-bin precision as a first-class metric.** Add it to whatever dashboard you maintain. The graph should be flat over time — drift shows up as a downward slope.

3. **Don't let prompts redefine the scale.** A prompt that says "rate confidence 0–1" and another that says "high/medium/low" produce labels that aren't directly comparable across runs. Pin the scale and the cutoffs in the prompt; treat changes to either as a recalibration event.

**Recovery.**

When drift is identified, **recalibrate, don't retroactively rewrite.** The fix is to change the prompt so `high` once again means ~95%, then re-run from that point forward. Findings produced before the recalibration carry their original (now-stale) confidence; consumers downstream get a notice that the label semantics changed at timestamp T.

## Failure mode 5: Audit-of-audit gaps

**Scenario.** Your audit pipeline emits hash-chained trace events. Verification is automated. A finding is flagged for human review; a reviewer reads it and clicks "approve." The approval gets logged. Three weeks later, you can't reconstruct *why* the reviewer approved it — was it because the finding was wrong, or because they were rushing on a Friday afternoon?

**Why it's audit-specific.** The agent's work is fully traced. The *meta-audit* (the human or automated review of the agent's output) isn't. You have provenance for what the agent did but not for what the supervising layer did with it.

**Detection.**

1. **Trace the reviewer too.** Approval / rejection events should carry: reviewer id, decision, free-text justification, hash of the finding being reviewed, model/version of any LLM-assisted-review tools the reviewer used. Same chain primitive, applied one level up.

2. **Sample meta-decisions for re-review.** Take 5% of approvals (or some sampling rate) and have a different reviewer re-evaluate them blind. Disagreement above your threshold means the meta-audit has its own calibration drift.

3. **Don't let approvals dissolve into "looks good".** Require a structured justification atom alongside every approval. "Confirmed false positive: string match is in a comment, not active code" is auditable; "lgtm" is not.

**Recovery.**

When an audit-of-audit gap surfaces (usually because two reviewers disagree about a months-old decision), the structural fix is to upgrade the approval shape to carry the missing context. Past approvals can't be retroactively annotated — they stand as evidence of what was known then.

## Putting it together

The five modes share a shape: **lessons 1–5 verify the agent's work product; lesson 6 verifies the audit's value as a claim**. Each gets its own detection pattern, and each pattern produces a new kind of trace atom or per-finding metadata:

| Mode | Detection pattern | New atom / metadata |
|---|---|---|
| False negatives | Known-bad regression corpus | `methodology_scope` |
| Evidence drift | Content-address inputs; re-verify at publish | `input_hash`, `input_superseded` |
| Findings rot | Pin toolchain on every finding | `basis_hash`, `superseded_by` |
| Trust calibration drift | Periodic hold-out + per-bin precision | `confidence_calibration_run` |
| Audit-of-audit gaps | Trace the reviewer, sample for re-review | `review_decision`, `meta_audit_sample` |

The trace chain from lesson 4 is the backbone; each new atom kind plugs into it the same way.

## Checklist

- [ ] Build (or adopt) a known-bad regression corpus and run it against every methodology change
- [ ] Add a `methodology_scope` atom listing exactly what each audit inspected
- [ ] Content-address every input artifact; record the hash on the audit shard
- [ ] Re-verify input hashes at report-publish time; hold or annotate on drift
- [ ] Pin the toolchain (model id, prompt hash, tool versions) in trace atoms for every finding
- [ ] Run a hold-out labeled corpus monthly; compute per-confidence-bin precision; chart over time
- [ ] Treat human/automated approvals as their own auditable layer; require structured justification
- [ ] Sample meta-decisions for blind re-review

## What comes next

You can detect audit failure modes in your own pipeline. Now the question is what changes when you go from "one analyst's well-instrumented pipeline" to "a fleet of audits running unattended across a growing input set." Scaling confidence is its own discipline — that's **Lesson 7: Scaling with Confidence** (coming next).

---

Previous: [Lesson 5: Self-Improving Pipelines](05-self-improving-pipelines.md)
Next: *Lesson 7: Scaling with Confidence — coming next*
