# Workshop: Adding AI Agents to a Security Audit Pipeline

Lessons on building reliable AI agent pipelines, drawn from real incidents building [Frio](https://github.com/aaronmarkham/frio)'s audit pipeline across 20 OCV apps and 5 states. Every lesson starts with something that broke and shows the fix.

The lessons build on each other: environment setup → tool verification → prompt engineering → provenance tracing → continuous improvement → distinguishing audit failures from agent failures → scaling that reliability.

## Lessons

1. [Environment & Permissions](01-environment-and-permissions.md) — setting up headless agents with the right permissions and PATH
2. [Tool Availability & Silent Degradation](02-tool-availability-and-silent-degradation.md) — preventing agents from falling back to weaker methods
3. [Prompt Ambiguity and Non-Deterministic Output](03-prompt-ambiguity-and-nondeterministic-output.md) — writing prompts that specify requirements, not capabilities
4. [Trace as a Verification Layer](04-trace-as-verification-layer.md) — automated verification with spiritwriter provenance chains
5. [Self-Improving Pipelines](05-self-improving-pipelines.md) — A/B testing prompts with trace-based scoring
6. [Audit Failure Modes](06-audit-failure-modes.md) — when the pipeline runs clean but the audit is still wrong: false negatives, evidence drift, findings rot, trust calibration drift, audit-of-audit gaps
7. [Scaling with Confidence](07-scaling-with-confidence.md) — going from "one well-instrumented pipeline you watch" to "a fleet running unattended" without losing trust: representative-sample pilots, staged rollout with abort criteria, ensemble verification, fleet-health signals, triage sampling

## Prerequisites

- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed
- Basic familiarity with running shell commands
- A project with at least one task you want to hand to an agent

## Context

These lessons use Frio's audit pipeline as the running example. The pipeline discovers sheriff's office mobile apps (OCV platform), downloads APKs, extracts evidence using [Rizin](https://rizin.re/) binary analysis, classifies findings against a canonical registry, and generates cryptographic provenance chains via spiritwriter.

You don't need Frio to follow along — the patterns apply to any pipeline where you're dispatching AI agents to do structured work and need to trust the output.
