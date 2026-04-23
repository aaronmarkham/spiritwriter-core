# Demo 3: Skills and Tools in the Audit Trail

An agent uses multiple "skills" (compound capabilities) and "tools"
(external side-effects) to plan a weekend trip. Every invocation is
recorded in the trace chain with input/output hashes.

## What it shows

- **`skill_invoked` events** — recorded before each skill call with the
  skill name and a hash of the input arguments
- **`tool_called` / `tool_result` events** — recorded around each tool
  invocation (simulated HTTP APIs) with argument and output hashes
- **Nesting** — a skill (`search_flights`) internally calls a tool
  (`flight_search_api`), and both are visible in the trace
- **Pure skills** — `draft_itinerary` calls no tools, just synthesizes
  data. The trace shows `skill_invoked` + `skill_result` with no
  `tool_called` in between.

## How to run

```bash
python examples/03_skills_and_tools/run.py
```

## What to look at

1. **`traces/agent.jsonl`** — the single trace chain. Walk through it
   and notice the pattern:
   ```
   skill_invoked (search_flights)
     tool_called (flight_search_api)
     tool_result (flight_search_api)
   skill_invoked (check_weather)
     tool_called (weather_api)
     tool_result (weather_api)
   skill_invoked (search_hotels)
     tool_called (hotel_search_api)
     tool_result (hotel_search_api)
   skill_invoked (draft_itinerary)
   skill_result (draft_itinerary)
   ```

2. **Input/output hashes** — each event includes a hash of its inputs
   or outputs. If you re-run the demo, the hashes are identical (same
   canned data). In production, these let you verify that the agent
   consumed the data it claims.

3. **No tool calls for `draft_itinerary`** — it's a pure synthesis step.
   The trace makes this distinction visible: skills that call external
   tools vs. skills that just reason over existing data.

## Takeaway

When an agent has access to tools (APIs, databases, file systems), every
invocation should be in the trace. The input/output hash pattern lets you
audit *what data the agent consumed* and *what it produced* without
storing the full payloads in the trace itself — just the hashes. If you
need the payloads, store them as shards and reference them by ID.
