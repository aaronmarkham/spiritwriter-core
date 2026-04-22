---
name: audit
description: Security audit of Android apps with cryptographic provenance — extract trackers, dangerous permissions, hardcoded secrets, and surveillance SDKs from an APK, then emit a hash-chained trace and self-hashing witness document. Uses Rizin (rz-bin) for binary extraction.
---

# Skill: App Audit

Run a traced security audit of an Android APK. Produces three artifacts per audit:

```
./audits/{category}/{name}/
    report.json     — findings, permissions, hardcoded secrets, narrative
    trace.jsonl     — hash-chained events from input APK to final report
    witness.json    — self-hashing document binding APK → evidence → findings
```

The witness chain is verifiable at four levels (L1–L4) so a third party can
confirm that the findings derive from the claimed APK bytes, not post-hoc
speculation.

## When to Use

- Audit a mobile app for tracking SDKs, ad networks, or surveillance infrastructure
- Produce an artifact you can hand to a journalist, lawyer, or policy team
- Teach/demo provenance: an audit that shows its work, cryptographically

## Install

```bash
pip install -e /path/to/spiritwriter-core
python -m spiritwriter.audit.seed      # one-time: populate canonical findings DB
```

Rizin (`rz-bin`) is the binary extractor. On Windows it installs to
`C:\Program Files\rizin\bin\`; on macOS/Linux install via your package
manager (`brew install rizin` / `apt install rizin`). Tested with
`rz-bin 0.8.2`.

## Concepts

| Concept | What it is |
|---|---|
| **Canonical findings registry** | SQLite-backed list of known SDKs (Firebase, AdMob, VINE, AWS IoT, ...) with categories + default risk. Audit agents use exact names so reports stay comparable. |
| **Trace chain** | `trace.jsonl` with 5 event types: input registration, evidence file hashes, DEX string corpus hash, per-finding derivation, report hash. Each event links to the previous via `prev_event_hash`. |
| **Witness** | `witness.json` — self-hashing summary binding the APK hash, every extracted file's hash, every finding's evidence, and the final report hash. Tampering with any part invalidates the witness `sha256`. |
| **4 verification levels** | L1 document integrity, L2 chain integrity, L3 evidence provenance (needs APK), L4 finding derivability (needs extraction). |

## Workflow

```
/discover              →   /audit                →   audits/.../*
 find app, get APK         extract, scan, trace        report + trace + witness
```

### Step 1 — Download and extract the APK

APK workspace lives outside the repo (e.g. `/tmp/audit_workspace/` on
macOS/Linux, `C:\tmp\audit_workspace\` on Windows). Never commit APKs
or extraction dirs.

```python
import requests, zipfile
pkg = "com.example.app"
r = requests.get(
    f"https://d.apkpure.net/b/APK/{pkg}?version=latest",
    headers={"User-Agent": "Mozilla/5.0", "Referer": "https://apkpure.com/"},
    timeout=120,
)
with open(f"{pkg}.apk", "wb") as f:
    f.write(r.content)
zipfile.ZipFile(f"{pkg}.apk").extractall(f"{pkg}_extracted/")
```

### Step 2 — Scan with a subagent

Hand the extracted APK to a subagent with the canonical findings list
embedded in the prompt. The agent extracts DEX/native strings with
Rizin and matches against the registry.

Paste the output of `canonical_finding_list` into the agent's prompt so
it uses exact names:

```python
from spiritwriter.audit import load_registry, canonical_finding_list
print(canonical_finding_list(load_registry()))
```

Prompt template (feed to the Agent tool):

```
You are a mobile app security auditor. Audit the Android app at
{EXTRACTION_DIR} for trackers, ad SDKs, hardcoded secrets, and
surveillance infrastructure.

Use the canonical finding names below. Do NOT invent variant names.
If you find something new, name it clearly and add "canonical": false.

Canonical finding registry:
{PASTE canonical_finding_list OUTPUT HERE}

Steps:
1. Scan all .properties files for SDK identifiers and versions.
2. Extract strings from every classes*.dex with Rizin:
     rz-bin -qq -zz -N 8:256 classes{N}.dex
   Fallback if Rizin unavailable:
     re.findall(rb'[\x20-\x7e]{8,}', open(path,'rb').read())
3. For native libs (lib/arm64-v8a/*.so), also run:
     rz-bin -qq -zz -N 10:500 lib/arm64-v8a/*.so
     rz-bin -i lib/arm64-v8a/*.so      # imported symbols
     rz-bin -E lib/arm64-v8a/*.so      # exported JNI entries
     rz-bin -l lib/arm64-v8a/*.so      # linked libraries
4. Search DEX + .so strings for:
   - Trackers: firebase, facebook, admob, ca-app-pub-, kochava, appsflyer,
     amplitude, mixpanel, adjust.com, sentry, crashlytics, bugsnag, comscore
   - AWS: pinpoint, cognito, amazonaws
   - Carceral data: appriss, vine, offenderwatch, icrime
   - IoT/tracking: AssetTracker, TagAlong, aws-iot
   - Ads: pagead2, googlesyndication, advertising.*id, GAID
   - Hardcoded secrets: API keys (AIzaSy..., sk-...), Firebase App IDs,
     storage buckets, Cognito pool IDs
   - Endpoints: any https:// URLs to analytics/tracking services
5. Extract Android permissions from AndroidManifest.xml (binary XML —
   regex on raw bytes for `android.permission.*`).
6. For each finding, include: canonical name, category, risk
   (low|medium|high|critical), sdk_version if available, evidence[]
   (file + matched string), endpoints[], data_collected[].
7. Write the full report to {REPORT_PATH}. Include a narrative
   summary at summary.narrative for a non-technical audience.

Be thorough. Check every DEX file. Flag anything unusual.
```

### Step 3 — Validate against the canonical registry

```python
import json
from spiritwriter.audit import load_registry, validate_report

registry = load_registry()
report = json.load(open("audits/MyCity/report.json"))
issues = validate_report(report, registry)
for i in issues:
    print(i)
```

Issue types:

- `category_mismatch` — agent used wrong category, fix in report
- `name_drift` — agent used a variant name, rename to the canonical
- `unknown_finding` — genuinely new SDK, review and seed if legitimate
- `risk_mismatch` — informational (context may justify deviation)

To canonicalize a new finding, add it to
`spiritwriter/audit/data/canonical_findings.json` and re-seed.

### Step 4 — Generate the trace chain and witness

```python
from spiritwriter.audit import trace_existing_audit

trace_path, witness_path = trace_existing_audit(
    package_name="com.example.app",
    apk_path="/tmp/audit_workspace/com.example.app.apk",
    extraction_dir="/tmp/audit_workspace/com.example.app_extracted",
    report_path="audits/MyCity/report.json",
)
```

This produces `trace.jsonl` (hash-chained events) and `witness.json`
(self-hashing proof document) next to the report.

**Note:** the emitter appends, so `trace_existing_audit` will overwrite
any stale `trace.jsonl` in the output directory before emitting. If you
call the low-level `emit_*` functions directly, delete `trace.jsonl`
first.

### Step 5 — Verify

```bash
# L1+L2 (from committed files alone)
python -m spiritwriter.audit.verify audits/MyCity

# L1-L4 (requires APK + extraction on disk)
python -m spiritwriter.audit.verify audits/MyCity \
    --apk /tmp/audit_workspace/com.example.app.apk \
    --extraction /tmp/audit_workspace/com.example.app_extracted
```

## Rizin reference

Binary extraction uses **Rizin** (`rz-bin`). All commands assume `rz-bin`
is on PATH — on Windows use the full path to `rz-bin.exe`.

```bash
# Strings (the workhorse)
rz-bin -z target.so              # data section strings
rz-bin -zz target.so             # all strings (noisier but more complete)
rz-bin -zz -N 8:256 target.so    # filter 8-256 char length
rz-bin -qq -zz target.so         # quiet — content only, no addresses

# Imports / exports / linked libs
rz-bin -i target.so              # imported symbols
rz-bin -E target.so              # exported symbols (JNI entry points)
rz-bin -l target.so              # linked libraries

# Binary info
rz-bin -I target.so              # arch, format, endianness

# JSON output
rz-bin -j -z target.so
```

Tips:

- **DEX files**: Rizin handles Dalvik natively — no `dex2jar`/`jadx` needed for strings.
- **arm64 over armv7**: when both are present, analyze arm64 — same strings, better symbols.
- **resources.arsc**: often the fastest path to agency/tenant identifiers.
- **Windows encoding**: `subprocess.run(..., capture_output=True)` then
  `stdout.decode("utf-8", errors="replace")` — `resources.arsc` has binary bytes.

## Report schema

```json
{
  "target": "com.example.app",
  "platform": "ocv|custom|socrata|...",
  "audit_date": "YYYY-MM-DD",
  "findings": [
    {
      "name": "Firebase Analytics (Google Analytics for Firebase)",
      "category": "analytics",
      "risk": "medium",
      "sdk_version": "...",
      "evidence": [{"file": "classes.dex", "match": "..."}],
      "endpoints": ["https://..."],
      "data_collected": ["..."]
    }
  ],
  "permissions": [{"name": "android.permission.ACCESS_FINE_LOCATION", "risk": "high"}],
  "hardcoded_secrets": [{"kind": "firebase_app_id", "value": "...", "file": "..."}],
  "summary": {
    "overall_risk_rating": "LOW|MEDIUM|HIGH|CRITICAL",
    "total_trackers_found": 12,
    "narrative": "Plain-English summary for a non-technical reader."
  }
}
```

## Reference audits

The `examples/app_audit/witnesses/` directory ships with witness
artifacts from 8 previously-audited OCV platform apps (county community
safety apps). Inspect them to see what a complete
`report + trace + witness` bundle looks like, or use them as regression
fixtures when modifying the module.

## Do not

- Commit APKs, extraction directories, or Rizin output to the repo.
- Probe live endpoints aggressively — this skill analyzes local bytes.
- Assume one app's findings apply to others on the same platform — audit each.
- Reuse an old `trace.jsonl` — the emitter appends, so chain integrity breaks.
