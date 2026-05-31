# Spiritwriter KB Expert Agent — Specification

## "Samurai Mechanic" as Reference Implementation

**Version:** 0.1.0-draft
**Date:** 2026-03-21
**Author:** Aaron + Claude (pair-specced during live repair session)
**Status:** RFC — ready for architecture review

---

## 1. Problem Statement

You just fixed your first belt squeal on an '87 Samurai using a hammer and three wrenches. The diagnostic chain that got you there — symptom → conditions → differential → narrowing → procedure → improvisation — lived entirely in a transient conversation. The video you shot is on your phone. The forum threads that informed the diagnosis are scattered across suzuki-forums.com and off-road.com. The FSM PDF sits on archive.org, unindexed.

None of this knowledge compounds. Next time something squeals, hums, or knocks, you start from zero.

**Spiritwriter KB Expert solves this by making domain expertise accumulate.** Ingest reference material, capture experiential knowledge from real repair sessions, build a queryable diagnostic graph, and produce shareable community content — all from the same substrate.

---

## 2. Architecture Overview

```
┌──────────────────────────────────────────────────────────┐
│                  Spiritwriter Core                         │
│  (content-addressed memory shards, decay classes)         │
│  CHECKPOINT 4hr │ ACTIVE 14d │ STABLE 90d │ PERMANENT    │
└──────────┬───────────────────────────┬───────────────────┘
           │                           │
     ┌─────▼──────┐            ┌──────▼────────┐
     │  KB Ingest  │            │  Session Log  │
     │  Pipeline   │            │  Capture      │
     └─────┬──────┘            └──────┬────────┘
           │                           │
     ┌─────▼──────────────────────────▼───────┐
     │         Knowledge Graph Layer           │
     │  (entities, relations, diagnostic trees)│
     └─────────────────┬──────────────────────┘
                       │
          ┌────────────┼────────────┐
          │            │            │
    ┌─────▼────┐ ┌────▼─────┐ ┌──▼──────────┐
    │  Expert   │ │  Maint.  │ │  Community   │
    │  Agent    │ │  Ledger  │ │  Publisher   │
    └──────────┘ └──────────┘ └─────────────┘
```

### 2.1 Three Layers

| Layer | Purpose | Shard Decay Class |
|-------|---------|-------------------|
| **Reference KB** | FSM chapters, wiring diagrams, torque specs, parts catalogs | PERMANENT |
| **Experiential KB** | Repair sessions, diagnostic chains, what-worked/what-didn't | STABLE (90d), promoted to PERMANENT on review |
| **Session Context** | Active diagnostic conversation, current symptoms, live photos | ACTIVE (14d) → CHECKPOINT during session |

---

## 3. KB Ingest Pipeline

### 3.1 PDF → Graph Extraction

The FSM is a 482-page PDF with a known structure. Ingest is chapter-aware, not page-aware.

```python
class FSMIngestor:
    """
    Extracts structured knowledge from Factory Service Manual PDFs.
    Produces graph nodes (Component, Procedure, Spec, DiagnosticStep)
    and edges (PART_OF, REQUIRES_TOOL, SYMPTOM_OF, LEADS_TO).
    """

    def __init__(self, pdf_path: str, vehicle: VehicleProfile):
        self.pdf_path = pdf_path
        self.vehicle = vehicle

    def extract_chapters(self) -> list[Chapter]:
        """
        OCR-aware chapter segmentation.
        The FSM has clear chapter headers — use these as primary splits.
        Falls back to page-range heuristics for scanned manuals.
        """
        ...

    def extract_components(self, chapter: Chapter) -> list[ComponentNode]:
        """
        Entities: alternator, water pump, V-belt, crankshaft pulley, etc.
        Attributes: torque specs, part numbers, clearances, fluid types.
        """
        ...

    def extract_procedures(self, chapter: Chapter) -> list[ProcedureNode]:
        """
        Step-by-step procedures with:
        - required_tools: list[Tool]
        - prerequisite_steps: list[ProcedureNode]
        - torque_specs: dict[str, TorqueSpec]
        - cautions: list[str]
        """
        ...

    def build_diagnostic_trees(self, chapter: Chapter) -> list[DiagnosticTree]:
        """
        Many FSM chapters contain explicit troubleshooting flowcharts.
        Extract these as decision trees:
          symptom → check → result → next_check | diagnosis
        """
        ...
```

### 3.2 Supported Source Types

| Source | Extractor | Graph Output |
|--------|-----------|--------------|
| FSM PDF | `FSMIngestor` | Components, procedures, specs, diagnostic trees |
| Chilton/Haynes PDF | `AftermarketIngestor` | Simplified procedures, cross-refs to FSM |
| Forum threads | `ForumIngestor` | Experiential nodes: symptom reports, solutions, failure modes |
| Repair session transcripts | `SessionIngestor` | Diagnostic chains, tool choices, improvisation notes |
| Video transcripts + frames | `VideoIngestor` | Time-stamped procedure steps, visual reference anchors |
| Parts catalogs | `PartsCatalogIngestor` | Part numbers, cross-references, pricing snapshots |

### 3.3 Graph Schema

```python
# Core node types
@dataclass
class ComponentNode:
    id: str                          # e.g., "samurai_87_alternator"
    name: str                        # "Alternator"
    system: str                      # "charging_system"
    vehicle: VehicleProfile
    specs: dict[str, Any]            # torque values, clearances, etc.
    part_numbers: list[PartRef]
    related_components: list[str]    # graph edges

@dataclass
class SymptomNode:
    id: str                          # e.g., "squeal_cold_start_rpm_dependent"
    description: str
    conditions: list[Condition]      # when it occurs
    modifiers: list[Modifier]        # what changes it
    # Conditions are key for differential diagnosis:
    # - rpm_dependent vs road_speed_dependent
    # - cold_only vs always
    # - load_dependent vs constant
    # - clutch_in_changes vs clutch_in_no_change

@dataclass
class DiagnosticEdge:
    symptom: str                     # SymptomNode.id
    component: str                   # ComponentNode.id
    confidence: float                # 0.0-1.0 based on condition match
    source: str                      # "fsm", "forum", "experience"
    reasoning: str                   # why this edge exists

@dataclass
class ProcedureNode:
    id: str
    title: str                       # "Alternator Belt Tension Adjustment"
    component: str                   # ComponentNode.id
    steps: list[ProcedureStep]
    tools_required: list[Tool]
    tools_improvised: list[Tool]     # from experiential KB
    difficulty: str                  # "beginner", "intermediate", "advanced"
    time_estimate: str
    media: list[MediaRef]            # photos, video timestamps

@dataclass
class ProcedureStep:
    order: int
    instruction: str
    spec: Optional[TorqueSpec]
    caution: Optional[str]
    media: list[MediaRef]            # step-specific photos/video
    improvisation_notes: list[str]   # "used hammer to tap alternator outward"

@dataclass
class MaintenanceEvent:
    """Ledger entry — what was actually done, when, by whom."""
    id: str
    date: datetime
    vehicle: VehicleProfile
    mileage: int
    procedure: str                   # ProcedureNode.id
    components_touched: list[str]
    parts_used: list[PartRef]
    tools_used: list[Tool]
    outcome: str
    notes: str
    media: list[MediaRef]
    session_transcript: Optional[str]  # link to spiritwriter shard
```

---

## 4. Expert Agent

### 4.1 Agent Role

The Expert Agent is a **diagnostic copilot** that:

1. **Listens** to symptom descriptions in natural language
2. **Queries** the KB graph to generate a ranked differential diagnosis
3. **Asks** discriminating questions to narrow the differential (rpm vs road speed? cold only? clutch in/out?)
4. **Retrieves** the relevant FSM procedure once diagnosis converges
5. **Adapts** instructions to the user's tool inventory and skill level
6. **Records** the session as experiential knowledge for future retrieval

### 4.2 Agent System Prompt Structure

```python
EXPERT_AGENT_SYSTEM = """
You are a diagnostic mechanic agent for {vehicle.year} {vehicle.make} {vehicle.model}.

## Knowledge Sources (ranked by authority)
1. Factory Service Manual (PERMANENT shards) — torque specs, procedures, wiring
2. Experiential KB (STABLE shards) — past repairs on THIS vehicle, what worked
3. Community knowledge (forum-sourced nodes) — common failure modes, gotchas
4. General mechanical reasoning — physics of the problem

## Vehicle Profile
{vehicle.profile_dump()}

## Maintenance History
{vehicle.maintenance_ledger.recent(n=20)}

## Owner Profile
- Skill level: {owner.skill_level}  # beginner/intermediate/advanced
- Available tools: {owner.tool_inventory}
- Nearby parts sources: {owner.parts_sources}

## Diagnostic Protocol
When presented with a symptom:
1. Classify: engine/drivetrain/electrical/suspension/brakes/body
2. Gather conditions: when does it happen? what changes it?
3. Generate differential with confidence scores from KB graph
4. Ask the ONE most discriminating question to split the differential
5. Iterate until confidence > 0.8 on a single diagnosis
6. Present procedure adapted to owner's skill and tools
7. Offer improvisation alternatives from experiential KB

## Session Recording
Flag key diagnostic insights for KB promotion:
- New symptom→cause edges not in current graph
- Improvisation techniques that worked
- Tool substitutions
- Gotchas and mistakes to avoid
"""
```

### 4.3 Diagnostic Decision Engine

```python
class DiagnosticEngine:
    """
    Walks the symptom→component graph using condition matching.
    Each user answer narrows the candidate set.
    """

    def __init__(self, kb: KnowledgeGraph, vehicle: VehicleProfile):
        self.kb = kb
        self.vehicle = vehicle
        self.candidates: list[DiagnosticEdge] = []
        self.asked: list[Condition] = []

    def intake(self, symptom_description: str) -> list[DiagnosticEdge]:
        """
        NLP parse → match to SymptomNodes → return ranked candidates.
        Example: "squealing on cold start with choke" →
          matches: squeal_cold_start_rpm_dependent (0.9),
                   bearing_whine_speed_dependent (0.3),
                   power_steering_pump_squeal (0.2)
        """
        ...

    def discriminate(self) -> Question:
        """
        Find the single question that maximally splits remaining candidates.
        Information-theoretic: pick the condition that halves the candidate set.

        Example state:
          candidates = [v_belt_slip (0.9), water_pump_bearing (0.4), alternator_bearing (0.3)]
          best_question = "Does the pitch change with engine RPM or road speed?"
          # RPM → belt/pump/alternator (no change)
          # Road speed → wheel bearing, drivetrain (eliminates all current candidates)
        """
        ...

    def update(self, condition: Condition, answer: Any) -> list[DiagnosticEdge]:
        """
        Bayesian update on candidates given new evidence.
        Returns re-ranked candidate list.
        """
        ...

    def converged(self, threshold: float = 0.8) -> Optional[ComponentNode]:
        """True when top candidate confidence > threshold."""
        ...
```

---

## 5. Maintenance Ledger

The ledger is the **append-only log** of everything done to the vehicle. Each entry is a STABLE shard (promoted to PERMANENT on annual review).

### 5.1 Ledger Entry Creation Flow

```
Repair session conversation
        │
        ▼
  Session transcript (CHECKPOINT shard)
        │
        ▼
  Agent extracts structured MaintenanceEvent
        │
        ▼
  Owner reviews and confirms
        │
        ▼
  Ledger entry (STABLE shard) + KB graph updates
        │
        ▼
  Experiential edges promoted if novel
```

### 5.2 Example Ledger Entry (Today's Session)

```yaml
event:
  id: "maint_20260321_belt_tension"
  date: 2026-03-21
  vehicle: { year: 1987, make: suzuki, model: samurai, vin: null }
  mileage: 1000  # post-rebuild
  procedure: "alternator_belt_tension_adjustment"
  components_touched:
    - alternator_bracket
    - v_belt_alternator_waterpump
    - engine_ground_wire  # found loose, deferred repair
  parts_used: []  # no parts replaced
  tools_used:
    - "10mm combination wrench"
    - "12mm combination wrench"
    - "14mm combination wrench"
    - "small hammer (improvised pry tool)"
  outcome: "success — squeal eliminated"
  notes: |
    Belt was slipping on cold start with choke engaged.
    Tensioned by loosening upper adjustment bolt and lower pivot bolt,
    tapping alternator body outward with hammer in small increments.
    Also discovered broken ring terminal on engine ground wire near
    alternator — aftermarket crimp, ring cracked open, just hanging
    on stud with no nut. Deferred to Monday (Tacoma Screw closed).
  follow_up:
    - "Replace ground wire ring terminal and add nut — 2026-03-23"
    - "Re-check belt tension after 50 miles of driving"
    - "Inspect belt for glazing/cracks at next check"
  media:
    - { type: video, path: "PXL_20260321_repair_session.mp4", description: "Full repair video" }
    - { type: photo, path: "PXL_20260321_192825271.jpg", description: "Broken ring terminal close-up" }
    - { type: photo, path: "PXL_20260321_192800018.jpg", description: "Alternator area wide shot showing ground wire" }
  diagnostic_chain:
    initial_hypothesis: "bearing (per mechanic friend)"
    symptoms_reported:
      - "squealing on cold start with choke"
      - "stops when choke lowered"
      - "returns when revving at idle to keep engine running"
    discriminating_conditions:
      - rpm_dependent: true
      - cold_dependent: true
      - road_speed_dependent: false
    final_diagnosis: "v_belt_slip"
    novel_edges:
      - { symptom: "squeal_cold_choke", component: "v_belt", confidence: 0.95, note: "choke raises RPM + cold rubber = textbook slip" }
    improvisation:
      - "Used hammer to tap alternator outward instead of pry bar — walk it in small increments"
```

---

## 6. Community Publisher

### 6.1 The Feedback Loop

The whole point is that fixing your car and sharing the knowledge are the **same workflow**, not separate activities.

```
You fix the squeal
        │
        ▼
  Spiritwriter captures the session
        │
        ├──▶ KB gets smarter (your agent improves)
        │
        ├──▶ Maintenance ledger updated (your records are clean)
        │
        └──▶ Community content generated (you give back)
```

### 6.2 Claude Studio Producer Integration

The repair video + session transcript + KB context feed directly into the Claude Studio Producer pipeline:

```python
class RepairContentPipeline:
    """
    Takes a repair session and produces shareable community content.
    Uses Claude Studio Producer's 6-agent architecture.
    """

    def generate_screenplay(
        self,
        session: MaintenanceEvent,
        video: MediaRef,
        transcript: str,
        kb_context: list[GraphNode]
    ) -> Screenplay:
        """
        Produces a screenplay from the repair session.

        Structure:
        1. COLD OPEN — the symptom (audio of the squeal)
        2. DIAGNOSIS — walking through the differential
           (overlay: diagnostic decision tree from KB)
        3. THE FIX — procedure with FSM reference
           (split screen: FSM diagram + actual hands on car)
        4. THE GOTCHA — unexpected discovery (broken ground wire)
        5. RESULT — before/after audio comparison
        6. KB UPDATE — what the agent learned (meta/educational)

        SegmentIntents used:
        - PROBLEM_STATEMENT (cold open)
        - EXPLAINER (diagnosis walkthrough)
        - TUTORIAL (procedure steps)
        - DISCOVERY (ground wire find)
        - COMPARISON (before/after)
        - META (KB learning)
        """
        ...

    def generate_forum_post(
        self,
        session: MaintenanceEvent,
        kb_context: list[GraphNode]
    ) -> ForumPost:
        """
        Generates a structured forum post for suzuki-forums.com or similar.

        Includes:
        - Vehicle specs and mileage
        - Symptom description with conditions
        - Diagnostic process (what was checked, what was ruled out)
        - Fix procedure with tool list
        - Photos
        - Follow-up items
        """
        ...

    def generate_kb_export(
        self,
        session: MaintenanceEvent
    ) -> KBExportPackage:
        """
        Portable knowledge package that others can import into
        their own Spiritwriter KB Expert instance.

        Contains:
        - New diagnostic edges (symptom→cause)
        - Procedure annotations (improvisation, tool substitutions)
        - Vehicle-specific gotchas
        - Media references

        Format: content-addressed shard bundle, importable via
        Spiritwriter's standard shard sync protocol.
        """
        ...
```

### 6.3 Content Output Formats

| Format | Target | Generated From |
|--------|--------|----------------|
| **Video essay** | YouTube / ToorCamp talk | Claude Studio Producer screenplay + repair footage |
| **Forum post** | suzuki-forums.com, off-road.com | Structured session summary + photos |
| **KB shard bundle** | Other Spiritwriter users | Portable diagnostic edges + procedures |
| **Maintenance report** | Personal records / insurance | Ledger entries with media attachments |
| **Tutorial article** | Blog / documentation | Procedure + context for beginners |

---

## 7. CMC-Lite Surface

The KB Expert needs a clean user-facing interface in CMC-Lite that makes it obvious how to:

### 7.1 Create a New Expert

```
┌─────────────────────────────────────┐
│  Create Expert KB                    │
│                                      │
│  Name: [ Samurai Mechanic          ] │
│  Domain: [ Vehicle Maintenance     ] │
│                                      │
│  Reference Sources:                  │
│  [+] Upload PDF (FSM, Chilton...)    │
│  [+] Add URL (forum thread, guide)   │
│  [+] Import shard bundle             │
│                                      │
│  Vehicle Profile:                    │
│  Year: [1987] Make: [Suzuki]         │
│  Model: [Samurai] Engine: [1.3L G13] │
│                                      │
│  Owner Profile:                      │
│  Skill Level: [Beginner ▼]           │
│  Tool Inventory: [Edit...]           │
│  Local Parts Sources: [Edit...]      │
│                                      │
│  [ Create Expert ]                   │
└─────────────────────────────────────┘
```

### 7.2 Interact With Expert

```
┌─────────────────────────────────────┐
│  🔧 Samurai Mechanic                │
│  1987 Suzuki Samurai │ 1,000 mi     │
│  Last service: 2026-03-21           │
│                                      │
│  ┌─────────────────────────────────┐ │
│  │ What's going on?                │ │
│  │ [ squealing noise on cold start]│ │
│  │                       [Ask ▶]  │ │
│  └─────────────────────────────────┘ │
│                                      │
│  Recent:                             │
│  ✅ Belt tension (3/21) — resolved   │
│  ⚠️  Ground wire repair — pending    │
│                                      │
│  Quick Actions:                      │
│  [📋 Maintenance History]            │
│  [📸 Add Photos/Video]               │
│  [📤 Share to Community]             │
│  [📥 Import KB Bundle]              │
└─────────────────────────────────────┘
```

### 7.3 Review & Publish Flow

After a repair session, the agent proposes:

```
┌─────────────────────────────────────┐
│  Session Complete — Review & Save    │
│                                      │
│  📝 Maintenance Entry    [Review ▶]  │
│     Belt tension adjustment          │
│     + ground wire discovery          │
│                                      │
│  🧠 KB Updates           [Review ▶]  │
│     2 new diagnostic edges           │
│     1 improvisation technique        │
│                                      │
│  🎬 Content Ready         [Edit ▶]  │
│     Video screenplay drafted         │
│     Forum post drafted               │
│                                      │
│  [ Save All ]  [ Edit ]  [ Discard ] │
└─────────────────────────────────────┘
```

---

## 8. Implementation Roadmap

### Phase 1: KB Ingest + Expert Agent (MVP)
**Target: ToorCamp CFP demo material (April 1)**

- [ ] PDF chapter extractor for Samurai FSM
- [ ] Basic graph schema (ComponentNode, SymptomNode, ProcedureNode)
- [ ] Diagnostic engine with condition-based narrowing
- [ ] Expert agent prompt with KB context injection
- [ ] Manual ledger entry creation from conversation

### Phase 2: Session Capture + Ledger
**Target: ToorCamp talk prep (May 4)**

- [ ] Automatic session transcript → MaintenanceEvent extraction
- [ ] Ledger as STABLE shards with promotion workflow
- [ ] Photo/video attachment to ledger entries
- [ ] Follow-up task tracking and reminders

### Phase 3: Community Publisher
**Target: Post-ToorCamp**

- [ ] Claude Studio Producer screenplay generation from repair sessions
- [ ] Forum post generator
- [ ] KB shard bundle export/import
- [ ] CMC-Lite UI for create/interact/publish flow

### Phase 4: Multi-Vehicle + Fleet
**Target: Future**

- [ ] Multiple vehicle profiles
- [ ] Cross-vehicle knowledge transfer (e.g., "V-belt tension" applies broadly)
- [ ] Shared community KB with trust scoring
- [ ] Parts price tracking and sourcing recommendations

---

## 9. ToorCamp Angle

This spec has a natural home in the ToorCamp talk. The narrative arc:

> "I built a privacy-preserving surveillance system to track ICE detention rosters.
> Then I used the same memory substrate to teach myself to fix a 1987 Suzuki Samurai.
> The same architecture that protects detained persons' families also helps you
> diagnose a belt squeal. Here's why that's not a coincidence — it's about
> **knowledge that compounds in systems you control.**"

The Samurai repair is a perfect live demo: show the video, walk through the diagnostic chain, demonstrate the KB agent doing real-time diagnosis, show how the session automatically becomes a shareable community post. It's concrete, funny, and it lands the architectural point about Spiritwriter without requiring the audience to care about jail rosters.

---

## 10. Resolved Architectural Decisions

These were initially open questions. Resolved 2026-03-21 during spec review.

### 10.1 Graph Storage: Local JSON-LD Atoms (RESOLVED)

**Decision:** Stay with local JSON-LD storage, consistent with CSP's KB tooling and spiritwriter/CMC-Lite. No graph database.

**Rationale:** CSP and spiritwriter have converged on the same pattern — JSON-LD atoms stored locally as content-addressed shards. GraphDB was deliberately avoided because the target KBs (scientific papers, FSM chapters, forum threads) are small enough that graph queries resolve in-memory. A 482-page FSM is larger than a typical 30-page paper, but not by enough to justify a new storage layer. The KB Expert is a **surface**, not a new system — it consumes the same atoms through the same shard protocol.

**Action:** Verify CSP's current KB atom schema and confirm compatibility with spiritwriter's shard addressing. If they've drifted, reconcile before building the ingest pipeline. The FSM ingestor should produce atoms in the existing format, not a new one.

### 10.2 KB Chunking: Chapter-First, Then Cross-Chapter Use-Case Shards (RESOLVED)

**Decision:** Chunk by chapter initially. Then create custom cross-chapter shards organized by common use case.

**Rationale:** FSM chapters are natural boundaries and likely fit within a single memory shard's size budget. But real diagnostic work crosses chapters — a belt squeal touches the charging system chapter, the cooling system chapter (water pump), and the general maintenance chapter (belt specs). Use-case shards curate the relevant sections from multiple chapters into a single coherent context.

**Example use-case shards for the Samurai:**
- `samurai_belt_drive_system` — alternator chapter belt section + cooling chapter water pump pulley + maintenance chapter belt specs + torque values
- `samurai_electrical_diagnosis` — charging system + wiring diagrams + ground point locations + fuse box
- `samurai_drivetrain_noise` — transmission bearings + transfer case + axle bearings + propeller shaft

These are analogous to how CSP's KB builds summaries from paper sections — you're not storing the whole paper in one shard, you're extracting the atoms that serve a particular query pattern.

### 10.3 Video → KB Alignment: Use CSP's Existing Pipeline (RESOLVED)

**Decision:** Reuse CSP's script-to-video manifest with EDL (Edit Decision List) alignment. Existing figure-presentation + audio-cue alignment is sufficient for procedure step anchoring.

**Rationale:** CSP already solved this for science video production — aligning visual figures with narration timestamps. A repair video has the same structure: "at 3:42, I'm loosening the upper bolt" maps to a procedure step the same way "at 1:15, Figure 3 shows the protein structure" maps to a paper section. The EDL notion in CSP gives us frame-accurate anchoring.

**Pipeline:**
```
Repair video (phone)
        │
        ▼
  Whisper transcript (timestamped)
        │
        ▼
  CSP manifest alignment
  (match transcript segments to ProcedureStep nodes)
        │
        ▼
  EDL with step-anchored timestamps
        │
        ▼
  MediaRef entries on each ProcedureStep
```

**Action:** Review CSP's manifest/EDL code, confirm it can accept an external video source (phone footage) rather than generated video. May need a thin adapter for raw footage vs. CSP's rendered output.

### 10.4 Community Trust: Trust-But-Verify via Budgeted Evaluation (RESOLVED)

**Decision:** Economic self-correction through budgeted evaluation. No reputation score — use ground-truth verification with margin pressure.

**Rationale:** The trust model falls out of the agent marketplace pattern. Applied to KB shards:

```
Agent/human submits KB shard bundle
        │
        ▼
  Marketplace job queue
  (budget: N+1 where 1 is ground truth)
        │
        ▼
  Consumer evaluates against ground truth
        │
        ├── Pass → contributor gets entitlement for next batch
        │          lineage records contributor as reliable for this type
        │
        └── Fail → contributor's margin shrinks
                   repeated failure → cost exceeds payment → self-exit
```

**Key insight:** You don't need a reputation *score* — you need a system where producing garbage is economically irrational. Contributors (human or agent) who consistently produce good shards appear in the lineage of trusted outputs. Those who produce bad shards burn their own margin and stop naturally.

**For the Samurai KB specifically:** Someone shares a shard claiming "Samurai alternator bolts are 15mm" — the system verifies against the FSM ground truth (they're not, they're 12/14mm) and the shard is rejected. Contributor's reliability for "Samurai specs" type outputs drops. Experiential knowledge (improvisation techniques, diagnostic shortcuts) is harder to verify automatically but gets corroborated when multiple contributors' shards agree.

### 10.5 Offline/Local: Distilled SPA Now, Local Model Later (RESOLVED)

**Decision:** V1 is a distilled SPA that works offline on your phone. Local model inference is the long-term target but not feasible yet. Cloud handles complex queries and KB sync.

**Rationale:** Three tiers of service, matching the current state of the tech:

| Tier | Context | Capability | Implementation |
|------|---------|------------|----------------|
| **Tier 1: Offline SPA** | Garage, dirty hands, no signal | Pre-cached relevant subgraph, searchable procedures, specs, photos. Read-only but useful. | PWA/SPA generated from shard contents, works offline. Natural extension of the Greasemonkey extension pattern — shards consumed and rendered in browser. |
| **Tier 2: Phone agent** | Has signal, conversational | Full diagnostic agent with KB access, can pull additional shards as needed, captures session notes | Current architecture — cloud inference with shard context injection |
| **Tier 3: Workstation** | Post-repair, at desk | Full ingest pipeline, video processing, community publishing, KB maintenance | Claude Code / CSP full pipeline |

**The Tier 1 SPA is the key near-term deliverable.** It's the "garage mode" — before you start a job, you tell the agent what you're working on, it distills the relevant subgraph into a self-contained offline page with procedures, specs, diagrams, and your maintenance history for that system. You pull it up on your phone, prop it on the fender, and work.

This is architecturally identical to how Frio's Tampermonkey extension consumes and generates shards from the browser. The shard format is the same. The delivery mechanism is the same. The only difference is the domain.

**Local model inference** (on-device LLM for Tier 1 conversational support) is where this goes eventually — phone hardware is almost there for small specialized models. But for v1, the distilled SPA covers 80% of the "dirty hands in the garage" use case without requiring local inference.

---

## 11. Key Realization: This Is Not a New System

The most important outcome of this spec review is recognizing that the KB Expert is **zero new infrastructure**. Every component maps to something that already exists:

| KB Expert Component | Already Exists In |
|--------------------|--------------------|
| Graph storage | spiritwriter JSON-LD atoms |
| Shard lifecycle | Spiritwriter decay classes (CHECKPOINT/ACTIVE/STABLE/PERMANENT) |
| KB ingest | CSP's KB tooling (paper → atoms) |
| Video alignment | CSP's manifest + EDL pipeline |
| Agent context injection | CMC-Lite's shard loading |
| Offline delivery | Frio's Tampermonkey extension pattern |
| Community trust | Agent marketplace budgeted evaluation |
| Content publishing | Claude Studio Producer's 6-agent pipeline |

The work is:
1. **Surface it** — CMC-Lite UI for creating and interacting with domain experts
2. **Write the FSM ingestor** — a new extractor type for CSP's existing KB pipeline
3. **Wire the diagnostic engine** — the Bayesian narrowing logic that sits between the agent and the graph
4. **Build the Tier 1 SPA generator** — distill a subgraph into an offline-capable page

That's it. Four deliverables, all building on proven substrate.
