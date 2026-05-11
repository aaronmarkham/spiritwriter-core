"""Trace visualization — render trace events as Mermaid diagrams.

Generates four diagram types:
1. Simple workflow: linear shard flow (package → decrypt → work → result)
2. Genealogy: shard lineage tree (parent → child → grandchild)
3. Multi-agent: per-agent event timelines in subgraphs
4. Delegation tree: cap delegation structure reconstructed from cap_chain
   fields (root → branches → leaves), with role + event count labels

Supports failure states (job_failed events).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def load_trace(path: str | Path) -> list[dict[str, Any]]:
    """Load trace events from JSONL file."""
    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _short_id(full_id: str, length: int = 8) -> str:
    """Truncate an ID for display."""
    if not full_id:
        return "?"
    return full_id[:length]


def _escape(text: str) -> str:
    """Escape text for Mermaid labels."""
    return text.replace('"', "'").replace("\n", " ")[:80]


# === Diagram 1: Simple Workflow ===

def render_simple_workflow(events: list[dict[str, Any]]) -> str:
    """Render a linear workflow diagram from trace events.

    Shows the happy path: package → entitle → decrypt → work → result.
    Failed jobs show with red styling.
    """
    lines = [
        "graph TD",
        "    classDef ok fill:#2d6a4f,stroke:#1b4332,color:#fff",
        "    classDef fail fill:#9d0208,stroke:#6a040f,color:#fff",
        "    classDef shard fill:#023e8a,stroke:#03045e,color:#fff",
        "    classDef spend fill:#e85d04,stroke:#dc2f02,color:#fff",
        "    classDef entitle fill:#7b2cbf,stroke:#5a189a,color:#fff",
        "",
    ]

    node_id = 0
    nodes = {}

    def add_node(label: str, css_class: str = "ok") -> str:
        nonlocal node_id
        nid = f"N{node_id}"
        node_id += 1
        # Replace \n with <br/> for Mermaid line breaks in node labels
        escaped = _escape(label).replace("\\n", "<br/>")
        lines.append(f'    {nid}["{escaped}"]:::{css_class}')
        return nid

    prev_nid = None
    has_failure = False

    for evt in events:
        t = evt["type"]

        if t == "entitlement_granted":
            nid = add_node(
                f"🎫 Entitlement Granted\\n"
                f"to: {evt.get('granted_to', '?')}\\n"
                f"budget: ${evt.get('budget_usd', 0):.2f}",
                "entitle",
            )
        elif t == "job_packaged":
            nid = add_node(
                f"📦 Job Packaged\\n"
                f"content: {_short_id(evt.get('content_shard_id', ''))}...\\n"
                f"task: {_short_id(evt.get('task_shard_id', ''))}...",
                "shard",
            )
        elif t == "capability_checked":
            allowed = evt.get("allowed", False)
            cap = evt.get("capability", "?")
            icon = "✅" if allowed else "🚫"
            nid = add_node(
                f"{icon} Cap Check: {cap}\\n"
                f"{'allowed' if allowed else 'DENIED'}",
                "ok" if allowed else "fail",
            )
        elif t == "shard_decrypted":
            scope = evt.get("scope", "?")
            sid = _short_id(evt.get("shard_id", ""))
            nid = add_node(
                f"🔓 Decrypt: {scope}\\n{sid}...",
                "shard",
            )
        elif t == "job_started":
            prompt = evt.get("prompt", "")
            short_prompt = _escape(prompt)[:50] if prompt else "?"
            nid = add_node(
                f"🎬 Job Started\\n{short_prompt}...",
                "ok",
            )
        elif t == "budget_spent":
            label = evt.get("label", "?")
            amount = evt.get("amount", 0)
            total = evt.get("total_spent", 0)
            nid = add_node(
                f"💰 {label}\\n${amount:.2f} (total: ${total:.2f})",
                "spend",
            )
        elif t == "job_completed":
            spent = evt.get("spent_usd", 0)
            nid = add_node(
                f"✅ Job Complete\\nspent: ${spent:.2f}",
                "ok",
            )
        elif t == "job_failed":
            error = _escape(evt.get("error", "unknown"))[:40]
            spent = evt.get("spent_usd", 0)
            nid = add_node(
                f"❌ Job Failed\\n{error}\\nspent: ${spent:.2f}",
                "fail",
            )
            has_failure = True
        else:
            nid = add_node(f"⚡ {t}", "ok")

        if prev_nid:
            lines.append(f"    {prev_nid} --> {nid}")
        prev_nid = nid

    return "\n".join(lines)


# === Diagram 2: Shard Genealogy ===

def render_shard_genealogy(events: list[dict[str, Any]]) -> str:
    """Render shard lineage as a tree diagram.

    Shows which shards spawned which, and how results link back.
    """
    lines = [
        "graph TD",
        "    classDef content fill:#023e8a,stroke:#03045e,color:#fff",
        "    classDef task fill:#7b2cbf,stroke:#5a189a,color:#fff",
        "    classDef result fill:#2d6a4f,stroke:#1b4332,color:#fff",
        "    classDef entitle fill:#e85d04,stroke:#dc2f02,color:#fff",
        "",
    ]

    # Collect shard relationships from events
    content_shards = set()
    task_shards = set()
    result_shards = set()
    jobs = []  # (content_id, task_id, token_id)
    completions = []  # (token_id, result_id)

    for evt in events:
        t = evt["type"]
        if t == "job_packaged":
            cid = evt.get("content_shard_id", "")
            tid = evt.get("task_shard_id", "")
            tok = evt.get("token_id", "")
            content_shards.add(cid)
            task_shards.add(tid)
            jobs.append((cid, tid, tok))
        elif t == "job_completed":
            tok = evt.get("token_id", "")
            rid = evt.get("result_shard_id", "")
            result_shards.add(rid)
            completions.append((tok, rid))

    # Render nodes
    for cid in content_shards:
        lines.append(f'    C_{_short_id(cid)}["📄 Content<br/>{_short_id(cid)}..."]:::content')
    for tid in task_shards:
        lines.append(f'    T_{_short_id(tid)}["📋 Task<br/>{_short_id(tid)}..."]:::task')
    for rid in result_shards:
        lines.append(f'    R_{_short_id(rid)}["✅ Result<br/>{_short_id(rid)}..."]:::result')

    # Render entitlements and edges
    for cid, tid, tok in jobs:
        tok_node = f"E_{_short_id(tok)}"
        lines.append(f'    {tok_node}{{"🎫 Entitlement<br/>{_short_id(tok)}..."}}:::entitle')
        lines.append(f'    C_{_short_id(cid)} --> {tok_node}')
        lines.append(f'    T_{_short_id(tid)} --> {tok_node}')

        # Link to result
        for rtok, rid in completions:
            if rtok == tok:
                lines.append(f'    {tok_node} --> R_{_short_id(rid)}')

    return "\n".join(lines)


# === Diagram 3: Multi-Agent Trace ===

def render_multi_agent(events: list[dict[str, Any]]) -> str:
    """Render multi-agent workflow with subgraphs per agent.

    Shows agent boundaries, spawn relationships, and where failures occur.
    """
    lines = [
        "graph TD",
        "    classDef ok fill:#2d6a4f,stroke:#1b4332,color:#fff",
        "    classDef fail fill:#9d0208,stroke:#6a040f,color:#fff",
        "    classDef shard fill:#023e8a,stroke:#03045e,color:#fff",
        "    classDef spend fill:#e85d04,stroke:#dc2f02,color:#fff",
        "",
    ]

    # Group events by agent
    agents: dict[str, list[dict]] = {}
    for evt in events:
        aid = evt.get("agent_id", "unknown")
        agents.setdefault(aid, []).append(evt)

    node_id = 0
    agent_last_node: dict[str, str] = {}
    spawn_links: list[tuple[str, str]] = []  # (parent_node, child_first_node)

    for agent_name, agent_events in agents.items():
        lines.append(f"    subgraph {agent_name.replace('-', '_')}[{agent_name}]")

        prev_nid = None
        for evt in agent_events:
            t = evt["type"]
            nid = f"N{node_id}"
            node_id += 1

            if t == "entitlement_granted":
                label = f"🎫 Grant → {evt.get('granted_to', '?')}"
                css = "shard"
            elif t == "job_packaged":
                label = f"📦 Package job"
                css = "shard"
            elif t == "capability_checked":
                ok = evt.get("allowed", False)
                label = f"{'✅' if ok else '🚫'} {evt.get('capability', '?')}"
                css = "ok" if ok else "fail"
            elif t == "shard_decrypted":
                label = f"🔓 {evt.get('scope', '?')}"
                css = "shard"
            elif t == "job_started":
                prompt = evt.get("prompt", "")
                label = f"🎬 {_escape(prompt)[:35]}..."
                css = "ok"
            elif t == "budget_spent":
                label = f"💰 {evt.get('label', '?')} ${evt.get('amount', 0):.2f}"
                css = "spend"
            elif t == "job_completed":
                label = f"✅ Done ${evt.get('spent_usd', 0):.2f}"
                css = "ok"
            elif t == "job_failed":
                label = f"❌ {_escape(evt.get('error', ''))[:30]}"
                css = "fail"
            elif t == "spawn_with_shards":
                child = evt.get("child_agent_id", "?")
                label = f"🚀 Spawn → {child}"
                css = "shard"
            else:
                label = f"⚡ {t}"
                css = "ok"

            lines.append(f'        {nid}["{label}"]:::{css}')
            if prev_nid:
                lines.append(f"        {prev_nid} --> {nid}")

            # Track spawn relationships
            if t == "entitlement_granted":
                granted_to = evt.get("granted_to", "")
                if granted_to:
                    spawn_links.append((nid, granted_to))

            prev_nid = nid
            agent_last_node[agent_name] = nid

        lines.append("    end")
        lines.append("")

    # Cross-agent links (entitlement → first event of granted agent)
    for parent_nid, child_agent in spawn_links:
        if child_agent in agents:
            child_events = agents[child_agent]
            if child_events:
                # Find the first node of child agent
                # We need to recalculate... use a simpler approach
                pass  # Cross-links handled by subgraph visual grouping

    return "\n".join(lines)


# === Diagram 4: Delegation Tree ===

def render_delegation_tree(events: list[dict[str, Any]]) -> str:
    """Render the capability delegation tree inferred from event cap_chains.

    Each event's ``cap_chain`` is a path through the delegation tree
    (root at index 0, leaf at the end). Aggregating across events
    reconstructs the tree: a cap's parent is whichever cap_id
    immediately precedes it in any chain that contains it.

    Each node shows its cap_id prefix; leaf nodes additionally show
    the role (if events tagged one) and the count of events emitted
    under that leaf. The tree is purely structural — it shows the
    authority relationships, not event sequence. Use
    :func:`render_multi_agent` for the per-worker event timeline.

    Returns a valid (if minimal) graph when no cap-tagged events are
    present, so callers don't need to special-case the legacy-trace
    case.
    """
    # Reconstruct tree structure: cap_id → parent cap_id (None = root)
    parent_of: dict[str, str | None] = {}
    # Per-leaf annotation: role and event count (only set for caps that
    # actually emitted events with cap_id matching them).
    leaf_role: dict[str, str] = {}
    leaf_event_count: dict[str, int] = {}

    for evt in events:
        chain = evt.get("cap_chain")
        if chain:
            for i, cap_id in enumerate(chain):
                # First-write-wins: if two events disagree about a cap's
                # parent (which would indicate a corrupted log or a
                # mis-built emitter), the first observation is kept and
                # subsequent ones are silently ignored. Treating chain
                # provenance as authoritative rather than majority-vote
                # avoids letting noisy late events rewrite tree shape.
                if cap_id not in parent_of:
                    parent_of[cap_id] = chain[i - 1] if i > 0 else None
            leaf_id = chain[-1]
            # An event emitted under a leaf cap will have cap_id == that leaf.
            # (Some events may carry chain without cap_id — those don't tag
            # the leaf, just the chain.)
            if evt.get("cap_id") == leaf_id:
                role = evt.get("role")
                if role and leaf_id not in leaf_role:
                    leaf_role[leaf_id] = role
                leaf_event_count[leaf_id] = leaf_event_count.get(leaf_id, 0) + 1
        else:
            # No chain — degraded emitter setup. Treat the leaf-only cap_id
            # as a root in the rendered tree; better than silently dropping.
            cap_id = evt.get("cap_id")
            if cap_id:
                parent_of.setdefault(cap_id, None)
                role = evt.get("role")
                if role and cap_id not in leaf_role:
                    leaf_role[cap_id] = role
                leaf_event_count[cap_id] = leaf_event_count.get(cap_id, 0) + 1

    if not parent_of:
        return "graph TD\n    %% no cap-tagged events in input"

    children_of: dict[str, set[str]] = {}
    for cap_id, parent in parent_of.items():
        if parent is not None:
            children_of.setdefault(parent, set()).add(cap_id)

    lines = [
        "graph TD",
        "    classDef root fill:#5a189a,stroke:#3c096c,color:#fff",
        "    classDef branch fill:#7b2cbf,stroke:#5a189a,color:#fff",
        "    classDef leaf fill:#023e8a,stroke:#03045e,color:#fff",
        "",
    ]

    def _node_id(cap_id: str) -> str:
        return f"C_{_short_id(cap_id)}"

    # Sort for deterministic output — important for diff-based tests.
    for cap_id in sorted(parent_of):
        if parent_of[cap_id] is None:
            css = "root"
        elif cap_id in children_of:
            css = "branch"
        else:
            css = "leaf"

        # Use _short_id for the visible label too — keeps node IDs and
        # the label's cap_id prefix the same length, matching the
        # convention in the other renderers.
        parts = [f"{_short_id(cap_id)}…"]
        if cap_id in leaf_role:
            # Route role through _escape() — it's caller-supplied text and
            # Mermaid labels can break on quotes, brackets, or newlines.
            parts.append(f"role: {_escape(leaf_role[cap_id])}")
        if cap_id in leaf_event_count:
            n = leaf_event_count[cap_id]
            parts.append(f"{n} event{'s' if n != 1 else ''}")
        label = "<br/>".join(parts)

        lines.append(f'    {_node_id(cap_id)}["{label}"]:::{css}')

    for cap_id, parent in sorted(parent_of.items()):
        if parent is not None:
            lines.append(f"    {_node_id(parent)} --> {_node_id(cap_id)}")

    return "\n".join(lines)


# === Convenience wrapper ===

def render_trace(
    events: list[dict[str, Any]],
    diagram_type: str = "workflow",
) -> str:
    """Convenience wrapper — render events with a named diagram type.

    diagram_type: "workflow", "genealogy", "multi-agent", or "delegation".
    """
    renderers = {
        "workflow": render_simple_workflow,
        "genealogy": render_shard_genealogy,
        "multi-agent": render_multi_agent,
        "delegation": render_delegation_tree,
    }
    renderer = renderers.get(diagram_type)
    if renderer is None:
        raise ValueError(
            f"Unknown diagram type {diagram_type!r}. "
            f"Choose from: {', '.join(renderers)}"
        )
    return renderer(events)


# === Main: Generate all diagrams from a trace file ===

def generate_all(trace_path: str | Path, output_dir: str | Path) -> dict[str, str]:
    """Generate all diagram types from a trace file.

    Returns dict of diagram_name → mermaid_content.
    Also writes .mmd files to output_dir.
    """
    events = load_trace(trace_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    diagrams = {
        "workflow": render_simple_workflow(events),
        "genealogy": render_shard_genealogy(events),
        "multi-agent": render_multi_agent(events),
        "delegation": render_delegation_tree(events),
    }

    for name, content in diagrams.items():
        path = output_dir / f"{name}.mmd"
        path.write_text(content, encoding="utf-8")

    return diagrams


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m spiritwriter.fabric.visualize <trace.jsonl> [output_dir]")
        sys.exit(1)

    trace_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "."

    diagrams = generate_all(trace_path, output_dir)
    for name, content in diagrams.items():
        print(f"\n=== {name.upper()} ===")
        print(content)
