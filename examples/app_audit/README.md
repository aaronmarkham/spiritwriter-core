# App Audit Demo

End-to-end demonstration of `spiritwriter.audit` — Android app security
audit with cryptographic provenance.

## What's here

- `run.py` — CLI runner that takes an existing audit report + APK and
  produces a trace chain and witness document, then verifies them.
- `witnesses/` — reference audit bundles (report + trace + witness) from
  8 apps on the OCV platform (community safety apps published by county
  agencies). Good for:
  - Inspecting what a complete audit looks like
  - Regression testing the `spiritwriter.audit.verify` code paths
  - Workshop material (audit artifacts, not the APKs themselves)

## Quick verify

Each witness bundle is independently verifiable at L1 (document
integrity) and L2 (hash-chain integrity) without needing the original
APK:

```bash
python -m spiritwriter.audit.verify examples/app_audit/witnesses/NV/Lyon\ County
```

Full L1-L4 verification requires the APK and extraction directory on
disk — ask the audit author for these if you need deeper verification.

## Running a new audit

See `skills/audit/SKILL.md` for the agent-driven scanning workflow.
This `run.py` only handles Steps 4-5 (witness + verify) assuming a
report already exists.

```bash
python examples/app_audit/run.py \
    --package com.example.app \
    --apk /tmp/app.apk \
    --extraction /tmp/app_extracted \
    --report ./audits/MyCity/report.json
```

## The witnesses

Each of the 8 bundles contains:

- `report.json` — the agent's findings (trackers, ad SDKs, permissions,
  hardcoded secrets, narrative)
- `trace.jsonl` — 24 hash-chained events: input → evidence → strings
  → per-finding → report
- `witness.json` — self-hashing summary binding every hash together

These audits were originally produced by the Frio transparency project
against public community-safety apps. The findings reflect commodity
tracking SDKs (Firebase Analytics, AdMob, AWS Pinpoint, etc.) common to
mobile apps across many verticals, plus a few platform-specific
surveillance integrations (Appriss VINE, AWS IoT, AssetTracker) that
are the reason careful audits matter.
