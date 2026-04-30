# Audit

The `spiritwriter.audit` module produces **tamper-evident security audits** for Android APKs. An audit binds the input APK hash, every extracted evidence file, every finding derivation, and the final report into a hash-chained trace plus a self-hashing witness document. Anyone with the APK and the extraction directory can re-run verification offline and confirm the report wasn't fabricated, edited, or based on different inputs than it claims.

The library is platform-agnostic — it doesn't extract APKs itself. You bring the report (from `apktool`, `jadx`, custom analyzers, manual review, whatever) and the audit module produces the provenance artifacts that prove the report is reproducible.

For workflow guidance — how to structure a multi-county audit, parallel agent dispatch, cross-checking findings — see [skills/audit/SKILL.md](../skills/audit/SKILL.md). This doc covers the Python API.

## What an Audit Produces

```
audits/<region>/<target>/
├── report.json       # the actual findings (you produce this)
├── trace.jsonl       # hash-chained event log (audit module produces this)
└── witness.json      # self-hashing summary that binds it all together
```

Plus the source APK and the extraction directory, which the verifier needs to confirm the on-disk evidence still matches what the trace claims.

## Quick Start: Trace an Existing Report

The most common entry point. You already have a `report.json`; you want to attach a trace + witness to it:

```python
from spiritwriter.audit import trace_existing_audit

trace_path, witness_path = trace_existing_audit(
    package_name="com.example.app",
    apk_path="/tmp/app.apk",
    extraction_dir="/tmp/app_extracted",
    report_path="./audits/MyCity/report.json",
)
```

`trace_existing_audit` walks the report, hashes the APK, scans the extraction directory for evidence files, derives a SHA-256 string corpus from all `classes*.dex`, emits a trace event for every finding, and finally emits `audit_report_generated`. It writes `trace.jsonl` and `witness.json` next to `report.json` (or under `output_dir` if passed). The trace is hash-chained; the witness self-hashes.

## Trace Events

Every audit emits five well-known event types in order:

| Event | Helper | Carries |
|---|---|---|
| `audit_input_registered` | `emit_audit_input(emitter, package_name, apk_path, download_source)` | APK SHA-256, size, package name, source |
| `audit_evidence_extracted` | `emit_audit_evidence(emitter, extraction_dir, patterns=None)` | Per-file hashes, file count, extraction patterns |
| `audit_strings_extracted` | `emit_audit_strings(emitter, extraction_dir, min_length=8)` | DEX corpus SHA-256, string count, DEX file list |
| `audit_finding_derived` (×N) | `emit_audit_finding(emitter, finding, evidence_file_hashes)` | Finding name, category, risk, evidence strings, evidence file hashes |
| `audit_report_generated` | `emit_audit_report(emitter, report_path, report)` | Report SHA-256, finding count, permission count |

A trace with N findings has `4 + N` events: input, evidence, strings, then N findings, then the report. `verify_chain(events)` returns `True` for an intact chain.

If you're rolling your own audit driver instead of calling `trace_existing_audit`, emit them manually in this order using a `TraceEmitter`:

```python
from spiritwriter.fabric.emitter import TraceEmitter
from spiritwriter.audit import (
    emit_audit_input, emit_audit_evidence, emit_audit_strings,
    emit_audit_finding, emit_audit_report,
)

emitter = TraceEmitter(
    run_id=f"audit-{package_name}-{date}",
    agent_id="audit-provenance",
    out_path=trace_path,
)

emit_audit_input(emitter, package_name=package, apk_path=apk_path)
ev = emit_audit_evidence(emitter, extraction_dir=extraction_dir)
emit_audit_strings(emitter, extraction_dir=extraction_dir)

for finding in report["findings"]:
    emit_audit_finding(emitter, finding=finding, evidence_file_hashes=ev["file_hashes"])

emit_audit_report(emitter, report_path=report_path, report=report)
```

The `TraceEmitter` is the same one used everywhere else in spiritwriter — see [tracing.md](tracing.md) for chain mechanics.

## Witness Document

A witness is a single JSON object that summarizes the entire audit and self-hashes. The `witness_sha256` field is computed over the document with that field set to `null`, then written back. Any later edit to any field changes the document's actual hash, breaking the self-claim:

```python
from spiritwriter.audit import generate_witness

witness = generate_witness(trace_path=trace_path, report_path=report_path)
# witness.json structure:
# {
#   "witness_version": "1.0",
#   "witness_sha256": "...",            # self-hash
#   "generated_at": "2026-03-31T05:32:15Z",
#   "input": {package_name, apk_sha256, apk_size_bytes, download_source},
#   "evidence": {extraction_file_count, file_hashes: {...}, dex_corpus_sha256, dex_string_count},
#   "findings": [{name, category, risk, evidence_strings, evidence_files, derivation_event_hash}],
#   "permissions": [...],
#   "hardcoded_secrets": [...],
#   "report": {path, sha256, finding_count, ...},
#   "trace_chain": {trace_path, trace_sha256, event_count, first_event_hash, last_event_hash, chain_intact},
#   "agent": {agent_id, run_id}
# }
```

The `derivation_event_hash` on each finding pins it to the exact `audit_finding_derived` event in the trace JSONL. A reader can lift any finding from the witness, find that hash in the trace, and replay the chain from there to confirm the finding existed at trace-emit time and wasn't inserted later.

## Verification

Four independent levels, each fast enough to run as a CI check:

```python
from pathlib import Path
from spiritwriter.audit import verify, verify_l1, verify_l2, verify_l3, verify_l4

audit_dir = Path("./audits/MyCity")

# Run them individually
issues = verify_l1(audit_dir)                                     # document hashes match
issues = verify_l2(audit_dir)                                     # chain hashes link
issues = verify_l3(audit_dir, apk_path=apk, extraction_dir=ext)   # APK + evidence still match
issues = verify_l4(audit_dir, extraction_dir=ext)                 # findings derive from evidence

# Or all four at once
results = verify(audit_dir, apk_path=apk, extraction_dir=ext)
# results = {"l1": [...], "l2": [...], "l3": [...], "l4": [...]} — empty lists mean pass
```

Each level returns a `list[str]` of issues. An empty list means pass.

| Level | Checks | Needs APK? | Needs extraction? |
|---|---|---|---|
| **L1: Document integrity** | Witness self-hash valid; `report.json` matches witness's `report.sha256`; `trace.jsonl` matches witness's `trace_chain.trace_sha256`. | No | No |
| **L2: Chain integrity** | `verify_chain(events)` returns True; event count matches witness claim. | No | No |
| **L3: Evidence provenance** | APK SHA-256 matches witness; every evidence file's current hash matches the witness's `file_hashes` map. | Yes | Yes |
| **L4: Finding derivability** | Each finding's `evidence_strings` actually appear in the DEX corpus or `.properties` files in the extraction directory. | No | Yes |

L1 and L2 are pure document-integrity checks — they only need the audit directory. L3 confirms the audit hasn't been generated against a swapped APK or extraction. L4 confirms the findings aren't fabricated — every claimed evidence string really is present in the extracted bytes.

## Findings Registry

Audits across many targets need a shared vocabulary — calling Firebase Analytics "Firebase" in one report and "Google Firebase Analytics" in another makes cross-comparison impossible. The registry is a SQLite-backed canonical findings database that resolves variant names to a single entry.

```python
from spiritwriter.audit import load_registry, canonical_finding_list, validate_report

registry = load_registry()                       # opens ~/.spiritwriter/audit/findings.db
print(canonical_finding_list(registry))          # markdown-formatted listing for prompts

# Validate a report against the registry
validation_issues = validate_report(report, registry)
# Each issue: {"finding": "...", "type": "naming_drift" | "category_mismatch" | "unknown",
#              "suggestion": "..."}
```

`validate_report` checks each finding's name against the registry using the canonicalize fuzzy-match layer at 0.90 confidence. Three failure modes:

- **`naming_drift`** — variant of a known SDK ("Firebase Analytics SDK" → suggest "Firebase").
- **`category_mismatch`** — known SDK but report's category doesn't match the registry's.
- **`unknown`** — no match. Either it's a new SDK (seed it), or a typo, or an actually-novel finding worth investigating.

The registry ships pre-populated with common tracking and surveillance SDKs: Firebase, AdMob, AWS Amplify, Mixpanel, Facebook SDK, Appriss VINE, etc. Re-seed to add more:

```python
from spiritwriter.audit.seed import seed
seed()                          # idempotent — bundled canonical_findings.json only
seed(extra=[                    # programmatic additions
    {"name": "MyVendor SDK", "category": "tracking", "default_risk": "MEDIUM", "aliases": ["MyVendor"]},
])
```

Idempotent by name + alias — running twice doesn't produce duplicates.

## Threat Model

What audit-layer provenance protects against:

- **Report fabrication.** A claim of "Firebase detected via class name `FirebaseInstanceId`" is verifiable: L4 confirms that string exists in the DEX corpus the audit was run against.
- **Post-hoc editing.** Edit a finding's risk level after publication; the trace's `audit_finding_derived` event hash no longer matches the witness's `derivation_event_hash`, and L1 fails.
- **Evidence swap.** Re-extract the APK, regenerate the report, but keep the old witness; L3 fails because the new file hashes don't match the witness's claims.
- **APK swap.** Hand a different APK; L3 fails on the input APK SHA-256 mismatch.

What it does not protect against:

- **A compromised auditor.** The agent that emits trace events is trusted at emit time. If the auditor itself is compromised, it can construct a trace that's internally consistent but not reflective of reality — L4 catches fabricated *evidence strings* (those have to actually exist in the extraction), but L1–L3 are integrity checks, not honesty checks.
- **APK that was tampered before extraction.** The audit binds the APK's SHA-256 you fed it; if the APK was modified before reaching you, the audit accurately captures a flawed input.
- **Extraction tool bugs.** If `apktool` or `jadx` mis-extracts content, the audit reflects what the extraction produced, not the original APK source.

For multi-tenant or adversarial settings, sign the witness with Ed25519 (see [encryption.md](encryption.md#ed25519-signing-for-result-integrity)) so the verifier can also confirm *who* produced the audit, not just that the audit's internal hashes line up.

## What Audit Is Not

- **Not a scanner.** This module records what an analyst found; it doesn't find findings. Pair it with `apktool`/`jadx`/MobSF/manual review.
- **Not a vulnerability database.** The registry tracks SDK identities, not CVEs or exploit patterns. Cross-reference with NVD or vendor advisories separately.
- **Not real-time monitoring.** Audits are point-in-time evidence about a specific APK build. App version changes mean re-running the audit.
- **Not a substitute for static analysis.** Provenance proves an audit happened against the inputs it claims; it doesn't prove the audit was thorough or that the analyst's interpretation was correct.
