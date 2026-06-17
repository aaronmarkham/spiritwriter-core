"""Redact audit witness bundles for safe public release.

Part of the audit lifecycle:  seed -> provenance -> verify -> **redact**.

Produces a clean, internally-consistent PUBLIC copy of an audit tree
(report.json + trace.jsonl + witness.json per dir) while the full,
unredacted originals stay private. Designed so anyone auditing the apps
in their own state can publish what they find without leaking live
credentials or handing out a ready-made target map.

Model
-----
Every embedded secret/identifier is replaced with a deterministic token
``<REDACTED:class fp:xxxxxxxxxxxx>`` where the fingerprint is
``sha256(value)[:12]``. Because it is deterministic, a value shared across
apps (e.g. a reused Google API key) maps to the SAME token everywhere —
the "shared across N apps" finding survives; the live value does not.

Evidence hashes are KEPT (apk_sha256, extracted-file hashes, DEX corpus
hash): they are not secrets, and they let a holder of the private APK
verify L3 against the public witness.

After redacting, the tree is re-chained with the project's own canonical
hash primitives (so L2/verify_chain passes) and each witness is
regenerated via ``generate_witness`` (so L1 passes).

Fail-closed safety gate
-----------------------
``redact_tree`` re-scans its own output. It distinguishes:

  * REDACT classes  — patterns it fingerprints automatically.
  * DETECT-ONLY classes — secret shapes it does NOT auto-handle
    (AWS access keys, private-key blocks, provider tokens, ...). If any
    appear in the output, the run is reported UNSAFE and the CLI exits
    non-zero. Better to refuse than to greenlight a leak.

LIMITS — read before trusting this
----------------------------------
This is best-effort, pattern-based redaction of KNOWN shapes. It cannot
recognise every custom secret format. ALWAYS eyeball the output. It also
does NOT rewrite git history (values already committed remain in history —
use git-filter-repo/BFG and/or coordinate key rotation), and it is not a
substitute for responsible disclosure to the vendor.

Usage::

    python -m spiritwriter.audit.redact SRC_DIR OUT_DIR
    python -m spiritwriter.audit.redact SRC_DIR OUT_DIR --strict
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import sys
from pathlib import Path

from spiritwriter.audit.provenance import generate_witness
from spiritwriter.fabric.emitter import verify_chain
from spiritwriter.fabric.shard import _canonical_json, _sha256

__all__ = [
    "scrub_text",
    "redact_report",
    "rechain",
    "redact_dir",
    "redact_tree",
    "residual_scan",
    "short_flagged_values",
    "_literal_patterns",
    "REDACT_PATTERNS",
    "DETECT_ONLY_PATTERNS",
]


# Minimum length for a flagged value to be swept from free text. Shorter
# values are too risky to sweep verbatim (they would rewrite common fragments
# in prose); they are still tokenized in their structured field.
_MIN_LITERAL_LEN = 8


def _fp(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _token(cls: str, value: str) -> str:
    return f"<REDACTED:{cls} fp:{_fp(value)}>"


# Classes the redactor fingerprints automatically. Ordered: specific first.
# Note: 64-hex file/APK/corpus hashes are intentionally NOT here, so they
# are preserved for L3 verification against the private original.
REDACT_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z\-_]{35}")),
    ("aws_cognito_identity_pool",
     re.compile(r"\b[a-z]{2}-[a-z]+-\d:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")),
    ("aws_cognito_user_pool", re.compile(r"\b[a-z]{2}-[a-z]+-\d_[A-Za-z0-9]{8,}\b")),
    # Host-based patterns accept an OPTIONAL scheme so a bare-host mention in
    # prose (e.g. "yiqvcldlxa.execute-api.us-east-1.amazonaws.com") is caught,
    # not just the full https:// endpoint form. The trailing ``[^\s"']*`` greedily
    # consumes the path/query, so an endpoint not delimited by whitespace/quotes
    # (e.g. "...amazonaws.com/dev,more") may swallow adjacent prose. This errs
    # toward over-redaction, so it stays fail-safe.
    ("aws_api_gateway",
     re.compile(r"(?:https?://)?[a-z0-9]+\.execute-api\.[a-z0-9-]+\.amazonaws\.com[^\s\"']*")),
    ("firebase_app_id", re.compile(r"\b\d:\d{6,}:android:[0-9a-f]+\b")),
    ("firebase_rtdb", re.compile(r"(?:https?://)?[a-z0-9-]+\.firebaseio\.com[^\s\"']*")),
    ("firebase_bucket",
     re.compile(r"\b[a-z0-9][a-z0-9._-]*\.(?:firebasestorage\.app|appspot\.com)\b")),
    ("oauth_client_id", re.compile(r"\b\d+-[a-z0-9]+\.apps\.googleusercontent\.com\b")),
]
# Target-specific endpoints (a single audit subject's CDN host, tenant domain,
# etc.) are deliberately NOT baked in here — this module is platform-agnostic.
# Such values are still removed everywhere they appear via the literal sweep
# (``_literal_patterns``), driven by the audit's own ``hardcoded_secrets`` list.

# Secret shapes we DETECT but do not auto-redact. Their presence in output
# means a class this tool doesn't handle slipped through — fail closed.
DETECT_ONLY_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("private_key_block", re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----")),
    ("slack_token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")),
    ("github_token", re.compile(r"\bghp_[0-9A-Za-z]{36}\b")),
    ("stripe_live_key", re.compile(r"\bsk_live_[0-9A-Za-z]{16,}\b")),
    ("generic_bearer", re.compile(r"\b[A-Za-z0-9_-]*(?:secret|token|password)[A-Za-z0-9_-]*\s*[=:]\s*[A-Za-z0-9._\-]{12,}")),
]


def scrub_text(s: str, literals: tuple[tuple[re.Pattern, str], ...] = ()) -> str:
    if not s:
        return s
    for cls, pat in REDACT_PATTERNS:
        s = pat.sub(lambda m, _c=cls: _token(_c, m.group(0)), s)
    for pat, token in literals:
        s = pat.sub(lambda m, _t=token: _t, s)
    return s


def _scrub_obj(obj, literals: tuple[tuple[re.Pattern, str], ...] = ()):
    if isinstance(obj, dict):
        return {k: _scrub_obj(v, literals) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub_obj(v, literals) for v in obj]
    if isinstance(obj, str):
        return scrub_text(obj, literals)
    return obj


def _class_for_value(value: str) -> str:
    """Pattern class for a structured secret value (consistent tokens)."""
    for cls, pat in REDACT_PATTERNS:
        if pat.search(value):
            return cls
    return "embedded_secret"


def _literal_patterns(report: dict) -> list[tuple[re.Pattern, str]]:
    """Exact-string redactors for every structured secret value in the report.

    The regex patterns only know a handful of secret shapes; the audit's own
    ``hardcoded_secrets`` list is the authoritative set of values to remove.
    Without this, a value whose shape no pattern matches gets its structured
    ``value`` field tokenized while the identical string survives verbatim in
    a finding's evidence ``match``, a trace ``evidence_strings`` entry, the
    narrative, or another secret's notes — redacted in one place, leaked two
    lines away.

    Each flagged value becomes its deterministic token (same class + fp the
    structured field uses, so occurrences tokenize identically and the
    shared-across-apps signal survives). Lookarounds bound every match to a
    non-alphanumeric neighborhood, so a value can never match inside a longer
    token — in particular it can never corrupt a preserved 64-hex evidence
    hash.

    Values shorter than ``_MIN_LITERAL_LEN`` are skipped here as a guard
    against catastrophic over-matching (a 2–3 char "secret" would rewrite
    every occurrence of that fragment in prose). Such a value is still
    tokenized in its structured ``value`` field by ``redact_report``, but it is
    NOT swept from free text — so it can survive verbatim in evidence, the
    narrative, or the trace. ``redact_dir`` surfaces any such values via its
    ``short_unswept`` count so the human reviewer knows to check them by eye.
    Use ``short_flagged_values`` to enumerate them.
    """
    pats: list[tuple[re.Pattern, str]] = []
    seen: set[str] = set()
    for s in report.get("hardcoded_secrets", []):
        v = s.get("value")
        if isinstance(v, str) and len(v) >= _MIN_LITERAL_LEN and v not in seen:
            seen.add(v)
            token = _token(_class_for_value(v), v)
            pat = re.compile(r"(?<![0-9A-Za-z])" + re.escape(v) + r"(?![0-9A-Za-z])")
            pats.append((pat, token))
    return pats


def short_flagged_values(report: dict) -> list[str]:
    """Flagged secret values too short for the literal sweep (see ``_MIN_LITERAL_LEN``).

    These are tokenized in their structured ``value`` field but not removed
    from free text, so a reviewer should confirm they don't appear verbatim
    elsewhere.
    """
    seen: set[str] = set()
    short: list[str] = []
    for s in report.get("hardcoded_secrets", []):
        v = s.get("value")
        if isinstance(v, str) and 0 < len(v) < _MIN_LITERAL_LEN and v not in seen:
            seen.add(v)
            short.append(v)
    return short


def redact_report(report: dict, literals: list[tuple[re.Pattern, str]] | None = None) -> dict:
    """Fingerprint structured secrets, neutralize exploit notes, scrub rest.

    ``literals`` (exact-value redactors) default to those derived from this
    report. ``redact_dir`` passes a shared set so the report and its trace
    tokenize identical values identically.

    The input ``report`` is never mutated — it is deep-copied at entry, so a
    caller reusing the original dict keeps its live values.
    """
    if literals is None:
        literals = _literal_patterns(report)
    report = copy.deepcopy(report)
    for s in report.get("hardcoded_secrets", []):
        if isinstance(s.get("value"), str):
            s["value"] = _token(_class_for_value(s["value"]), s["value"])
        # Keep the human-readable type as a label; replace exploit framing.
        s["notes"] = (
            f"{s.get('type', 'Embedded identifier')} present in the APK; "
            "value redacted, fingerprint retained for cross-app correlation. "
            "Full value and location available in the private witness."
        )
    return _scrub_obj(report, literals)


def rechain(events: list[dict], new_report_sha: str | None,
            literals: tuple[tuple[re.Pattern, str], ...] = ()) -> list[dict]:
    """Scrub event payloads and recompute the hash chain in order."""
    prev = None
    out = []
    for evt in events:
        evt = _scrub_obj(evt, literals)
        if evt.get("type") == "audit_report_generated" and new_report_sha:
            evt["report_sha256"] = new_report_sha
        evt.pop("hash", None)
        evt.pop("sig", None)
        evt["prev_event_hash"] = prev
        hashable = {k: v for k, v in evt.items() if k not in ("hash", "sig")}
        h = _sha256(_canonical_json(hashable))
        evt["hash"] = h
        prev = h
        out.append(evt)
    return out


def redact_dir(src: Path, dst: Path) -> dict:
    """Redact one audit dir; returns {chain_ok, events, secrets, short_unswept}.

    Requires both ``report.json`` and ``trace.jsonl`` in ``src``. Both are
    checked up front so a dir missing its trace fails before any output is
    written (no half-redacted bundle left behind).
    """
    src = Path(src)
    report_src, trace_src = src / "report.json", src / "trace.jsonl"
    if not report_src.exists():
        raise FileNotFoundError(f"no report.json in {src}")
    if not trace_src.exists():
        raise FileNotFoundError(f"no trace.jsonl beside {report_src}")
    dst.mkdir(parents=True, exist_ok=True)
    raw_report = json.loads(report_src.read_text(encoding="utf-8"))
    # Derive exact-value redactors from the audit's own secret list, then use
    # the SAME set for report and trace so every flagged value tokenizes
    # identically everywhere it appears.
    literals = _literal_patterns(raw_report)
    report = redact_report(raw_report, literals)
    report_path = dst / "report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    new_report_sha = hashlib.sha256(report_path.read_bytes()).hexdigest()

    events = [
        json.loads(line)
        for line in trace_src.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    events = rechain(events, new_report_sha, literals)
    trace_path = dst / "trace.jsonl"
    with open(trace_path, "w", encoding="utf-8") as f:
        for evt in events:
            f.write(json.dumps(evt, ensure_ascii=False) + "\n")

    witness = generate_witness(trace_path, report_path)
    (dst / "witness.json").write_text(
        json.dumps(witness, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return {"chain_ok": verify_chain(events), "events": len(events),
            "secrets": len(report.get("hardcoded_secrets", [])),
            "short_unswept": short_flagged_values(raw_report)}


def _shannon(s: str) -> float:
    if not s:
        return 0.0
    counts = {c: s.count(c) for c in set(s)}
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def residual_scan(root: Path, strict: bool = False) -> list[tuple[str, str, str]]:
    """Re-scan output for leaks. Returns (file, class, sample) findings.

    Flags any REDACT pattern that survived (a redactor bug) and any
    DETECT_ONLY class (an unhandled secret shape). With ``strict``, also
    flags high-entropy alphanumeric runs that look key-like and aren't
    redaction tokens or evidence hashes (advisory; may false-positive).
    """
    findings: list[tuple[str, str, str]] = []
    hexhash = re.compile(r"\b[0-9a-f]{32,64}\b")
    keyish = re.compile(r"\b[A-Za-z0-9_\-]{20,}\b")
    # The redactor's own tokens (``<REDACTED:class fp:...>``) must never trip a
    # gate. They don't today only because of the space before ``fp:``; strip the
    # spans up front so the gate stays correct even if the token format changes.
    redacted_span = re.compile(r"<REDACTED:[^>]*>")
    for fp in root.rglob("*"):
        if not fp.is_file():
            continue
        raw = fp.read_text(encoding="utf-8", errors="replace")
        text = redacted_span.sub("", raw)
        rel = str(fp.relative_to(root))
        for cls, pat in REDACT_PATTERNS:
            for m in pat.finditer(text):
                findings.append((rel, f"residual:{cls}", m.group(0)[:48]))
        for cls, pat in DETECT_ONLY_PATTERNS:
            for m in pat.finditer(text):
                findings.append((rel, f"unhandled:{cls}", m.group(0)[:48]))
        if strict:
            for m in keyish.finditer(text):
                tok = m.group(0)
                if tok.startswith("REDACTED") or hexhash.fullmatch(tok):
                    continue
                if _shannon(tok) >= 3.9:
                    findings.append((rel, "strict:high_entropy", tok[:48]))
    return findings


def redact_tree(src: Path, out: Path, strict: bool = False) -> dict:
    """Redact every audit dir under ``src`` into ``out``; gate on residuals.

    A ``report.json`` with no sibling ``trace.jsonl`` is skipped (recorded in
    ``skipped``) rather than aborting the whole tree mid-run with a partial
    bundle on disk.
    """
    src, out = Path(src), Path(out)
    audit_dirs = sorted({p.parent for p in src.rglob("report.json")})
    results = {}
    skipped = {}
    for d in audit_dirs:
        rel = str(d.relative_to(src))
        if not (d / "trace.jsonl").exists():
            skipped[rel] = "no trace.jsonl beside report.json"
            continue
        results[rel] = redact_dir(d, out / rel)
    leaks = residual_scan(out, strict=strict)
    chain_ok = all(r["chain_ok"] for r in results.values())
    return {"dirs": results, "skipped": skipped, "leaks": leaks,
            "safe": chain_ok and not leaks}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="Source dir (scanned recursively for report.json)")
    ap.add_argument("out", help="Output dir for the redacted public copy")
    ap.add_argument("--strict", action="store_true", help="Also flag high-entropy key-like strings (advisory)")
    args = ap.parse_args()

    if not list(Path(args.src).rglob("report.json")):
        print(f"No report.json found under {args.src}")
        sys.exit(2)

    summary = redact_tree(args.src, args.out, strict=args.strict)
    for rel, r in summary["dirs"].items():
        flag = "OK " if r["chain_ok"] else "FAIL-CHAIN"
        print(f"  [{flag}] {rel}  ({r['events']} events, {r['secrets']} secrets fingerprinted)")
        for v in r.get("short_unswept", []):
            print(f"    [WARN] flagged value too short to sweep from free text "
                  f"(<{_MIN_LITERAL_LEN} chars) — verify by eye: {v!r}")

    for rel, why in summary.get("skipped", {}).items():
        print(f"  [SKIP] {rel}  ({why})")

    if summary["leaks"]:
        print(f"\nUNSAFE — {len(summary['leaks'])} residual finding(s):")
        for rel, cls, sample in summary["leaks"][:40]:
            print(f"    {cls:28} {rel}  ::  {sample}")
        print("\nDo NOT publish this output. Resolve the findings above first")
        print("(unhandled classes need a manual pass or a new REDACT pattern).")
        sys.exit(1)

    print("\nCLEAN — no residual secrets detected; chain + L1/L2 intact.")
    print("Reminder: review by eye, scrub git history of originals, and")
    print("coordinate disclosure before publishing.")


if __name__ == "__main__":
    main()
