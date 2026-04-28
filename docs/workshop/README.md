# Workshop: Adding AI Agents to a Security Audit Pipeline

Five lessons drawn from real incidents building [Frio](https://github.com/aaronmarkham/frio) — a jail roster monitoring system that uses Claude Code agents to discover and audit sheriff's office apps across the United States.

Every lesson starts with something that actually went wrong, then shows how to fix it. The lessons build on each other: environment setup → tool verification → prompt engineering → provenance tracing → continuous improvement.

## Lessons

1. [Environment & Permissions](01-environment-and-permissions.md) — the boring but mandatory stuff
2. [Tool Availability & Silent Degradation](02-tool-availability-and-silent-degradation.md) — when agents adapt in ways you didn't want
3. [Prompt Ambiguity and Non-Deterministic Output](03-prompt-ambiguity-and-nondeterministic-output.md) — same input, different output
4. [Trace as a Verification Layer](04-trace-as-verification-layer.md) — catching gaps automatically with spiritwriter
5. [Self-Improving Pipelines](05-self-improving-pipelines.md) — the payoff

## Prerequisites

- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed
- Basic familiarity with running shell commands
- A project with at least one task you want to hand to an agent

## Context

These lessons use Frio's audit pipeline as the running example. The pipeline discovers sheriff's office mobile apps (OCV platform), downloads APKs, extracts evidence using Rizin binary analysis, classifies findings against a canonical registry, and generates cryptographic provenance chains via spiritwriter.

You don't need Frio to follow along — the patterns apply to any pipeline where you're dispatching AI agents to do structured work and need to trust the output.
