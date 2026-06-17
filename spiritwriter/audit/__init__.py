"""Android app security audit — hash-chained provenance for APK scans.

Produces tamper-evident audit trails for mobile app security analysis:

  APK + extracted files  →  TraceEmitter  →  trace.jsonl  (hash chain)
                        \\                 \\
                         report.json        witness.json (self-hashing)

A witness document binds the APK hash, the extracted-file hashes, every
finding derivation, and the final report into a single verifiable
artifact. Verification runs at four levels (L1 document integrity,
L2 chain integrity, L3 evidence provenance, L4 finding derivability).

Typical flow:

    from spiritwriter.audit import trace_existing_audit, load_registry

    # Canonicalize findings against the shared registry
    registry = load_registry()

    # Produce trace.jsonl + witness.json from an existing report
    trace_path, witness_path = trace_existing_audit(
        package_name="com.example.app",
        apk_path="/tmp/app.apk",
        extraction_dir="/tmp/app_extracted",
        report_path="./audits/MyCity/report.json",
    )

The audit module is platform-agnostic — any APK can be audited, and the
canonical findings registry ships with common tracking SDKs (Firebase,
AdMob, AWS Amplify, Appriss VINE, etc.) plus whatever new SDKs you seed.

Lifecycle:  seed -> provenance -> verify -> redact. The redact stage
(``spiritwriter.audit.redact``) produces an internally-consistent PUBLIC
copy of an audit tree with embedded secrets replaced by deterministic
``<REDACTED:class fp:...>`` tokens, evidence hashes preserved (L3 still
verifies), and a fail-closed residual gate over the output.
"""

from spiritwriter.audit.provenance import (
    emit_audit_evidence,
    emit_audit_finding,
    emit_audit_input,
    emit_audit_report,
    emit_audit_strings,
    generate_witness,
    trace_existing_audit,
)
from spiritwriter.audit.registry import (
    AUDIT_FINDING_SCHEMA,
    canonical_finding_list,
    load_registry,
    validate_report,
)
from spiritwriter.audit.verify import verify, verify_l1, verify_l2, verify_l3, verify_l4

__all__ = [
    "AUDIT_FINDING_SCHEMA",
    "canonical_finding_list",
    "emit_audit_evidence",
    "emit_audit_finding",
    "emit_audit_input",
    "emit_audit_report",
    "emit_audit_strings",
    "generate_witness",
    "load_registry",
    "trace_existing_audit",
    "validate_report",
    "verify",
    "verify_l1",
    "verify_l2",
    "verify_l3",
    "verify_l4",
]
