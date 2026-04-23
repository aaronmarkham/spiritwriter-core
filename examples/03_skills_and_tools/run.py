#!/usr/bin/env python3
"""Demo 3: Skills and tools in the audit trail.

Shows an agent using multiple "skills" (functions) and "tools" (external
side-effects) over the course of a task, with each invocation recorded
in the trace chain.

The toy task: "Plan a weekend trip to Portland, OR."

Skills: search_flights, check_weather, draft_itinerary
Tools: HTTP-shaped stubs returning canned JSON

The final trace chain shows every skill/tool usage is auditable from
the JSONL alone — you can reconstruct exactly what the agent did,
what data it consumed, and what it produced.

Usage:
    python examples/03_skills_and_tools/run.py
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

from spiritwriter.fabric.shard import (
    MemoryShard, ShardAtom, AtomKind, DecayClass,
)
from spiritwriter.fabric.store import ShardStore
from spiritwriter.fabric.emitter import TraceEmitter, verify_chain
from spiritwriter.fabric.visualize import render_trace


# ── Canned tool responses (simulate external APIs) ──────────────────

FLIGHT_DATA = {
    "flights": [
        {"airline": "Alaska", "depart": "SFO 08:15", "arrive": "PDX 10:20", "price": 149},
        {"airline": "United", "depart": "SFO 12:30", "arrive": "PDX 14:35", "price": 189},
        {"airline": "Alaska", "depart": "SFO 17:00", "arrive": "PDX 19:05", "price": 129},
    ],
    "currency": "USD",
}

WEATHER_DATA = {
    "location": "Portland, OR",
    "forecast": [
        {"day": "Saturday", "high": 68, "low": 52, "condition": "Partly cloudy"},
        {"day": "Sunday", "high": 72, "low": 54, "condition": "Sunny"},
    ],
    "unit": "fahrenheit",
}

HOTEL_DATA = {
    "hotels": [
        {"name": "Ace Hotel Portland", "rate": 185, "rating": 4.3},
        {"name": "Jupiter Hotel", "rate": 142, "rating": 4.1},
    ],
    "currency": "USD",
}


def _data_hash(data) -> str:
    """SHA-256 hash of canonical JSON — used for trace input/output hashes."""
    raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


# ── Tool stubs (simulate HTTP calls) ────────────────────────────────

def tool_flight_search(origin: str, dest: str, date: str) -> dict:
    """Simulate an HTTP call to a flight search API."""
    return FLIGHT_DATA


def tool_weather_api(location: str, days: int) -> dict:
    """Simulate an HTTP call to a weather API."""
    return WEATHER_DATA


def tool_hotel_search(location: str, checkin: str, checkout: str) -> dict:
    """Simulate an HTTP call to a hotel search API."""
    return HOTEL_DATA


# ── Skills (agent capabilities that compose tools) ──────────────────

def skill_search_flights(tracer: TraceEmitter, origin: str, dest: str, date: str) -> dict:
    """Skill: search for flights. Calls the flight search tool."""
    skill_input = {"origin": origin, "dest": dest, "date": date}
    tracer.emit("skill_invoked", skill_name="search_flights", input_hash=_data_hash(skill_input))

    # Call the underlying tool
    tool_args = {"origin": origin, "dest": dest, "date": date}
    tracer.emit("tool_called", tool_name="flight_search_api", argument_hash=_data_hash(tool_args))
    result = tool_flight_search(origin, dest, date)
    tracer.emit("tool_result", tool_name="flight_search_api", output_hash=_data_hash(result))

    return result


def skill_check_weather(tracer: TraceEmitter, location: str, days: int) -> dict:
    """Skill: check weather forecast. Calls the weather API tool."""
    skill_input = {"location": location, "days": days}
    tracer.emit("skill_invoked", skill_name="check_weather", input_hash=_data_hash(skill_input))

    tool_args = {"location": location, "days": days}
    tracer.emit("tool_called", tool_name="weather_api", argument_hash=_data_hash(tool_args))
    result = tool_weather_api(location, days)
    tracer.emit("tool_result", tool_name="weather_api", output_hash=_data_hash(result))

    return result


def skill_search_hotels(tracer: TraceEmitter, location: str, checkin: str, checkout: str) -> dict:
    """Skill: search for hotels. Calls the hotel search tool."""
    skill_input = {"location": location, "checkin": checkin, "checkout": checkout}
    tracer.emit("skill_invoked", skill_name="search_hotels", input_hash=_data_hash(skill_input))

    tool_args = {"location": location, "checkin": checkin, "checkout": checkout}
    tracer.emit("tool_called", tool_name="hotel_search_api", argument_hash=_data_hash(tool_args))
    result = tool_hotel_search(location, checkin, checkout)
    tracer.emit("tool_result", tool_name="hotel_search_api", output_hash=_data_hash(result))

    return result


def skill_draft_itinerary(
    tracer: TraceEmitter,
    flights: dict,
    weather: dict,
    hotels: dict,
    preferences: dict,
) -> str:
    """Skill: draft an itinerary from gathered data. No tool calls — pure synthesis."""
    skill_input = {
        "flight_count": len(flights.get("flights", [])),
        "weather_days": len(weather.get("forecast", [])),
        "hotel_count": len(hotels.get("hotels", [])),
        "preferences": preferences,
    }
    tracer.emit("skill_invoked", skill_name="draft_itinerary", input_hash=_data_hash(skill_input))

    # "Synthesize" the itinerary (mock — no LLM)
    best_flight = min(flights["flights"], key=lambda f: f["price"])
    best_hotel = min(hotels["hotels"], key=lambda h: h["rate"])
    forecast = weather["forecast"]

    itinerary = (
        f"Weekend Trip to Portland, OR\n"
        f"{'=' * 35}\n\n"
        f"Flight: {best_flight['airline']} {best_flight['depart']} -> {best_flight['arrive']} "
        f"(${best_flight['price']})\n"
        f"Hotel: {best_hotel['name']} (${best_hotel['rate']}/night, {best_hotel['rating']} stars)\n\n"
        f"Weather:\n"
    )
    for day in forecast:
        itinerary += f"  {day['day']}: {day['condition']}, {day['high']}F/{day['low']}F\n"
    itinerary += f"\nEstimated total: ${best_flight['price'] + best_hotel['rate'] * 2}"

    tracer.emit("skill_result", skill_name="draft_itinerary", output_hash=_data_hash(itinerary))
    return itinerary


# ── Main ────────────────────────────────────────────────────────────

def main(output_dir: Path | None = None) -> int:
    if output_dir is None:
        output_dir = Path(__file__).parent / "traces"
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        store = ShardStore(td)
        trace_path = str(output_dir / "agent.jsonl")
        Path(trace_path).unlink(missing_ok=True)

        tracer = TraceEmitter(
            run_id="trip-planner-001",
            agent_id="travel-agent",
            out_path=trace_path,
        )

        # Record the task
        request_shard = MemoryShard(
            atoms=[
                ShardAtom(
                    text="Plan a weekend trip to Portland, OR from San Francisco.",
                    kind=AtomKind.INSTRUCTION,
                    key="task",
                ),
                ShardAtom(
                    text="Budget-conscious, prefer morning flights.",
                    kind=AtomKind.PREFERENCE,
                    key="preferences",
                ),
            ],
            scope="demo:trip",
            origin="travel-agent",
            decay_class=DecayClass.SESSION,
            tags=["trip-request"],
        )
        store.put(request_shard)
        tracer.shard_created(
            shard_id=request_shard.shard_id,
            scope=request_shard.scope,
            atom_count=len(request_shard.atoms),
        )

        # ── Execute skills ──
        flights = skill_search_flights(tracer, "SFO", "PDX", "2026-07-18")
        weather = skill_check_weather(tracer, "Portland, OR", 2)
        hotels = skill_search_hotels(tracer, "Portland, OR", "2026-07-18", "2026-07-20")
        itinerary = skill_draft_itinerary(
            tracer, flights, weather, hotels,
            preferences={"budget": "low", "flight_time": "morning"},
        )

        # Store the result
        result_shard = MemoryShard(
            atoms=[
                ShardAtom(
                    text=itinerary,
                    kind=AtomKind.CONTEXT,
                    key="itinerary",
                ),
                ShardAtom(
                    text=f"Total estimated cost: ${129 + 142 * 2}",
                    kind=AtomKind.FACT,
                    key="total_cost",
                    entity="trip",
                    value=str(129 + 142 * 2),
                ),
            ],
            scope="demo:trip-result",
            origin="travel-agent",
            decay_class=DecayClass.STABLE,
            tags=["trip-itinerary"],
        )
        store.put(result_shard)
        tracer.shard_created(
            shard_id=result_shard.shard_id,
            scope=result_shard.scope,
            atom_count=len(result_shard.atoms),
        )

        # ── Verify ──
        events = tracer.get_events()
        chain_ok = verify_chain(events)

        print("== Demo 3: Skills and Tools in the Audit Trail ==\n")
        print(f"  Trace: {len(events)} events, chain valid: {chain_ok}")

        print("\n  Event sequence:")
        for e in events:
            t = e["type"]
            if t == "skill_invoked":
                print(f"    SKILL  {e['skill_name']}  (input: {e['input_hash'][:12]}...)")
            elif t == "skill_result":
                print(f"    SKILL  {e['skill_name']} -> (output: {e['output_hash'][:12]}...)")
            elif t == "tool_called":
                print(f"      TOOL {e['tool_name']}  (args: {e['argument_hash'][:12]}...)")
            elif t == "tool_result":
                print(f"      TOOL {e['tool_name']} -> (output: {e['output_hash'][:12]}...)")
            else:
                print(f"    {t}")

        # Show the itinerary
        print(f"\n  Result:\n")
        for line in itinerary.split("\n"):
            print(f"    {line}")

        # Count skill and tool events
        skill_count = sum(1 for e in events if e["type"] == "skill_invoked")
        tool_count = sum(1 for e in events if e["type"] == "tool_called")
        print(f"\n  Skills invoked: {skill_count}")
        print(f"  Tools called:   {tool_count}")

        # Mermaid
        mermaid = render_trace(events, diagram_type="workflow")
        mermaid_path = output_dir / "workflow.mmd"
        mermaid_path.write_text(mermaid, encoding="utf-8")
        print(f"\n  Mermaid diagram: {mermaid_path}")

        if not chain_ok:
            print("\n  FAIL")
            return 1

        print("\n  PASS")
        return 0


if __name__ == "__main__":
    sys.exit(main())
