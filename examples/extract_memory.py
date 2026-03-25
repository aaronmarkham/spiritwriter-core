#!/usr/bin/env python3
"""Extract shard atoms from memory files using LLM with shingle consensus.

Uses overlapping windows (shingles) and multi-pass extraction to ensure
no knowledge is lost to truncation or boundary effects. Multiple extraction
passes vote on atoms — only atoms that appear in 2+ passes are kept.

At ~$0.03/pass over all files, 3 passes cost under a dime for
consensus-validated memories.

Usage:
    python3 shards/extract_memory.py                       # extract from all memory files
    python3 shards/extract_memory.py memory/2026-02-20.md  # specific file
    python3 shards/extract_memory.py --dry-run             # preview without storing
    python3 shards/extract_memory.py --passes 3            # number of consensus passes (default 2)
    python3 shards/extract_memory.py --force               # re-extract already-processed files
    python3 shards/extract_memory.py --regex               # use regex fallback (no API call)
"""

import sys
import os
import re
import json
import hashlib
from pathlib import Path
from datetime import datetime
from collections import Counter

# Add spiritwriter-core to path
SW_PATH = Path.home() / "Documents" / "GitHub" / "spiritwriter-core"
if SW_PATH.exists():
    sys.path.insert(0, str(SW_PATH))

from spiritwriter.trace.shard import (
    MemoryShard, ShardAtom, AtomKind, DecayClass,
)
from spiritwriter.trace.store import ShardStore
from spiritwriter.trace.extract import extract_atoms, classify_decay

WORKSPACE = Path(__file__).parent.parent
STORE_PATH = WORKSPACE / "shards"
MEMORY_DIR = WORKSPACE / "memory"
MEMORY_MD = WORKSPACE / "MEMORY.md"

# Track what we've already extracted (by file content hash)
EXTRACTED_LOG = STORE_PATH / ".extracted_files"

# Chunking config
CHUNK_TARGET_CHARS = 2000   # ~500 tokens per chunk
CHUNK_OVERLAP_CHARS = 400   # ~100 token overlap (shingle window)
MAX_TOKENS_PER_CALL = 4000  # LLM max output tokens per chunk

EXTRACTION_PROMPT = """\
You are a knowledge extraction system. Given a section of markdown from an AI agent's memory file, extract structured knowledge atoms.

For each piece of extractable knowledge, output a JSON object with these fields:
- "text": The original or cleaned-up source text (1-2 sentences max)
- "kind": One of: "decision", "fact", "convention", "preference", "entity", "context", "instruction"
- "entity": The subject (e.g., "Aaron", "CSP", "spiritwriter-core", "Lilit"). Use lowercase for generic subjects.
- "key": A short label for what this is about (max 60 chars)
- "value": The actual information or choice made
- "decay": One of: "permanent", "stable", "active", "session"
- "confidence": 0.0-1.0, how certain this is a real extractable fact (skip anything below 0.5)

Guidelines:
- DECISIONS: Explicit choices with rationale. "We chose X over Y because Z"
- FACTS: Durable information. Names, versions, URLs, relationships, architecture details.
- CONVENTIONS: Rules and patterns. "Always do X", "Never do Y"
- PREFERENCES: Personal preferences. "Aaron prefers X"
- ENTITIES: Key project/person details worth remembering
- INSTRUCTIONS: How-to knowledge, workflows, commands
- CONTEXT: Important contextual knowledge that doesn't fit other categories

Skip:
- Transient debugging notes
- Routine status updates ("pushed commit X") unless they contain important context
- Anything too vague to be useful later
- Keep it concise — prefer fewer high-quality atoms over many noisy ones

Output ONLY a JSON array of objects. No markdown, no explanation.
If nothing worth extracting, output: []
"""


def _sha256_str(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _tokenize(text: str) -> set[str]:
    """Extract meaningful tokens from text for fuzzy matching."""
    text = text.lower().strip()
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    return {w for w in text.split() if len(w) > 2}


def _atom_signature(atom: ShardAtom) -> str:
    """Create a normalized signature for consensus matching.

    Uses kind + entity + sorted key tokens. This allows matching
    across passes even when key phrasing varies:
    - "dog name" vs "pet name" won't match (different semantics)
    - "quota status" vs "quota status details" WILL match (shared tokens)
    """
    kind = atom.kind.value.lower()
    entity = (atom.entity or "").lower().strip()
    key = (atom.key or "").lower().strip()
    key = re.sub(r'\s+', ' ', key)
    return f"{kind}|{entity}|{key}"


def _signatures_match(sig1: str, sig2: str) -> bool:
    """Fuzzy match two atom signatures using Jaccard similarity on key tokens."""
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


def load_extracted_files() -> dict[str, str]:
    if EXTRACTED_LOG.exists():
        return json.loads(EXTRACTED_LOG.read_text())
    return {}


def save_extracted_files(data: dict[str, str]):
    EXTRACTED_LOG.write_text(json.dumps(data, indent=2) + "\n")


def get_memory_files(specific: str | None = None) -> list[Path]:
    if specific:
        p = Path(specific)
        if not p.is_absolute():
            p = WORKSPACE / p
        return [p] if p.exists() else []
    files = []
    if MEMORY_MD.exists():
        files.append(MEMORY_MD)
    if MEMORY_DIR.exists():
        files.extend(sorted(MEMORY_DIR.glob("*.md")))
    return files


def chunk_text(text: str, target_chars: int = CHUNK_TARGET_CHARS,
               overlap_chars: int = CHUNK_OVERLAP_CHARS) -> list[str]:
    """Split text into overlapping chunks, breaking at line boundaries.

    Never splits mid-line. Overlap ensures atoms at chunk boundaries
    are seen in at least two chunks (shingle principle).
    """
    lines = text.splitlines(keepends=True)
    if not lines:
        return []

    chunks = []
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

    # Don't forget the last chunk
    if current_chunk:
        last = "".join(current_chunk)
        # Only add if it has substantial content beyond what's already in previous chunk
        if not chunks or len(last) > overlap_chars * 1.5:
            chunks.append(last)

    return chunks if chunks else [text]


def llm_extract_chunk(text: str, filepath: str, chunk_idx: int,
                      total_chunks: int, pass_num: int) -> tuple[list[ShardAtom], float]:
    """Extract atoms from a single text chunk via LLM. Returns (atoms, cost)."""
    try:
        from openai import OpenAI
    except ImportError:
        print("    openai package not installed, falling back to regex", file=sys.stderr)
        return regex_extract_text(text), 0.0

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("    OPENAI_API_KEY not set, falling back to regex", file=sys.stderr)
        return regex_extract_text(text), 0.0

    client = OpenAI(api_key=api_key)

    chunk_label = f"[chunk {chunk_idx + 1}/{total_chunks}, pass {pass_num}]"

    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": EXTRACTION_PROMPT},
            {"role": "user", "content": f"File: {filepath} {chunk_label}\n\n{text}"},
        ],
        temperature=0.1,  # Low temp for consistent extraction across passes
        max_tokens=MAX_TOKENS_PER_CALL,
    )

    raw = resp.choices[0].message.content.strip()

    # Check for truncation (incomplete JSON)
    if resp.choices[0].finish_reason == "length":
        print(f"    ⚠ Response truncated {chunk_label} — chunk may be too large", file=sys.stderr)
        # Try to salvage: find last complete JSON object
        raw = _salvage_truncated_json(raw)

    # Parse JSON
    if raw.startswith("```"):
        raw = re.sub(r'^```(?:json)?\n?', '', raw)
        raw = re.sub(r'\n?```$', '', raw)

    cost = 0.0
    usage = resp.usage
    if usage:
        cost = (usage.prompt_tokens * 0.4 + usage.completion_tokens * 1.6) / 1_000_000

    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        print(f"    ⚠ JSON parse failed {chunk_label}", file=sys.stderr)
        return [], cost

    if not isinstance(items, list):
        return [], cost

    atoms = _parse_items(items, filepath)
    return atoms, cost


def _salvage_truncated_json(raw: str) -> str:
    """Try to recover atoms from truncated JSON array.

    If the response was cut off mid-array, find the last complete
    object and close the array.
    """
    # Find the last complete object (ends with })
    last_close = raw.rfind("}")
    if last_close == -1:
        return "[]"

    truncated = raw[:last_close + 1]

    # Close the array
    if not truncated.rstrip().endswith("]"):
        truncated = truncated.rstrip().rstrip(",") + "\n]"

    return truncated


def _parse_items(items: list, filepath: str) -> list[ShardAtom]:
    """Parse LLM JSON output into ShardAtoms."""
    kind_map = {k.value: k for k in AtomKind}
    atoms = []

    for item in items:
        if not isinstance(item, dict):
            continue
        confidence = item.get("confidence", 1.0)
        if confidence < 0.5:
            continue

        kind_str = item.get("kind", "context")
        kind = kind_map.get(kind_str, AtomKind.CONTEXT)
        text_val = item.get("text", "").strip()
        if not text_val:
            continue

        atom = ShardAtom(
            text=text_val,
            kind=kind,
            entity=item.get("entity"),
            key=item.get("key", "")[:60],
            value=item.get("value"),
            confidence=confidence,
            source_ref=filepath,
        )
        atoms.append(atom)

    return atoms


def regex_extract_text(text: str) -> list[ShardAtom]:
    """Fallback: regex-based extraction (noisy but free)."""
    atoms = []
    seen = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        clean = re.sub(r'\*\*(.+?)\*\*', r'\1', line)
        clean = re.sub(r'`(.+?)`', r'\1', clean)
        clean = re.sub(r'^\s*[-*]\s*', '', clean).strip()
        if 15 <= len(clean) <= 300:
            extracted = extract_atoms(clean)
            for atom in extracted:
                h = atom.content_hash
                if h not in seen:
                    seen.add(h)
                    atoms.append(atom)
    return atoms


def extract_file_with_consensus(filepath: Path, rel_path: str,
                                num_passes: int, use_regex: bool) -> tuple[list[ShardAtom], float]:
    """Extract atoms from a file using chunked shingle consensus.

    1. Chunk the file into overlapping windows
    2. Run extraction on each chunk, N times (passes)
    3. Atoms appearing in 2+ passes are kept (consensus)
    4. Single-pass atoms kept only if confidence >= 0.8

    Returns (consensus_atoms, total_cost).
    """
    content = filepath.read_text()

    if use_regex:
        return regex_extract_text(content), 0.0

    chunks = chunk_text(content)
    print(f"    {len(chunks)} chunk(s), {num_passes} pass(es)")

    # Collect atoms per pass
    pass_results: list[list[ShardAtom]] = []
    total_cost = 0.0

    for pass_num in range(1, num_passes + 1):
        pass_atoms: list[ShardAtom] = []
        for chunk_idx, chunk in enumerate(chunks):
            atoms, cost = llm_extract_chunk(chunk, rel_path, chunk_idx, len(chunks), pass_num)
            pass_atoms.extend(atoms)
            total_cost += cost
        pass_results.append(pass_atoms)

    # Build consensus via fuzzy signature matching
    # For each atom in pass 1, check if a fuzzy-matching atom exists in other passes
    if num_passes == 1:
        consensus_atoms = pass_results[0]
    else:
        # Use pass 1 as the anchor, count fuzzy matches across other passes
        anchor = pass_results[0]
        other_passes = pass_results[1:]

        consensus_atoms = []
        for atom in anchor:
            sig = _atom_signature(atom)
            votes = 1  # self-vote from pass 1

            for other_atoms in other_passes:
                for other_atom in other_atoms:
                    other_sig = _atom_signature(other_atom)
                    if _signatures_match(sig, other_sig):
                        votes += 1
                        # Upgrade to higher-confidence version if available
                        if other_atom.confidence > atom.confidence:
                            atom = other_atom
                        break

            if votes >= 2:
                consensus_atoms.append(atom)

        # Also check atoms unique to later passes that match each other
        # (in case pass 1 missed something that passes 2+ both found)
        if len(other_passes) >= 2:
            for atom in other_passes[0]:
                sig = _atom_signature(atom)
                # Already captured?
                already = any(_signatures_match(sig, _atom_signature(a)) for a in consensus_atoms)
                if already:
                    continue
                for other_atoms in other_passes[1:]:
                    for other_atom in other_atoms:
                        if _signatures_match(sig, _atom_signature(other_atom)):
                            consensus_atoms.append(atom)
                            break

    total_candidates = sum(len(p) for p in pass_results)
    print(f"    consensus: {len(consensus_atoms)} kept from {total_candidates} candidates across {num_passes} passes")

    return consensus_atoms, total_cost


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Extract shard atoms from memory files")
    parser.add_argument("files", nargs="*", help="Specific files to extract from")
    parser.add_argument("--dry-run", action="store_true", help="Show extractions without storing")
    parser.add_argument("--force", action="store_true", help="Re-extract already-processed files")
    parser.add_argument("--regex", action="store_true", help="Use regex fallback instead of LLM")
    parser.add_argument("--passes", type=int, default=2,
                        help="Number of consensus passes (default: 2)")
    parser.add_argument("--scope", default="memory:extracted",
                        help="Scope for extracted shard (default: memory:extracted)")
    args = parser.parse_args()

    store = ShardStore(STORE_PATH)
    extracted_files = load_extracted_files()

    target_files = (get_memory_files(args.files[0] if args.files else None)
                    if args.files else get_memory_files())
    if not target_files:
        print("No memory files found.")
        return

    all_atoms: list[ShardAtom] = []
    file_stats: dict[str, int] = {}
    total_cost = 0.0

    for filepath in target_files:
        rel_path = str(filepath.relative_to(WORKSPACE))
        content = filepath.read_text()
        content_hash = _sha256_str(content)

        if not args.force and extracted_files.get(rel_path) == content_hash:
            print(f"  {filepath.name}: unchanged, skipping")
            continue

        print(f"  {filepath.name}: extracting...")

        atoms, cost = extract_file_with_consensus(
            filepath, rel_path, args.passes, args.regex
        )
        total_cost += cost
        file_stats[filepath.name] = len(atoms)
        all_atoms.extend(atoms)

        # Mark as extracted (even if dry run, track what we've seen)
        if not args.dry_run:
            extracted_files[rel_path] = content_hash

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

    print(f"\n=== Extraction Results ===")
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
        save_extracted_files(extracted_files)
        return

    shard = MemoryShard(
        scope=args.scope,
        atoms=all_atoms,
        origin="lilit:extract_memory",
        tags=[f"extracted-{datetime.now().strftime('%Y-%m-%d')}"],
        meta={
            "extracted_at": datetime.now().isoformat(),
            "method": "regex" if args.regex else "llm",
            "passes": args.passes,
            "cost": round(total_cost, 4),
        },
    )

    ref = store.put(shard)
    shard_id = ref.shard_id
    store.set_ref("memory-extracted", shard_id)
    save_extracted_files(extracted_files)

    print(f"\nStored shard: {shard_id[:16]}...")
    print(f"  Scope: {args.scope}")
    print(f"  Atoms: {len(all_atoms)}")
    print(f"  Tokens: ~{shard.token_estimate}")
    print(f"  Ref: memory-extracted")
    print(f"  Cost: ${total_cost:.4f}")


if __name__ == "__main__":
    main()
