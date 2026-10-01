"""spiritwriter.agents — record agent-harness activity as trace chains.

Adapters that turn a harness's own event stream (hook payloads, run
logs) into hash-chained :class:`~spiritwriter.fabric.TraceEmitter`
events, so what an agent session actually did becomes a verifiable
receipt with parent/child lineage.

Modules:
    claude_code — Claude Code hooks (``spiritwriter-claude-hook``)
"""
