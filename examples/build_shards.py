#!/usr/bin/env python3
"""Build real project shards from workspace knowledge.

This creates the initial shard set that replaces monolithic TOOLS.md
and MEMORY.md with scoped, content-addressed knowledge bundles.

Usage:
    python3 examples/build_shards.py [--store-path ~/.openclaw/workspace/shards]
"""

import sys
from pathlib import Path

# Add parent to path for local dev
sys.path.insert(0, str(Path(__file__).parent.parent))

from spiritwriter.fabric.shard import (
    MemoryShard, ShardAtom, AtomKind, DecayClass,
)
from spiritwriter.fabric.store import ShardStore


AGENT_ID = "agent:main:main"


def build_csp_shard() -> MemoryShard:
    """Project context for claude-studio-producer."""
    return MemoryShard(
        atoms=[
            # Facts
            ShardAtom(
                text="CSP repo at /Users/aaron/Documents/GitHub/claude-studio-producer",
                kind=AtomKind.FACT,
                entity="csp",
                key="repo_path",
                value="/Users/aaron/Documents/GitHub/claude-studio-producer",
            ),
            ShardAtom(
                text="CSP entry point: cs or claude-studio (not csp)",
                kind=AtomKind.FACT,
                entity="csp",
                key="entry_point",
                value="cs or claude-studio",
            ),
            ShardAtom(
                text="CSP current version: 0.7.0",
                kind=AtomKind.FACT,
                entity="csp",
                key="version",
                value="0.7.0",
            ),
            ShardAtom(
                text="CSP git identity: Lilit ⚸ (AI) / lilit-ai@users.noreply.github.com",
                kind=AtomKind.FACT,
                entity="csp",
                key="git_identity",
                value="Lilit ⚸ (AI) / lilit-ai@users.noreply.github.com",
            ),
            ShardAtom(
                text="CSP venv at .venv/, ffmpeg on Mac mini has no drawtext filter — use Pillow",
                kind=AtomKind.FACT,
                entity="csp",
                key="environment",
                value=".venv/, no drawtext in ffmpeg, use Pillow",
            ),
            # Decisions
            ShardAtom(
                text="Always use the librarian path (Unified Architecture), never the legacy asset manifest fallback",
                kind=AtomKind.DECISION,
                entity="decision",
                key="assembler architecture",
                value="librarian path only, legacy raises exception",
            ),
            ShardAtom(
                text="If assembler says 'Using asset manifest (legacy mode)' — STOP. Debug why ContentLibrary.load() failed.",
                kind=AtomKind.CONVENTION,
                entity="convention",
                key="legacy fallback",
                value="never — fix the content library instead",
            ),
            ShardAtom(
                text="Use Mermaid for diagrams, not AI image generators (they hallucinate garbled text)",
                kind=AtomKind.DECISION,
                entity="decision",
                key="diagram generation",
                value="Mermaid produces clean accurate diagrams, AI hallucinates text",
            ),
            ShardAtom(
                text="Ken Burns conditional: only on dall_e images, static hold (figure_sync) for everything else",
                kind=AtomKind.DECISION,
                entity="decision",
                key="Ken Burns behavior",
                value="dall_e=Ken Burns zoom, everything else=static hold",
            ),
            # Instructions
            ShardAtom(
                text="Valid AssetSource enum values: dalle, elevenlabs, openai_tts, google_tts, luma, runway, kb_extraction, web, ffmpeg, manual",
                kind=AtomKind.INSTRUCTION,
                key="AssetSource values",
            ),
            ShardAtom(
                text="Custom diagrams/figures: asset_type='figure', source='manual', display_mode='figure_sync' in structured script",
                kind=AtomKind.INSTRUCTION,
                key="figure asset config",
            ),
            ShardAtom(
                text="cs figures inject <run_dir> <segment> <image_path> -d 'Description'",
                kind=AtomKind.INSTRUCTION,
                key="inject figure",
            ),
            ShardAtom(
                text="cs figures list <run_dir>",
                kind=AtomKind.INSTRUCTION,
                key="list figures",
            ),
            ShardAtom(
                text="cs figures remove <run_dir> <segment>",
                kind=AtomKind.INSTRUCTION,
                key="remove figure",
            ),
            ShardAtom(
                text="Mermaid render: npx @mermaid-js/mermaid-cli -i file.mmd -o file.png -w 1920 -H 1080 -b transparent (use dark theme for video)",
                kind=AtomKind.INSTRUCTION,
                key="render mermaid",
            ),
            ShardAtom(
                text="Video production: cs kb create → cs kb add --paper → cs kb script -p -d 300 → cs produce-video --script --kb --budget low --live → cs figures inject → cs assemble → cs upload youtube",
                kind=AtomKind.INSTRUCTION,
                key="video production flow",
            ),
            ShardAtom(
                text="YouTube upload: cs upload youtube <video> -t 'Title'. YouTube metadata update: cs upload youtube-update VIDEO_ID. OAuth scope needs full 'youtube' (re-auth once).",
                kind=AtomKind.INSTRUCTION,
                key="youtube upload",
            ),
        ],
        scope="project:csp",
        origin=AGENT_ID,
        decay_class=DecayClass.STABLE,
        tags=["CSP project context"],
        meta={"version": "0.7.0"},
    )


def build_spiritwriter_shard() -> MemoryShard:
    """Project context for spiritwriter-core."""
    return MemoryShard(
        atoms=[
            ShardAtom(
                text="spiritwriter-core repo at /Users/aaron/Documents/GitHub/spiritwriter-core",
                kind=AtomKind.FACT,
                entity="spiritwriter",
                key="repo_path",
                value="/Users/aaron/Documents/GitHub/spiritwriter-core",
            ),
            ShardAtom(
                text="spiritwriter-core is private, Apache 2.0, GitHub: aaronmarkham/spiritwriter-core",
                kind=AtomKind.FACT,
                entity="spiritwriter",
                key="github",
                value="aaronmarkham/spiritwriter-core (private, Apache 2.0)",
            ),
            ShardAtom(
                text="spiritwriter-core version 0.1.0",
                kind=AtomKind.FACT,
                entity="spiritwriter",
                key="version",
                value="0.1.0",
            ),
            ShardAtom(
                text="Module structure: models/ (document.py, knowledge.py), secrets/ (keychain.py), classify/ (classifier.py, rules.py, signals.py, zones.py), ingest/ (document.py, extraction.py, mappings.py, mock.py, prompts.py), llm/ (base.py, anthropic.py), kb/ (manager.py), trace/ (shard.py, store.py, emitter.py, extract.py), stopwords.py",
                kind=AtomKind.FACT,
                entity="spiritwriter",
                key="module_structure",
                value="models, secrets, classify, ingest, llm, kb, trace, stopwords",
            ),
            ShardAtom(
                text="Import pattern: from spiritwriter.kb import KnowledgeProject",
                kind=AtomKind.INSTRUCTION,
                key="import pattern",
            ),
            ShardAtom(
                text="DocumentType enum: SCIENTIFIC_PAPER, NEWS_ARTICLE, BLOG_POST, TECHNICAL_REPORT, DATASET_README, GOVERNMENT_DOCUMENT, GENERIC (NOT RESEARCH_PAPER)",
                kind=AtomKind.INSTRUCTION,
                key="DocumentType values",
            ),
            ShardAtom(
                text="Mac mini system Python is 3.14 — needs --break-system-packages for pip install",
                kind=AtomKind.FACT,
                entity="environment",
                key="python_version",
                value="3.14, use --break-system-packages",
            ),
            ShardAtom(
                text="pyproject.toml uses setuptools.build_meta (not legacy — doesn't exist in Python 3.14)",
                kind=AtomKind.DECISION,
                entity="decision",
                key="build backend",
                value="setuptools.build_meta (legacy doesn't exist in 3.14)",
            ),
            ShardAtom(
                text="Chose namespace packages over git submodules because git submodules are painful",
                kind=AtomKind.DECISION,
                entity="decision",
                key="namespace packages over submodules",
                value="git submodules are painful",
            ),
            ShardAtom(
                text="classify/ was split: classifier.py, rules.py, signals.py, zones.py (no more content.py)",
                kind=AtomKind.FACT,
                entity="spiritwriter",
                key="classify_split",
                value="classifier.py, rules.py, signals.py, zones.py",
            ),
        ],
        scope="project:spiritwriter",
        origin=AGENT_ID,
        decay_class=DecayClass.STABLE,
        tags=["spiritwriter-core project context"],
        meta={"version": "0.1.0"},
    )


def build_agent_tools_shard() -> MemoryShard:
    """General agent tooling knowledge (not project-specific)."""
    return MemoryShard(
        atoms=[
            ShardAtom(
                text="Claude Code (CC): claude CLI for bounded refactors. Use -p for print mode, --permission-mode acceptEdits for auto-approve.",
                kind=AtomKind.INSTRUCTION,
                key="Claude Code usage",
            ),
            ShardAtom(
                text="Do NOT run CC from exec shell — eats too much memory on Mac mini, gets SIGKILL'd. Let Aaron drive CC directly.",
                kind=AtomKind.CONVENTION,
                entity="convention",
                key="CC execution",
                value="never from exec shell — Aaron drives directly",
            ),
            ShardAtom(
                text="CC vs sub-agents: CC for bounded refactors (full file context), sub-agents for multi-file docs/PRs (Sonnet), me for quick edits/commits/architecture",
                kind=AtomKind.INSTRUCTION,
                key="task routing",
            ),
            ShardAtom(
                text="Sub-agents (Sonnet) run out of gas at ~200k tokens/10min. Scope tasks tightly.",
                kind=AtomKind.CONVENTION,
                entity="convention",
                key="sub-agent scoping",
                value="always — they hit 200k/10min limit",
            ),
            ShardAtom(
                text="GPT-4.1 workers need absolute paths and step-by-step instructions. They plan instead of executing. Say 'stop planning, start doing.'",
                kind=AtomKind.INSTRUCTION,
                key="GPT-4.1 worker tips",
            ),
            ShardAtom(
                text="TTS fallback: ElevenLabs primary, OpenAI TTS (tts-1-hd, 'onyx' voice) as fallback. TTS_PROVIDER=openai env var. 20x cheaper.",
                kind=AtomKind.INSTRUCTION,
                key="TTS configuration",
            ),
        ],
        scope="agent:tools",
        origin=AGENT_ID,
        decay_class=DecayClass.PERMANENT,
        tags=["agent tooling"],
    )


def build_user_identity_shard() -> MemoryShard:
    """Core user identity — permanent facts."""
    return MemoryShard(
        atoms=[
            ShardAtom(
                text="Aaron — ML Engineer, builds agents, memory systems, knowledge graphs",
                kind=AtomKind.ENTITY,
                entity="Aaron",
                key="role",
                value="ML Engineer — agents, memory systems, KGs",
            ),
            ShardAtom(
                text="Aaron's timezone is America/Los_Angeles (Pacific)",
                kind=AtomKind.FACT,
                entity="Aaron",
                key="timezone",
                value="America/Los_Angeles",
            ),
            ShardAtom(
                text="Chaotic creative — lots of ideas, low execution rate. Wants help with follow-through and accountability.",
                kind=AtomKind.FACT,
                entity="Aaron",
                key="work_style",
                value="chaotic creative, needs accountability",
            ),
            ShardAtom(
                text="Interests: snowboarding (west coast), music (rap, dubstep/bass/trap, indie, psych rock), vinyl, wine, cooking, politics, scientific papers, security",
                kind=AtomKind.FACT,
                entity="Aaron",
                key="interests",
                value="snowboarding, music, vinyl, wine, cooking, politics, papers, security",
            ),
            ShardAtom(
                text="Pet peeve: stupid and rude people",
                kind=AtomKind.PREFERENCE,
                entity="Aaron",
                key="pet_peeve",
                value="stupid and rude people",
            ),
            ShardAtom(
                text="Aaron has a dog named Bear",
                kind=AtomKind.ENTITY,
                entity="Aaron",
                key="pet",
                value="Bear (dog)",
            ),
        ],
        scope="user:identity",
        origin=AGENT_ID,
        decay_class=DecayClass.PERMANENT,
        tags=["user identity"],
    )


def build_active_work_shard() -> MemoryShard:
    """Current active work — refreshes often."""
    return MemoryShard(
        atoms=[
            ShardAtom(
                text="cs figures CLI in progress — needs AssetRecord field verification",
                kind=AtomKind.CONTEXT,
                entity="csp",
                key="active_task",
                value="figures CLI, verify AssetRecord fields",
            ),
            ShardAtom(
                text="Memory shard system just built in spiritwriter-core (trace/). Next: wire into OpenClaw spawn + vec0/FTS5.",
                kind=AtomKind.CONTEXT,
                entity="spiritwriter",
                key="active_task",
                value="shard system built, wiring into OpenClaw next",
            ),
            ShardAtom(
                text="draw-to-deploy: PDF extractor Lambda planned (spiritwriter KB pipeline serverless, solves Pensieve SIGKILL #10)",
                kind=AtomKind.CONTEXT,
                entity="d2d",
                key="next_task",
                value="PDF extractor Lambda",
            ),
            ShardAtom(
                text="Pre-existing test failure: test_karaoke_overlay.py::TestKaraokeOverlay::test_temp_frames_cleaned_up",
                kind=AtomKind.CONTEXT,
                entity="csp",
                key="known_failure",
                value="test_karaoke_overlay temp_frames_cleaned_up",
            ),
            ShardAtom(
                text="CSP open issues: #10 (Pensieve SIGKILL on large PDFs), #12 (KB examples), #13 (provider docs), #14 (security page)",
                kind=AtomKind.CONTEXT,
                entity="csp",
                key="open_issues",
                value="#10, #12, #13, #14",
            ),
        ],
        scope="work:active",
        origin=AGENT_ID,
        decay_class=DecayClass.ACTIVE,
        tags=["active work"],
    )


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Build project shards")
    parser.add_argument(
        "--store-path",
        default=str(Path.home() / ".openclaw" / "workspace" / "shards"),
        help="Path to shard store",
    )
    args = parser.parse_args()

    store = ShardStore(args.store_path)

    shards = [
        ("project:csp", build_csp_shard()),
        ("project:spiritwriter", build_spiritwriter_shard()),
        ("agent:tools", build_agent_tools_shard()),
        ("user:identity", build_user_identity_shard()),
        ("work:active", build_active_work_shard()),
    ]

    for scope_name, shard in shards:
        ref = store.put(shard)
        # Set named ref for easy lookup
        ref_name = scope_name.replace(":", "-")
        store.set_ref(ref_name, ref.shard_id)
        print(f"  ✓ {scope_name:<25} {ref.shard_id[:16]}... ({len(shard.atoms)} atoms, ~{shard.token_estimate} tokens)")

    print(f"\nStore: {args.store_path}")
    stats = store.stats()
    print(f"Total: {stats['total_shards']} shards, {stats['total_atoms']} atoms")
    print(f"Scopes: {', '.join(stats['scopes'].keys())}")

    # Show what a sub-agent would see
    print("\n--- Example: sub-agent hydrating project:csp ---")
    csp_shard = store.resolve_ref("project-csp")
    if csp_shard:
        ctx = csp_shard.hydrate_context()
        print(f"Context size: ~{len(ctx) // 4} tokens")
        print(ctx[:500] + "...")


if __name__ == "__main__":
    main()
