# Skill: Spiritwriter Shards

Content-addressed memory shards — the core unit of distributable agent knowledge.

## When to Use

- You need to **store structured knowledge** (facts, decisions, conventions, preferences, instructions)
- You need to **pass context to sub-agents** without sending full documents
- You need to **hydrate context** from shard references in a task
- You need to **query knowledge** by scope, ref name, or content address

## Install

```bash
pip install -e /path/to/spiritwriter-core
```

## Concepts

| Concept | What it is |
|---------|-----------|
| **ShardAtom** | Smallest unit of knowledge. Has `text`, `kind`, optional `entity`/`key`/`value`. Content-addressed (SHA-256). |
| **MemoryShard** | Bundle of atoms with scope, origin, decay class, tags. Immutable — edits create new shards. Content-addressed. |
| **ShardRef** | Lightweight pointer (shard_id + scope + label). Pass these instead of full content. |
| **ShardStore** | File-based content-addressed store. Git-style object layout (`ab/cd1234...json`). Named refs like git branches. Optional network resolver for L2 fallback (see `skills/network/SKILL.md`). |
| **Named Ref** | Human-readable pointer to a shard (e.g., `project-csp` → shard_id). Updated when shards are superseded. |

### Atom Kinds

| Kind | Use for |
|------|---------|
| `fact` | Structured entity/key/value triples |
| `decision` | Choices with rationale |
| `convention` | Always/never rules |
| `preference` | User preferences |
| `entity` | Named entity information |
| `context` | Freeform contextual knowledge |
| `instruction` | How-to steps, workflows, commands |
| `checkpoint` | Temporary state snapshots |

### Decay Classes

| Class | TTL | Use for |
|-------|-----|---------|
| `permanent` | Never | Architecture decisions, identities |
| `stable` | 90 days | Project details, relationships |
| `active` | 14 days | Current tasks, sprint goals |
| `session` | 24 hours | Debugging context |
| `checkpoint` | 4 hours | Pre-flight state saves |

## Python API

### Create a shard

```python
from spiritwriter.trace.shard import MemoryShard, ShardAtom, AtomKind, DecayClass

shard = MemoryShard(
    atoms=[
        ShardAtom(
            text="CSP uses namespace packages, not git submodules",
            kind=AtomKind.DECISION,
            entity="claude-studio-producer",
            key="package_strategy",
            value="namespace_packages",
        ),
        ShardAtom(
            text="Always use the librarian path in the assembler",
            kind=AtomKind.CONVENTION,
            entity="csp-assembler",
            key="architecture",
        ),
    ],
    scope="project:csp",
    origin="lilit",
    decay_class=DecayClass.STABLE,
    tags=["CSP project context"],
)
```

### Store and retrieve

```python
from spiritwriter.trace.store import ShardStore

store = ShardStore("/path/to/shard-directory")

# Store (idempotent — same content = same ID)
ref = store.put(shard)
print(ref.shard_id)  # SHA-256 content address

# Set a named ref for easy lookup
store.set_ref("project-csp", ref.shard_id)

# Retrieve by ID
shard = store.get(ref.shard_id)

# Retrieve by named ref
shard = store.resolve_ref("project-csp")

# Query by scope
shards = store.by_scope("project:csp")

# List everything
store.list_scopes()   # all scopes
store.stats()         # summary with counts
```

### Hydrate context for an agent

```python
# From named refs
shard = store.resolve_ref("project-csp")
context = shard.hydrate_context()
# Returns XML-tagged, structured text ready for prompt injection

# From multiple refs
refs = [store.resolve_ref(name).ref for name in ["project-csp", "agent-tools"]]
context = store.hydrate(refs)
```

### Extract atoms from text

```python
from spiritwriter.trace.extract import extract_atoms

atoms = extract_atoms("""
We decided to use SQLite with vec0 for memory search.
Aaron prefers namespace packages over git submodules.
""")
# Returns list[ShardAtom] with detected kinds
```

## CLI (hydrate.py)

If a `hydrate.py` script exists in the shard directory:

```bash
# List available refs and scopes
python3 shards/hydrate.py --list

# Hydrate specific refs (outputs injectable context)
python3 shards/hydrate.py project-csp agent-tools

# Hydrate by scope
python3 shards/hydrate.py --scope project:csp

# Hydrate everything
python3 shards/hydrate.py --all
```

## Sub-Agent Protocol (SHARD_REFS)

When spawning sub-agents, include shard refs in the task text:

```
SHARD_REFS: project-csp agent-tools
Implement the new feature based on project conventions.
```

The sub-agent hydrates refs on startup:
```bash
python3 shards/hydrate.py project-csp agent-tools
```

Or use `spawn_with_shards.py` to auto-inject:
```bash
python3 shards/spawn_with_shards.py "SHARD_REFS: project-csp\nDo the task"
```
This parses refs, hydrates them, wraps content in `<hydrated-context>` XML, and strips the refs line.

## Storage Layout

```
shard-directory/
  shards/
    ab/
      cd1234...json      # shard files (content-addressed)
  refs/
    project-csp.ref      # named ref → shard_id
    agent-tools.ref
  index.json             # scope → [shard_id] mapping
```

## Key Properties

- **Immutable**: Content changes produce a new shard with a new ID
- **Content-addressed**: SHA-256 of (atoms + scope + origin). Same content = same ID. Dedup is free.
- **File-based**: No database required. Git-style object layout.
- **Network-ready**: Optional IPFS resolver for L2 fallback. See `skills/network/SKILL.md`.
- **Pull-based**: Agents get refs (pointers), not content. They pull what they need.
- **Scoped**: Every shard has a scope for access control boundaries.

## Source Files

- `spiritwriter/trace/shard.py` — MemoryShard, ShardAtom, ShardRef, DecayClass, AtomKind
- `spiritwriter/trace/store.py` — ShardStore (file-based content-addressed storage, optional network resolver)
- `spiritwriter/trace/extract.py` — Knowledge extraction from text → atoms
- `spiritwriter/trace/network.py` — NetworkResolver protocol, ShardLocation, ShardManifest
