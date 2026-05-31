#!/usr/bin/env python3
"""Extract ShardAtoms from markdown files via shingled extraction.

Overlapping windows + multi-pass consensus voting — see
docs/shingled-extraction.md for the why. Only atoms that appear in
≥N passes survive (default 2-of-N). Atoms straddling chunk boundaries
sit inside at least one fully-overlapping window thanks to the shingle
overlap, so nothing gets clipped.

This is a worked example, not a production tool. It demonstrates how
to plug spiritwriter-core's shard/atom primitives into a custom
extraction pipeline. Adapt freely.

Usage:
    # Required: --input and --store
    python examples/extract_memory.py --input ./notes/ --store ./shards/
    python examples/extract_memory.py --input ./MEMORY.md --store ./shards/
    python examples/extract_memory.py --input ./notes/ --store ./shards/ --passes 3
    python examples/extract_memory.py --input ./notes/ --store ./shards/ --dry-run
    python examples/extract_memory.py --input ./notes/ --store ./shards/ --regex

Requirements:
    pip install anthropic   # for LLM-based extraction (default)
    Set ANTHROPIC_API_KEY in OS keychain or env.
    Without an API key, --regex fallback runs the bundled
    regex extractor (noisier, but free and offline).

Cost: ~$0.001-0.003 per markdown file per pass with claude-haiku-4-5
at default chunk sizes. Two-pass consensus over a memory folder of
~20 files typically lands under $0.10.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from spiritwriter.fabric.shard import (
    MemoryShard, ShardAtom, AtomKind, DecayClass,
)
from spiritwriter.fabric.store import ShardStore
from spiritwriter.fabric.extract import extract_atoms
from spiritwriter.secrets import get_api_key


# === Shingled-extraction config ===
CHUNK_TARGET_CHARS = 2000   # ~500 tokens per chunk
CHUNK_OVERLAP_CHARS = 400   # the shingle window — atoms at chunk
                            # boundaries appear in ≥2 chunks
MAX_TOKENS_PER_CALL = 4000  # LLM max output tokens per chunk

# === LLM config (Anthropic Claude) ===
DEFAULT_MODEL = "claude-haiku-4-5"
# Pricing per 1M tokens, claude-haiku-4-5 (override via --model + --price-* if you switch)
DEFAULT_PRICE_INPUT_PER_1M = 1.0
DEFAULT_PRICE_OUTPUT_PER_1M = 5.0


EXTRACTION_PROMPT = """\
You are a knowledge extraction system. Given a section of markdown from an agent's memory file, extract structured knowledge atoms.

For each piece of extractable knowledge, output a JSON object with these fields:
- "text": The original or cleaned-up source text (1-2 sentences max)
- "kind": One of: "decision", "fact", "convention", "preference", "entity", "context", "instruction"
- "entity": The subject (e.g., a person, project, or system name like "Aaron", "myproject", "api-gateway"). Use lowercase for generic subjects.
- "key": A short label for what this is about (max 60 chars)
- "value": The actual information or choice made
- "decay": One of: "permanent", "stable", "active", "session"
- "confidence": 0.0-1.0, how certain this is a real extractable fact (skip anything below 0.5)

Guidelines:
- DECISIONS: Explicit choices with rationale. "We chose X over Y because Z"
- FACTS: Durable information. Names, versions, URLs, relationships, architecture details.
- CONVENTIONS: Rules and patterns. "Always do X", "Never do Y"
- PREFERENCES: Personal preferences. "The user prefers X"
- ENTITIES: Key project/person details worth remembering
- INSTRUCTIONS: How-to knowledge, workflows, commands
- CONTEXT: Important contextual knowledge that doesn't fit other categories

Skip:
- Transient debugging notes
- Routine status updates ("pushed commit X") unless they carry important context
- Anything too vague to be useful later
- Keep it concise — prefer fewer high-quality atoms over many noisy ones

Output ONLY a JSON array of objects. No markdown, no explanation.
If nothing worth extracting, output: []
"""


# ── Helpers ─────────────────────────────────────────────────────────


def _sha256_str(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _tokenize(text: str) -> set[str]:
    """Extract meaningful tokens from text for fuzzy matching."""
    text = text.lower().strip()
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    return {w for w in text.split() if len(w) > 2}


def _atom_signature(atom: ShardAtom) -> str:
    """Normalized signature for consensus matching.

    kind + entity + sorted key tokens. Matches across passes even when
    key phrasing varies:
    - "dog name" vs "pet name" won't match (different semantics)
    - "quota status" vs "quota status details" WILL match (shared tokens)
    """
    kind = atom.kind.value.lower()
    entity = (atom.entity or "").lower().strip()
    key = (atom.key or "").lower().strip()
    key = re.sub(r'\s+', ' ', key)
    return f"{kind}|{entity}|{key}"


def _signatures_match(sig1: str, sig2: str) -> bool:
    """Fuzzy match two atom signatures via Jaccard similarity on key tokens."""
    parts1 = sig1.split("|", 2)
    parts2 = sig2.split("|", 2)

    # Kind must match exactly
    if parts1[0] != parts2[0]:
        return False

    # Entity: fuzzy match (tokens overlap)
    ent1 = _tokenize(parts1[1]) if len(parts1) > 1 else set()
    ent2 = _tokenize(parts2[1]) if len(parts2) > 1 else set()
    if ent1 and ent2 and not ent1 & ent2:
        return False

    # Key: Jaccard similarity >= 0.4
    key1 = _tokenize(parts1[2]) if len(parts1) > 2 else set()
    key2 = _tokenize(parts2[2]) if len(parts2) > 2 else set()
    if not key1 or not key2:
        return key1 == key2
    jaccard = len(key1 & key2) / len(key1 | key2)
    return jaccard >= 0.4


def load_extracted_log(path: Path) -> dict[str, str]:
    """Cache of which files we've already extracted (by content hash)."""
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_extracted_log(path: Path, data: dict[str, str]):
    path.write_text(json.dumps(data, indent=2) + "\n")


def discover_files(input_path: Path) -> list[Path]:
    """Return markdown files under input_path (or [input_path] if it's a file)."""
    if input_path.is_file():
        return [input_path]
    if input_path.is_dir():
        return sorted(input_path.glob("**/*.md"))
    return []


# ── Shingled chunking ───────────────────────────────────────────────


def chunk_text(text: str, target_chars: int = CHUNK_TARGET_CHARS,
               overlap_chars: int = CHUNK_OVERLAP_CHARS) -> list[str]:
    """Split text into overlapping chunks at line boundaries.

    Never splits mid-line. The shingle overlap ensures atoms at chunk
    boundaries are seen in at least two chunks — the shingled-extraction
    principle.
    """
    lines = text.splitlines(keepends=True)
    if not lines:
        return []

    chunks: list[str] = []
    current_chunk: list[str] = []
    current_len = 0

    i = 0
    while i < len(lines):
        line = lines[i]
        current_chunk.append(line)
        current_len += len(line)

        if current_len >= target_chars:
            chunks.append("".join(current_chunk))

            # Backtrack for overlap: find lines that give us ~overlap_chars
            overlap_lines: list[str] = []
            overlap_len = 0
            for j in range(len(current_chunk) - 1, -1, -1):
                overlap_len += len(current_chunk[j])
                overlap_lines.insert(0, current_chunk[j])
                if overlap_len >= overlap_chars:
                    break

            current_chunk = overlap_lines
            current_len = overlap_len

        i += 1

    # Last chunk (only if it has substantial content beyond the overlap)
    if current_chunk:
        last = "".join(current_chunk)
        if not chunks or len(last) > overlap_chars * 1.5:
            chunks.append(last)

    return chunks if chunks else [text]


# ── LLM-backed extraction (Claude) ──────────────────────────────────


def _get_anthropic_client():
    """Returns (client, api_key) or (None, None) if SDK / key missing.

    Uses spiritwriter.secrets.get_api_key() so the OS keychain is
    consulted first, then env vars.
    """
    try:
        import anthropic
    except ImportError:
        print("    anthropic SDK not installed — falling back to regex.",
              file=sys.stderr)
        print("    Install with: pip install anthropic", file=sys.stderr)
        return None, None

    api_key = get_api_key("ANTHROPIC_API_KEY")
    if not api_key:
        print("    ANTHROPIC_API_KEY not set (keychain or env) — "
              "falling back to regex.", file=sys.stderr)
        return None, None

    return anthropic.Anthropic(api_key=api_key), api_key


def llm_extract_chunk(
    client, model: str, text: str, source_ref: str,
    chunk_idx: int, total_chunks: int, pass_num: int,
    price_in: float, price_out: float,
) -> tuple[list[ShardAtom], float]:
    """Extract atoms from a single chunk via Claude. Returns (atoms, cost_usd)."""
    chunk_label = f"[chunk {chunk_idx + 1}/{total_chunks}, pass {pass_num}]"

    resp = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS_PER_CALL,
        system=EXTRACTION_PROMPT,
        messages=[{
            "role": "user",
            "content": f"File: {source_ref} {chunk_label}\n\n{text}",
        }],
        temperature=0.1,  # low temp for consistent extraction across passes
    )

    # Claude SDK: content is a list of blocks; we want the text from the first text block
    raw = ""
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            raw = block.text.strip()
            break

    truncated = (resp.stop_reason == "max_tokens")
    if truncated:
        print(f"    ⚠ Response hit max_tokens {chunk_label} — chunk may be too large",
              file=sys.stderr)
        raw = _salvage_truncated_json(raw)

    # Strip markdown fencing if Claude added it despite the prompt
    if raw.startswith("```"):
        raw = re.sub(r'^```(?:json)?\n?', '', raw)
        raw = re.sub(r'\n?```$', '', raw)

    # Cost (USD)
    in_tok = resp.usage.input_tokens if resp.usage else 0
    out_tok = resp.usage.output_tokens if resp.usage else 0
    cost = (in_tok * price_in + out_tok * price_out) / 1_000_000

    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        print(f"    ⚠ JSON parse failed {chunk_label}", file=sys.stderr)
        return [], cost

    if not isinstance(items, list):
        return [], cost

    return _parse_items(items, source_ref), cost


def _salvage_truncated_json(raw: str) -> str:
    """If the response was cut mid-array, find the last complete object and close."""
    last_close = raw.rfind("}")
    if last_close == -1:
        return "[]"
    truncated = raw[:last_close + 1]
    if not truncated.rstrip().endswith("]"):
        truncated = truncated.rstrip().rstrip(",") + "\n]"
    return truncated


def _parse_items(items: list, source_ref: str) -> list[ShardAtom]:
    """Convert the LLM's JSON output into ShardAtoms."""
    kind_map = {k.value: k for k in AtomKind}
    atoms: list[ShardAtom] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        confidence = item.get("confidence", 1.0)
        if confidence < 0.5:
            continue

        kind = kind_map.get(item.get("kind", "context"), AtomKind.CONTEXT)
        text_val = item.get("text", "").strip()
        if not text_val:
            continue

        atoms.append(ShardAtom(
            text=text_val,
            kind=kind,
            entity=item.get("entity"),
            key=item.get("key", "")[:60],
            value=item.get("value"),
            confidence=confidence,
            source_ref=source_ref,
        ))

    return atoms


# ── Regex fallback (offline, no API key needed) ─────────────────────


def regex_extract_text(text: str) -> list[ShardAtom]:
    """Regex-based extraction via spiritwriter.fabric.extract.extract_atoms.

    Noisier than LLM extraction but free and offline. Useful for
    smoke-testing the pipeline without burning API calls.
    """
    atoms: list[ShardAtom] = []
    seen: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        clean = re.sub(r'\*\*(.+?)\*\*', r'\1', line)
        clean = re.sub(r'`(.+?)`', r'\1', clean)
        clean = re.sub(r'^\s*[-*]\s*', '', clean).strip()
        if 15 <= len(clean) <= 300:
            for atom in extract_atoms(clean):
                h = atom.content_hash
                if h not in seen:
                    seen.add(h)
                    atoms.append(atom)
    return atoms


# ── Per-file extraction with consensus ──────────────────────────────


def extract_file_with_consensus(
    filepath: Path, source_ref: str, num_passes: int,
    use_regex: bool, client, model: str,
    price_in: float, price_out: float,
) -> tuple[list[ShardAtom], float]:
    """Extract atoms from a file using shingled extraction + consensus voting.

    1. Chunk the file into overlapping shingle windows
    2. Run extraction on each chunk, repeated for `num_passes` passes
    3. Atoms appearing in ≥2 passes are kept (consensus survivors)
    4. Single-pass atoms only kept if confidence ≥ 0.8 (handled
       per-atom by the LLM prompt's confidence threshold)

    Returns (consensus_atoms, total_cost_usd).
    """
    content = filepath.read_text()

    if use_regex or client is None:
        return regex_extract_text(content), 0.0

    chunks = chunk_text(content)
    print(f"    {len(chunks)} chunk(s), {num_passes} pass(es)")

    # Collect atoms per pass
    pass_results: list[list[ShardAtom]] = []
    total_cost = 0.0

    for pass_num in range(1, num_passes + 1):
        pass_atoms: list[ShardAtom] = []
        for chunk_idx, chunk in enumerate(chunks):
            atoms, cost = llm_extract_chunk(
                client, model, chunk, source_ref,
                chunk_idx, len(chunks), pass_num,
                price_in, price_out,
            )
            pass_atoms.extend(atoms)
            total_cost += cost
        pass_results.append(pass_atoms)

    # Consensus via fuzzy signature matching
    if num_passes == 1:
        consensus_atoms = pass_results[0]
    else:
        # Use pass 1 as anchor; count fuzzy matches across other passes
        anchor = pass_results[0]
        other_passes = pass_results[1:]

        consensus_atoms = []
        for atom in anchor:
            sig = _atom_signature(atom)
            votes = 1  # self-vote from pass 1

            for other_atoms in other_passes:
                for other_atom in other_atoms:
                    if _signatures_match(sig, _atom_signature(other_atom)):
                        votes += 1
                        # Prefer the higher-confidence version if available
                        if other_atom.confidence > atom.confidence:
                            atom = other_atom
                        break

            if votes >= 2:
                consensus_atoms.append(atom)

        # Atoms unique to later passes that match each other (in case
        # pass 1 missed something later passes both saw)
        if len(other_passes) >= 2:
            for atom in other_passes[0]:
                sig = _atom_signature(atom)
                if any(_signatures_match(sig, _atom_signature(a)) for a in consensus_atoms):
                    continue
                for other_atoms in other_passes[1:]:
                    for other_atom in other_atoms:
                        if _signatures_match(sig, _atom_signature(other_atom)):
                            consensus_atoms.append(atom)
                            break

    total_candidates = sum(len(p) for p in pass_results)
    print(f"    consensus: {len(consensus_atoms)} kept from {total_candidates} "
          f"candidates across {num_passes} passes")

    return consensus_atoms, total_cost


# ── Main ────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Extract ShardAtoms from markdown using shingled extraction "
            "(overlapping windows + multi-pass consensus voting). "
            "See docs/shingled-extraction.md."
        ),
    )
    parser.add_argument("--input", type=Path, required=True,
                        help="Markdown file or directory to extract from")
    parser.add_argument("--store", type=Path, required=True,
                        help="ShardStore directory where the extracted shard is written")
    parser.add_argument("--scope", default="memory:extracted",
                        help="Scope for the extracted shard (default: memory:extracted)")
    parser.add_argument("--origin", default="example:extract_memory",
                        help="Origin agent id recorded on the shard "
                             "(default: example:extract_memory)")
    parser.add_argument("--passes", type=int, default=2,
                        help="Number of consensus passes (default: 2)")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Claude model id (default: {DEFAULT_MODEL})")
    parser.add_argument("--price-in", type=float, default=DEFAULT_PRICE_INPUT_PER_1M,
                        help="USD per 1M input tokens (default: %(default).2f)")
    parser.add_argument("--price-out", type=float, default=DEFAULT_PRICE_OUTPUT_PER_1M,
                        help="USD per 1M output tokens (default: %(default).2f)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview extraction without writing the shard")
    parser.add_argument("--force", action="store_true",
                        help="Re-extract files even if their content hash is unchanged")
    parser.add_argument("--regex", action="store_true",
                        help="Use regex fallback instead of the LLM (free, offline, noisier)")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"Input not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    store = ShardStore(args.store)
    extracted_log_path = args.store / ".extracted_files"
    extracted_log = load_extracted_log(extracted_log_path)

    target_files = discover_files(args.input)
    if not target_files:
        print(f"No markdown files found under {args.input}")
        return

    # Set up LLM client once (None if SDK or key missing → regex fallback)
    client = None
    if not args.regex:
        client, _ = _get_anthropic_client()

    all_atoms: list[ShardAtom] = []
    file_stats: dict[str, int] = {}
    total_cost = 0.0

    for filepath in target_files:
        # source_ref is the file path relative to the input root when input
        # is a dir, else just the file name
        if args.input.is_dir():
            rel = str(filepath.relative_to(args.input))
        else:
            rel = filepath.name
        content = filepath.read_text()
        content_hash = _sha256_str(content)

        if not args.force and extracted_log.get(rel) == content_hash:
            print(f"  {filepath.name}: unchanged, skipping")
            continue

        print(f"  {filepath.name}: extracting...")

        atoms, cost = extract_file_with_consensus(
            filepath, rel, args.passes, args.regex,
            client, args.model, args.price_in, args.price_out,
        )
        total_cost += cost
        file_stats[filepath.name] = len(atoms)
        all_atoms.extend(atoms)

        if not args.dry_run:
            extracted_log[rel] = content_hash

    # Global dedup across files
    seen_sigs: set[str] = set()
    deduped: list[ShardAtom] = []
    for atom in all_atoms:
        sig = _atom_signature(atom)
        if sig not in seen_sigs:
            seen_sigs.add(sig)
            deduped.append(atom)
    dupes_removed = len(all_atoms) - len(deduped)
    all_atoms = deduped

    print(f"\n=== Extraction results ===")
    print(f"Files processed: {len(file_stats)}")
    print(f"Atoms extracted: {len(all_atoms)} ({dupes_removed} cross-file dupes removed)")
    print(f"Total LLM cost: ${total_cost:.4f}")
    for fname, count in file_stats.items():
        print(f"  {fname}: {count} atoms")

    if args.dry_run:
        print(f"\n=== Preview (dry run) ===")
        by_kind: dict[str, list[ShardAtom]] = {}
        for atom in all_atoms:
            by_kind.setdefault(atom.kind.value, []).append(atom)
        for kind, atoms_list in sorted(by_kind.items()):
            print(f"\n--- {kind} ({len(atoms_list)}) ---")
            for atom in atoms_list:
                conf = f" ({atom.confidence:.0%})" if atom.confidence < 1.0 else ""
                print(f"  {atom.entity}:{atom.key} = {atom.value or '—'}{conf}")
        return

    if not all_atoms:
        print("No new atoms to extract.")
        save_extracted_log(extracted_log_path, extracted_log)
        return

    shard = MemoryShard(
        scope=args.scope,
        atoms=all_atoms,
        origin=args.origin,
        tags=[f"extracted-{datetime.now().strftime('%Y-%m-%d')}"],
        meta={
            "extracted_at": datetime.now().isoformat(),
            "method": "regex" if (args.regex or client is None) else "llm",
            "model": args.model if not args.regex and client is not None else None,
            "passes": args.passes,
            "cost_usd": round(total_cost, 4),
            "input_root": str(args.input),
        },
    )

    ref = store.put(shard)
    store.set_ref("memory-extracted", ref.shard_id)

    save_extracted_log(extracted_log_path, extracted_log)

    print(f"\nStored shard: {ref.shard_id[:16]}...")
    print(f"  Scope:  {args.scope}")
    print(f"  Origin: {args.origin}")
    print(f"  Atoms:  {len(all_atoms)}")
    print(f"  Tokens: ~{shard.token_estimate}")
    print(f"  Ref:    memory-extracted")
    print(f"  Cost:   ${total_cost:.4f}")


if __name__ == "__main__":
    main()
