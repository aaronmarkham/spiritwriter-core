#!/usr/bin/env python3
"""Spiritwriter MCP Server — shard store + trace over MCP.

Bare MCP server wrapping ShardStore methods with auto-tracing.
Every tool call emits a trace event automatically.

Usage (stdio, for CC integration):
    python3 spiritwriter_mcp/server.py --store /path/to/shards

Register in ~/.claude/settings.json:
    "mcpServers": {
        "spiritwriter": {
            "command": "python3",
            "args": ["/path/to/spiritwriter-core/spiritwriter_mcp/server.py", "--store", "/path/to/shards"]
        }
    }
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from spiritwriter.trace.shard import (
    AtomKind,
    DecayClass,
    MemoryShard,
    ShardAtom,
)
from spiritwriter.trace.store import ShardStore
from spiritwriter.trace.emitter import TraceEmitter


# === Globals (set in main) ===
store: ShardStore | None = None
emitter: TraceEmitter | None = None

# === Server ===
mcp = FastMCP("spiritwriter")


# === Auto-trace decorator ===

def traced(fn):
    """Wrap an MCP tool handler with automatic trace events."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        tool_name = fn.__name__
        if emitter:
            emitter.emit("mcp_call",
                tool=tool_name,
                params=_safe_params(kwargs),
            )
        try:
            result = fn(*args, **kwargs)
            if emitter:
                emitter.emit("mcp_result",
                    tool=tool_name,
                    status="ok",
                    summary=_summarize(result),
                )
            return result
        except Exception as e:
            if emitter:
                emitter.emit("mcp_error",
                    tool=tool_name,
                    error=str(e),
                )
            raise
    return wrapper


def _safe_params(kwargs: dict) -> dict:
    """Redact long values for trace readability."""
    out = {}
    for k, v in kwargs.items():
        s = str(v)
        out[k] = s[:200] if len(s) > 200 else s
    return out


def _summarize(result) -> str:
    """Short summary of a result for trace."""
    s = str(result)
    return s[:300] if len(s) > 300 else s


# === Shard Tools ===

@mcp.tool()
@traced
def get_shard(ref: str) -> dict:
    """Resolve a named ref or shard_id and return hydrated context.

    Args:
        ref: Named ref (e.g. 'project-csp') or full shard_id (sha256 hex).

    Returns:
        Dict with shard_id, scope, atom_count, token_estimate, and hydrated context.
    """
    # Try named ref first
    shard = store.resolve_ref(ref)
    if shard is None:
        # Try as raw shard_id
        shard = store.get(ref)
    if shard is None:
        return {"error": f"Shard not found: {ref}"}
    return {
        "shard_id": shard.shard_id,
        "scope": shard.scope,
        "atom_count": len(shard.atoms),
        "token_estimate": shard.token_estimate,
        "context": shard.hydrate_context(),
    }


@mcp.tool()
@traced
def put_shard(
    atoms_json: str,
    scope: str,
    origin: str = "mcp-client",
    decay_class: str = "stable",
    tags: str = "",
) -> dict:
    """Store a new shard.

    Args:
        atoms_json: JSON array of atom objects. Each atom needs at minimum
                    {"text": "..."}. Optional: kind, entity, key, value.
        scope: Entitlement scope (e.g. 'project:csp', 'jobs:queued').
        origin: Agent/process that created this shard.
        decay_class: One of: permanent, stable, active, session, checkpoint.
        tags: Comma-separated tags (e.g. 'checkpoint,my-pipeline').

    Returns:
        Dict with shard_id and atom_count.
    """
    raw_atoms = json.loads(atoms_json)
    atoms = [ShardAtom.from_dict(a) for a in raw_atoms]
    shard = MemoryShard(
        atoms=atoms,
        scope=scope,
        origin=origin,
        decay_class=DecayClass(decay_class),
        tags=[t.strip() for t in tags.split(",") if t.strip()],
    )
    ref = store.put(shard)
    return {
        "shard_id": ref.shard_id,
        "atom_count": len(atoms),
    }


@mcp.tool()
@traced
def set_ref(name: str, shard_id: str) -> dict:
    """Set a named reference pointing to a shard.

    Args:
        name: Ref name (e.g. 'project-csp', 'job:pipeline:checkpoint').
        shard_id: SHA-256 shard content address.

    Returns:
        Confirmation dict.
    """
    store.set_ref(name, shard_id)
    return {"ref": name, "shard_id": shard_id, "status": "set"}


@mcp.tool()
@traced
def delete_ref(name: str) -> dict:
    """Remove a named reference.

    Args:
        name: Ref name to delete.

    Returns:
        Dict with existed=true/false.
    """
    existed = store.delete_ref(name)
    return {"ref": name, "existed": existed}


@mcp.tool()
@traced
def list_refs(prefix: str = "") -> dict:
    """List all named refs, optionally filtered by prefix.

    Args:
        prefix: Only return refs starting with this string (e.g. 'job:').

    Returns:
        Dict with refs list and count.
    """
    refs = store.list_refs(prefix)
    return {"refs": refs, "count": len(refs)}


@mcp.tool()
@traced
def list_scopes() -> dict:
    """List all scopes that have shards.

    Returns:
        Dict with scopes list.
    """
    scopes = store.list_scopes()
    return {"scopes": scopes}


@mcp.tool()
@traced
def store_stats() -> dict:
    """Get summary statistics for the shard store.

    Returns:
        Dict with total_shards, total_atoms, scopes breakdown, decay class breakdown.
    """
    return store.stats()


# === Checkpoint Tools ===

@mcp.tool()
@traced
def write_checkpoint(
    pipeline: str,
    stage: str,
    refs_json: str = "{}",
    origin: str = "cc-worker",
) -> dict:
    """Write a checkpoint shard after completing a pipeline stage.

    Args:
        pipeline: Pipeline name (e.g. 'bias-pipeline', 'podcast').
        stage: Stage name (e.g. 'extract', 'generate').
        refs_json: JSON dict of additional key→shard_id references to store.
        origin: Agent that completed this stage.

    Returns:
        Dict with checkpoint shard_id.
    """
    extra_refs = json.loads(refs_json)
    atoms = [
        ShardAtom(
            text=f"Stage {stage} complete",
            kind=AtomKind.CHECKPOINT,
            entity=f"job:{pipeline}",
            key="stage",
            value=f"{stage}_complete",
        ),
    ]
    for k, v in extra_refs.items():
        atoms.append(ShardAtom(
            text=k,
            kind=AtomKind.CONTEXT,
            entity=f"job:{pipeline}",
            key=k,
            value=str(v),
        ))

    shard = MemoryShard(
        atoms=atoms,
        scope="jobs:in-progress",
        origin=origin,
        decay_class=DecayClass.CHECKPOINT,
        tags=["checkpoint", pipeline],
    )
    ref = store.put(shard)
    store.set_ref(f"job:{pipeline}:checkpoint", ref.shard_id)
    return {"shard_id": ref.shard_id, "stage": f"{stage}_complete"}


@mcp.tool()
@traced
def write_result(
    pipeline: str,
    outputs_json: str = "{}",
    origin: str = "cc-worker",
) -> dict:
    """Write final result shard for a completed pipeline. Cleans up checkpoint.

    Args:
        pipeline: Pipeline name.
        outputs_json: JSON dict of output key→value pairs to store.
        origin: Agent that completed the pipeline.

    Returns:
        Dict with result shard_id.
    """
    outputs = json.loads(outputs_json)
    atoms = [
        ShardAtom(
            text="complete",
            kind=AtomKind.DECISION,
            entity=f"job:{pipeline}",
            key="status",
            value="complete",
        ),
    ]
    for k, v in outputs.items():
        atoms.append(ShardAtom(
            text=k,
            kind=AtomKind.CONTEXT,
            entity=f"job:{pipeline}",
            key=k,
            value=str(v),
        ))

    shard = MemoryShard(
        atoms=atoms,
        scope="jobs:complete",
        origin=origin,
        decay_class=DecayClass.STABLE,
        tags=["result", pipeline],
    )
    ref = store.put(shard)
    store.set_ref(f"job:{pipeline}:result", ref.shard_id)
    store.delete_ref(f"job:{pipeline}:checkpoint")
    return {"shard_id": ref.shard_id, "status": "complete"}


@mcp.tool()
@traced
def get_resume_stage(pipeline: str) -> dict:
    """Check where a pipeline should resume from.

    Args:
        pipeline: Pipeline name.

    Returns:
        Dict with status ('not_started', 'in_progress', 'complete') and current stage.
    """
    # Check for result first
    result = store.resolve_ref(f"job:{pipeline}:result")
    if result is not None:
        return {"status": "complete", "result_shard_id": result.shard_id}

    # Check for checkpoint
    checkpoint = store.resolve_ref(f"job:{pipeline}:checkpoint")
    if checkpoint is not None:
        atom = checkpoint.get_atom("stage")
        if atom:
            completed = atom.value.replace("_complete", "")
            return {
                "status": "in_progress",
                "last_completed_stage": completed,
                "checkpoint_shard_id": checkpoint.shard_id,
            }

    return {"status": "not_started"}


# === Entry Point ===

def main():
    global store, emitter

    parser = argparse.ArgumentParser(description="Spiritwriter MCP Server")
    parser.add_argument(
        "--store",
        default=os.environ.get(
            "SHARD_STORE_PATH",
            str(Path.home() / ".openclaw" / "workspace" / "shards"),
        ),
        help="Path to shard store directory",
    )
    parser.add_argument(
        "--trace",
        default=os.environ.get("TRACE_PATH", None),
        help="Path to trace JSONL output (optional)",
    )
    args = parser.parse_args()

    store = ShardStore(args.store)

    if args.trace:
        import uuid
        emitter = TraceEmitter(
            run_id=str(uuid.uuid4())[:8],
            agent_id="mcp-server",
            out_path=args.trace,
        )
        emitter.emit("mcp_server_started",
            store_path=args.store,
            trace_path=args.trace,
        )

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
