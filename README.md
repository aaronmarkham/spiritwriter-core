# spiritwriter

**Agent memory you own.**

Durable, content-addressed memory · provable traces · scoped delegation · entity resolution — local-first, with no service to run and no data handed over. Drop it under whatever orchestrator and retriever you already use.

---

If you've built more than one agentic system, you've rebuilt the same glue every time. Where does what the agent learned live, and how do you keep it from drifting into contradiction? How do you hand a sub-task to another agent without handing over the keys to everything? And three steps later, when something went wrong — can you prove what actually happened?

Most teams rebuild that layer per app, suffer the misalignments and delegation failures and memory drift, or rent it from a managed service that takes custody of their data and their latency budget. Few teams do *provable* tracing or scoped entitlements without standing up heavy infrastructure. There's no standard library for the layer **beneath** the agent.

spiritwriter is that layer.

## What it is

A **trust-configurable memory substrate**. One set of primitives — content-addressed memory shards, hash-chained traces, scoped entitlements, deterministic entity resolution — that you dial from *fully public and provable* to *fully private and zero-knowledge* by changing one thing: the shard's [posture](docs/shard-postures.md).

It is **not** an agent framework and **not** a vector database. It's the layer those sit on:

```
  your orchestrator   (LangGraph / CrewAI / a raw loop)     ← bring your own
  your retriever      (vector DB / RAG / full-text search)  ← bring your own
  ─────────────────────────────────────────────────────────
  spiritwriter        memory · provenance · delegation       ← yours: local-first, data never leaves
                      · entity resolution
```

Bring your own everything-else. spiritwriter handles what those layers leave out: durable memory that doesn't drift, traces you can prove to a third party, delegation you can actually scope, and entity records that don't duplicate or collide. It's additive — you don't migrate, you slip it underneath what you already run. And because it's local-first, your data never leaves your machine: nothing to provision, nothing to meter, almost nothing to switch off if you change your mind. The registry is a file you own, not a row in someone else's database.

## Proven at both extremes

The substrate is real because two live products run on it at opposite ends of the trust dial — same primitives, opposite postures:

| | [news.spiritwriter.ai](https://news.spiritwriter.ai) | [frio.help](https://frio.help) |
|---|---|---|
| **Posture** | maximum transparency | maximum privacy |
| **What it does** | articles atomized, rewritten across the spectrum, every variant linked back to its source | families searching for an incarcerated relative; rosters matched, alerts sent |
| **What you can see** | the full lineage — follow a fact as it mutates | nothing — searches are sealed from the operator; matching happens in memory |

One library wrote both. The only difference is the posture.

## Install

```bash
pip install -e .                        # core
pip install -e ".[sealed]"              # + NaCl sealed boxes (zero-knowledge)
pip install -e ".[network]"             # + IPFS backend
pip install -e ".[s3]"                  # + S3 backend (AWS object storage)
pip install -e ".[dev,sealed,network]"  # everything
```

Requires Python 3.9+.

## Quick Start

```python
from spiritwriter.fabric.shard import MemoryShard, ShardAtom, AtomKind, DecayClass
from spiritwriter.fabric.store import ShardStore

store = ShardStore("~/.myapp/shards")

shard = MemoryShard(
    atoms=[
        ShardAtom(text="Project uses FastAPI", kind=AtomKind.FACT,
                  entity="myproject", key="framework", value="FastAPI"),
        ShardAtom(text="Always run migrations before deploying",
                  kind=AtomKind.CONVENTION, entity="myproject",
                  key="deploy_rule", value="migrations-first"),
    ],
    scope="project:myproject",
    origin="dev-agent",
    decay_class=DecayClass.STABLE,
)

ref = store.put(shard)              # idempotent — same content, same ID
context = store.hydrate([ref])      # XML-tagged context, ready for prompt injection
```

That's the whole onramp: store what the agent learned, hydrate it back into a prompt later. Everything below is the same substrate dialed up — encryption, delegation, provenance, resolution. See [docs/getting-started.md](docs/getting-started.md) for the layered model and use-case reading paths.

> **Teach it to your agent, not just your app.** Each capability ships with an agent-readable skill (`skills/*/SKILL.md`). An agent can learn the primitives by reading a skill — no install, no integration code on your side.

## What it ends

Each capability maps to a problem you'd otherwise re-solve by hand:

- **Memory that doesn't drift** — *Memory Shards.* Knowledge grows without losing history: new observations supersede old ones via lineage links; identical content from different agents dedupes into one record; decay classes (`PERMANENT`, `STABLE`, `ACTIVE`, `SESSION`, `CHECKPOINT`) prune what shouldn't outlive its purpose. Content-addressed (SHA-256 over atoms + scope + origin), so the same content is always the same ID.
- **Storage you own** — *Shard Store.* Local-first on disk, Git-style object layout. Named refs (mutable pointers to immutable shards) give you "latest version of X" without breaking content addressing. Optional network fetch when a backend is configured.
- **Privacy as a setting** — *Encryption.* AES-256-GCM when the operator and key-holder cooperate; NaCl sealed boxes when the operator must *not* see content (multi-tenant hosting, source protection, zero-knowledge services).
- **Delegation you can scope** — *Entitlements + Jobs.* Hand a sub-agent a token bundling decryption keys + scope patterns + capabilities + budget; the store enforces every constraint before it decrypts. Package content + task + entitlement into one unit of work; the sub-agent hydrates, runs, returns a result shard. Every step traced.
- **Proof of what happened** — *Tracing.* Hash-chained JSONL, optionally Ed25519-signed. Replay a run, prove nothing's been edited, render it as workflow / genealogy / multi-agent diagrams — for debugging expensive failures, auditing before deploy, or proving a run's integrity to a third party.
- **Entities that don't collide** — *Entity Resolution.* Tell "Bear" the dog from "Bear" the brand; merge "Carlos Martinez" and "MARTINEZ, CARLOS A" into one. Deterministic-then-fuzzy, no embeddings, no LLM in the merge path. (See [The Bear Problem](#the-bear-problem) below.)
- **Sharing without a database** — *IPFS distribution.* Publish shards to a private swarm; consumers fetch missing shards from the network and cache locally. Or persist them to a managed S3 bucket for durability without a node to operate.
- **Tamper-evident audits** — *Android APK audits.* Inputs, evidence, findings, and report bound into a hash-chained trace plus a self-hashing witness — anyone with the APK can re-run verification offline.

## Encryption

```python
from spiritwriter.fabric.crypto import generate_job_key

key = generate_job_key()
encrypted = store.encrypt_and_store(shard, key)        # AES-256-GCM, operator can decrypt with the key
decrypted = store.decrypt_and_get(encrypted.shard_id, key)
```

Zero-knowledge (operator can't decrypt — this is the posture `frio` runs):

```python
from spiritwriter.fabric.sealed import generate_owner_keypair

keypair = generate_owner_keypair()
sealed = store.seal_and_store(shard, keypair.public_key)   # only the owner's private key opens it
decrypted = store.unseal_and_get(sealed.shard_id, keypair.private_key)
```

## Entity Resolution

```python
from spiritwriter.fabric.canonicalize import CanonicalRegistry, CanonicalSchema

schema = CanonicalSchema(
    name="person",
    ess_fields=["last_name", "first_name", "dob"],
    fuzzy_fields={"last_name": 0.90, "first_name": 0.80},
)

candidate = {"last_name": "Smith", "first_name": "John", "dob": "1990-05-12"}
with CanonicalRegistry("/tmp/people.db", schema) as registry:
    result = registry.resolve(candidate)
    cid = registry.upsert(candidate, result, "source_a", "001")
```

The interesting part is *why* this resolves correctly without an embedding model or an LLM in the loop — that's the Bear Problem, below.

## The Bear Problem

You're extracting facts about Aaron from a stack of documents. Document 1 surfaces "Bear is Aaron's favorite." Document 2: "Aaron and Bear were at the park." Document 3: "Aaron's dog Bear, a 10-year-old black lab / border collie mix (a Borador)."

Each document gives partial defining-field coverage, and your extractor classifies Bear three different ways: a name in Document 1, a generic animal in Document 2, a specific dog in Document 3. Three identifiers for the same entity, and they don't align. A naive system keeps them separate (you have three Bears, no convergence as more documents arrive) or collapses by surface name alone (now Bear-the-dog merges with Bear-the-beer brand mentioned in Document 4). Embedding-based systems hallucinate the boundaries — they score "Bear" the dog close to "Bear" the bear close to "Bear" the brand, and the merge decisions become unauditable.

The resolver hashes the *defining fields* (name + entity type + owner + …) into an **Entity Sense Signature (ESS)**, a deterministic identity hash. As more documents land, defining fields accumulate per entity. Document 1 gives `name=Bear, owner=Aaron`. Document 3 adds `entity_type=dog, breed=borador`. The growing field set produces a stable ESS the moment you have enough fields to disambiguate. Fields not yet known don't penalize the match — they're absent from the hash, and ESS overlap rewards the fields you *do* share.

The same primitive handles the inverse: "Carlos Martinez", "MARTINEZ, CARLOS A", and "C. Martinez" across three rosters dedupe into one entity, because their defining fields normalize to the same hash regardless of surface spelling. (One caveat worth knowing up front: the registry normalizes only case and whitespace — anything more is the caller's job. See [Normalize before you resolve](docs/entity-resolution.md#normalize-before-you-resolve).)

### Resolution Tiers

| Tier | Match | Action |
|------|-------|--------|
| T1 | Exact ESS digest | Auto-merge |
| T2 | High fuzzy quality + high ESS overlap | Auto-merge |
| T3 | Fuzzy with lower combined score | Flag, don't merge |
| T4 | Weak context overlap | Flag only |

### Tech Stack

Two layers, one per concern:

- **`CanonicalRegistry`** — one SQLite file. The entity-resolution index: three tables (`entities`, `sightings`, `merges`), WAL mode for concurrent readers.
- **`ShardStore`** — content-addressed JSON atoms on disk. The underlying knowledge the registry points at.

The registry holds *which canonical entity each sighting maps to*; the shards hold *what the entity actually is*. Same architecture whether you're on a laptop or a multi-node deployment. See [Memory Shards](docs/memory-shards.md) and [Shard Store](docs/shard-store.md).

### Why These Design Choices

- **Local-first.** A `CanonicalRegistry` is one SQLite file; the shards it points at are plain JSON. No service to run, no vector DB to host, no daemon to keep alive. The registry *is* the artifact — email it, version-control it, copy it between machines, restore it from a backup.
- **Deterministic before fuzzy.** Auto-merge only at T1 and T2. Anything weaker becomes a flagged event for human review. False merges are the worst failure mode in entity resolution, and silent ones are unauditable. The resolver fails loud.
- **No LLM in the auto-merge path.** LLMs hallucinate, and for entity resolution that means silently combining records of two different people. Deterministic + fuzzy with explicit tiers is verifiable end-to-end; LLM judgment isn't. Use an LLM upstream to extract atoms if you want; keep it out of the merge decision.
- **Schema-driven, domain-agnostic.** Same engine handles people, products, papers, articles — anything where you can name the defining fields. Tier thresholds tune per domain. The schema's hash is stored on first open; reopening with a different schema raises `ValueError` rather than silently misclassifying records.
- **Lightweight to bootstrap.** No embedding model to train or host, no GPU, no vector index to rebuild on schema change. From `pip install` to resolving entities in seconds, on a laptop, offline.

### The Numbers

**100% auto-merge precision — 0 incorrect merges across 5 benchmark corpora**, and it surfaces 100% of same-entity matches for review — auto-merged at T1/T2 when safe, flagged otherwise — so nothing slips through silently. No embeddings, no LLM calls — SQLite, normalization, and string matching. See [docs/benchmarks/runs-log.md](docs/benchmarks/runs-log.md) for the measurements and the falsification battery behind them.

The full spec ([docs/specs/cmc-spec-v0.1.md](docs/specs/cmc-spec-v0.1.md)) draws on academic prior art (EDC/EMNLP 2024, Graphiti/Zep, SimpleMem, EMem-G); the implementation pulls the three highest-impact ideas — content-addressed identity, tiered escalation, and [shingled extraction](docs/shingled-extraction.md) — and ships them with zero new infrastructure.

**Deeper:** [Entity Resolution guide](docs/entity-resolution.md), [Shingled Extraction](docs/shingled-extraction.md), [CMC-Lite spec](docs/specs/cmc-lite-v0.1.md).

## Canonical Forms

Entity resolution answers *are these two records the same thing?* with a confidence score. Some structures don't need one. A ring of see-also references read from a different starting point, an edge list walked backwards, a set of co-equal authors listed in another order — each differs from its twin only by a symmetry the structure allows, and there the answer is exact.

`fabric.orbit` computes the single canonical representative of a structure's equivalence class, plus a content-addressed digest of it. Two systems that have never communicated arrive at the same value because there is only one value to arrive at — no registry to consult, no identifier service, no prior agreement beyond the operations themselves.

```python
from spiritwriter.fabric import cycle_digest

a = ["Cartography", "Map projections", "Geodesy", "Surveying", "Topographic mapping"]
b = ["Geodesy", "Surveying", "Topographic mapping", "Cartography", "Map projections"]

cycle_digest(a) == cycle_digest(b)      # True — same loop, different starting point
```

### The Numbers

Across **200 pairs** over three corpora — undirected rings, directed cycles, unordered groupings — the rule settles **114/114 same-structure pairs with 0 wrong merges**. The best similarity threshold that makes no wrong merges catches **0 of them**.

No cutoff separates the classes, and the per-relation means say why: pairs that must stay *apart* score **higher** (0.729) than rotations (0.690), permutations (0.610), or reflections (0.546). Rewriting a structure changes the text a great deal; altering the structure changes it hardly at all. Similarity points the wrong way here. See [benchmarks/eval/structural_accuracy](benchmarks/eval/structural_accuracy/) for the harness and the full sweep.

This is a floor under the fuzzy layer. It collapses exactly the differences you can state in advance and nothing else — it will never know that Twain and Clemens are one person.

**Deeper:** [Canonical Forms guide](docs/canonical-forms.md). Long-form, with the demonstrations running live: [Computed, Not Assigned](https://spiritwriter.ai/computed-not-assigned.html).

## Documentation

| Guide | Description |
|-------|-------------|
| [Getting Started](docs/getting-started.md) | installation, the layered model, use-case reading paths |
| [Memory Shards](docs/memory-shards.md) | atoms, decay classes, hydration, content addressing |
| [Atoms](docs/atoms.md) | what's flexible vs not, worked examples for every AtomKind |
| [Shard Store](docs/shard-store.md) | storage layout, named refs, scope queries, maintenance |
| [Shard Postures](docs/shard-postures.md) | the trust dial — encryption, signing, scope, decay, distribution as one setting |
| [Encryption](docs/encryption.md) | AES-GCM, NaCl sealed boxes, threat model |
| [Entitlements](docs/entitlements.md) | bearer tokens, capabilities, budget, scope enforcement |
| [Jobs](docs/jobs.md) | packaging delegated sub-agent work; issuer / runner sides |
| [Entity Resolution](docs/entity-resolution.md) | ESS, tiered matching, normalization, batch processing |
| [Canonical Forms](docs/canonical-forms.md) | exact forms under declared symmetry — rings, cycles, groupings, digests |
| [Shingled Extraction](docs/shingled-extraction.md) | overlapping-window extraction with multi-pass consensus |
| [Tracing](docs/tracing.md) | hash-chained provenance, chain verification, signed traces |
| [Traced Workflows](docs/traced-workflows.md) | multi-stage pipelines with checkpoint/resume |
| [Network Distribution](docs/network-distribution.md) | IPFS and S3 backends, choosing between them, manifests, private swarm, L1/L2 resolution |
| [Substrate Flavor](docs/substrate-flavor.md) | wire format + verification rules for library-free implementers in any language |
| [Audit](docs/audit.md) | tamper-evident Android APK security audits |
| [Integration Guide](docs/integration-guide.md) | how frio, perseus-news, and Claude Studio Producer use it |
| [API Reference](docs/api-reference.md) | complete public API surface |

## Examples

Self-contained demos that exercise the fabric APIs end-to-end — no LLM calls, no network, plain Python composing shards, traces, entitlements, jobs, and resolution. Each runs with `python examples/NN_xxx/run.py` and exits 0.

| Demo | What it shows |
|------|---------------|
| [01_simple_trace](examples/01_simple_trace/) | Parent packages a job, spawns a subagent, receives a result shard — two independent hash-chained traces |
| [02_todo_fanout](examples/02_todo_fanout/) | Compound request split into 4 subagents, each writing a result shard with `source_ref` lineage, assembled by the parent |
| [03_skills_and_tools](examples/03_skills_and_tools/) | Agent uses skills and tools to plan a trip; every invocation recorded with input/output hashes |
| [04_governance_divergence](examples/04_governance_divergence/) | Same job run twice — Run A behaves, Run B exceeds budget and capabilities; parent detects violations via trace |
| [05_delegation_with_trace](examples/05_delegation_with_trace/) | Per-key delegation: root → orchestrator → 3 workers, each with its own Ed25519 leaf cap; signed shards trace back to the event that produced them |
| [06_phalanx_flow](examples/06_phalanx_flow/) | Full pipeline — paper → shingled chunking → atoms → memory shard → delegated job → entity resolution, all under one trace |

Run them under test with `python -m pytest tests/test_demos.py -v`.

## Benchmarks

```bash
python -m pytest benchmarks/ -v -s
```

See [benchmarks/README.md](benchmarks/README.md) for what's measured and how to read it, and [docs/benchmarks/runs-log.md](docs/benchmarks/runs-log.md) for the tracked measurements over time.

## Architecture

```
spiritwriter/
├── audit/          # Tamper-evident Android APK security audits
├── classify/       # Content/theme classification
├── fabric/         # Shards, store, encryption, entitlements, jobs, traces, network
│   ├── shard.py         # MemoryShard, ShardAtom, ShardRef
│   ├── store.py         # ShardStore (Git-style content addressing)
│   ├── crypto.py        # AES-256-GCM encryption
│   ├── sealed.py        # NaCl sealed boxes, Ed25519 signing
│   ├── entitlement.py   # Scoped access tokens
│   ├── canonicalize.py  # Entity resolution (CanonicalRegistry, ESS, tiers)
│   ├── emitter.py       # Hash-chained trace events
│   ├── extract.py       # Atom extraction utilities
│   ├── visualize.py     # Mermaid diagram rendering
│   ├── network.py       # NetworkResolver protocol
│   ├── jobs.py          # JobSpec, package_job
│   ├── runner.py        # hydrate_job, BudgetTracker, create_result_shard
│   └── backends/
│       ├── ipfs.py      # IPFS / Kubo backend
│       └── s3.py        # S3 backend (AWS object storage)
├── geo/            # Geographic types and view shards (experimental)
├── agents/         # Agent-harness recorders (Claude Code, Kiro hooks to traces)
├── ingest/         # Document ingestion (PDF)
├── integrations/   # Third-party memory-provider adapters (mempalace, ...)
├── kb/             # Knowledge base CRUD
├── llm/            # LLM provider abstraction (Anthropic)
├── models/         # DocumentAtom, KnowledgeProject
├── secrets/        # OS keychain API key management
├── sw_vocab/       # Terminology canonicalization for spiritwriter's own docs
└── stopwords.py    # Centralized stopword list
```

## Integrations

spiritwriter ships a pluggable memory-provider protocol (`spiritwriter/integrations/base.py`) so any external memory system can be backed by content-addressed shards. One adapter is in-tree:

- **[mempalace](https://github.com/aaronmarkham/mempalace)** — atomic memory store with decay-based recall and contextual entity weighting. The `spiritwriter/integrations/mempalace/` adapter wires it to the shard store and entity registry.

The same protocol can plug in **Mem0**, **Zep**, **Mastra**, or any custom memory layer — implement `MemoryProvider` and `MemoryBackend`, and spiritwriter handles shard storage, entity resolution, encryption, and tracing underneath.

## Recording Kiro sessions

`spiritwriter-kiro-hook` records [Kiro](https://kiro.dev) sessions (IDE and CLI) as hash-chained traces in the same frame schema as the Claude Code recorder, so existing readers and `verify_chain` work unchanged. Every frame carries `source: "kiro"`. Needs POSIX (Linux, macOS).

```bash
pip install spiritwriter                 # provides the spiritwriter-kiro-hook command
cd /path/to/your/workspace
spiritwriter-kiro-hook install --dry-run # show what would be written
spiritwriter-kiro-hook install           # writes .kiro/hooks/spiritwriter.json
```

`install` writes one command hook per trigger (`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, `SessionEnd`; an empty matcher means all tools), each with an absolute path to the executable so Kiro's shell `PATH` does not matter. It only touches `spiritwriter.json`, is idempotent, and `--uninstall` removes only its own hooks. `--triggers A,B` registers exactly those triggers. Besides the six defaults it accepts `PreTaskExec`, `PostTaskExec`, `PostFileCreate`, `PostFileSave`, `PostFileDelete` and `Manual`, which are not installed by default (for example `--triggers SessionStart,PreToolUse,PostToolUse,Stop,SessionEnd,PostTaskExec,PostFileSave`).

| Where | Command | Notes |
|-------|---------|-------|
| Kiro IDE | `spiritwriter-kiro-hook install [WORKSPACE]` | The IDE reads workspace hooks from `.kiro/hooks/`. Run it once per workspace. |
| Kiro CLI | `spiritwriter-kiro-hook install --global` | Writes `~/.kiro/hooks/` (CLI V3 loads global hooks; workspace hooks work too). |

**Install it exactly once.** Kiro merges hooks from every scope: the global directory and each root of a multi-root workspace. Define spiritwriter once across all of them, either with `install --global` to trace every workspace, or in exactly one root to trace just that multi-root workspace. Do not combine a global install with a workspace install, and do not install it in every root: each copy runs, so every tool event is recorded twice.

**Activation.** A full Kiro restart is not needed; running sessions pick up hook changes. A new agent session is the reliable boundary, and you need one to see `SessionStart`.

**Virtual environments.** The hook stores the absolute path of the executable that was found at install time. If you move, delete or replace that environment, run `install` again. The `|| true` guard makes a broken path fail silently, so nothing will warn you that recording has stopped.

**Trace location.** `~/.spiritwriter/kiro/traces/<session_id>.jsonl` (directory 0700, files 0600). Override with `--dir DIR` or `SPIRITWRITER_KIRO_TRACE_DIR`. When a payload has no `session_id`, the session key is `kiro-<sha256(cwd)[:12]>-<YYYYMMDD>`, so one workspace's events for one local day share a trace.

**Event mapping.** The trace uses Claude-style event names: `SessionStart` (alias `agentSpawn`) to `session_started`, `UserPromptSubmit` to `prompt_submitted`, `PreToolUse` to `tool_call`, `PostToolUse` to `tool_result`, `Stop` to `turn_completed`, `SessionEnd` to `session_ended`; anything else is a `hook_event`. The extra triggers map to `spec_task_started` / `spec_task_completed` (`PreTaskExec` / `PostTaskExec`, with `spec_name`, `task_name`, `task_success` and `tool_kind: "spec"`), `file_created` / `file_saved` / `file_deleted` (`PostFileCreate` / `PostFileSave` / `PostFileDelete`, with `file_path`) and `manual_trigger` (`Manual`). `Stop` records `user_decision` when Kiro sends it and `SessionEnd` records `reason`. Tool ids get a `tool_kind` (`read`, `write`, `shell`, `web`, `spec`, `spawn`, `mcp`, `other`). `spawn` marks Kiro's `invoke_sub_agent` and `subagent_response` tools; `report_progress` stays `other`. A Kiro MCP tool id (`mcp_<server>_<tool>`) is always recorded as `mcp_id`; `mcp_server` and `mcp_tool` are added only when the id starts with a server configured in `<workspace>/.kiro/settings/mcp.json` or `~/.kiro/settings/mcp.json` (names normalized as Kiro does: whitespace and `-` to `_`, other characters dropped, lowercase; the longest match wins). When the id is 64 or more characters (truncated) or ends in `_<digits>` (a collision suffix, or a real name such as `get_page_2`), the server is still set and `mcp_tool` is the remainder, but `mcp_tool_exact: false` is added because the tool part may be cut short or carry a suffix. The field is omitted when the tool part is trustworthy. `PostToolUse` records `tool_response`, redacted and capped at 2000 characters, and derives `ok` when the response says so (`success`, `is_error`, `error`, `exit_code`, `status`). Raw tool inputs and prompts are never written, only hashes, lengths and a scrubbed short summary.

**Session lineage.** This follows the Claude Code recorder's naming. Kiro runs each hook with `KIRO_SESSION_ID` set to the root conversation id. The trace's `session_id` (also its `run_id` and file name) is that root, else the payload `session_id`. When the payload `session_id` differs from the root, `agent_id` is the payload `session_id`, so a child conversation nests under its root; otherwise `agent_id` is `main`. There is no separate root field. Raw capture also records `KIRO_SESSION_ID` as received. This is unconfirmed for subagents: a live run did not show a subagent with a distinct `session_id`.

**The hook never interferes with Kiro.** It always exits 0, prints nothing to stdout (Kiro adds a successful hook's stdout to the agent's context) or stderr, has no way to block a tool call, gives up reading stdin after 2 seconds, ends itself after 5 if stdin stalls (the timer is cancelled once the payload is read, so it can never interrupt a trace write), and registers with a 10 second Kiro-side timeout. The registered command is `<hook> --event <Trigger> >/dev/null 2>&1 || true`, so even a broken interpreter or a failed import cannot exit non-zero (Kiro reads a `Stop` hook that exits 1 as "keep working") or print a traceback. Re-running `install` upgrades hooks written with the older, unguarded command in place. Failures go to `errors.log` in the trace directory (`~/.spiritwriter/kiro/traces/errors.log` by default); set `SPIRITWRITER_KIRO_DEBUG=1` to also print them to stderr.

**Raw capture (opt-in).** Recorded traces only keep fields the adapter knows, so raw capture shows what Kiro really sends. To use it, set `SPIRITWRITER_KIRO_RAW=1` in the environment Kiro runs in, or install with `spiritwriter-kiro-hook install --raw`. Each hook call then appends one line to `~/.spiritwriter/kiro/raw-capture.jsonl` (override with `SPIRITWRITER_KIRO_RAW_FILE`): the stdin JSON, the trigger, the payload's keys, whether a prompt arrived (the payload `prompt` field, else the legacy `USER_PROMPT` variable) and its length, and the names (not values) of `KIRO*` environment variables. Secret shapes and secret-named keys are redacted, long strings are capped, and prompt text is left out unless you also set `SPIRITWRITER_KIRO_RAW_PROMPTS=1` (then it is included, redacted). Still, the file holds your tool inputs, and one global file accumulates them across every workspace. Use raw capture as temporary diagnostics: when you have what you need, reinstall without `--raw` and delete the capture file.

**Known gaps.**
- The shape of `tool_response` varies by tool. `ok` is derived only when the response says so; otherwise the frame has `ok_unknown: true`. Results are paired to calls by `tool_use_id` when Kiro sends one, else by `args_sha256`. The stored response is redacted for secret shapes but can still contain file contents or command output (capped at 2000 characters), so treat traces as private.
- Subagent lineage rests on `KIRO_SESSION_ID` and is unconfirmed: in a live IDE run, invoking a subagent produced no distinct `agent_id`.
- `SessionStart` and `SessionEnd` have not been captured live in the IDE yet; that needs a fresh agent session after installing. `SessionEnd` is documented for CLI V3 only, and the IDE may not fire it.
- A live `Stop` carried only the common fields (`session_id`, `hook_event_name`, `cwd`), so `user_decision` is optional and often absent.
- MCP ids are split only for servers found in the two `mcp.json` files; servers defined elsewhere keep just `mcp_id`. Truncated or numerically suffixed ids are split with `mcp_tool_exact: false`, so per-tool metrics should treat those tool names as approximate.
- Built-in tool ids are not documented, so `tool_kind` is a name heuristic. Power tools are marked only if the payload says so.
- Kiro does not run v2 hooks in an untrusted workspace.

## Used By

Two postures, several products:

- **[frio.help](https://frio.help)** — *zero-knowledge.* Jail-roster monitoring with encrypted search shards and fuzzy name matching; the operator can't see who searched.
- **[news.spiritwriter.ai](https://news.spiritwriter.ai)** / **[texascrime.org](https://texascrime.org)** — *fully transparent.* Source → agent → variant news with public lineage and cross-consumer shard sharing.
- **[podcasts.spiritwriter.ai](https://podcasts.spiritwriter.ai)** — AI-generated podcasts from multi-agent video production.
- **[Claude Studio Producer](https://github.com/aaronmarkham/claude-studio-producer)** — media production pipeline; the canonical worked example in [traced-workflows.md](docs/traced-workflows.md).

## Tests

```bash
python -m pytest tests/ -v                              # full suite
python -m pytest tests/test_demos.py -v                 # the demos above
python -m pytest tests/test_ipfs_backend.py -v -m ipfs  # IPFS integration (requires Kubo)
python -m pytest tests/test_s3_backend.py -v             # S3 backend (in-memory fake, no AWS)
```

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for release notes (0.8.0+). Pre-1.0 SemVer: **minor** for breaking changes, **patch** for additive/non-breaking changes.

## License

Apache 2.0
