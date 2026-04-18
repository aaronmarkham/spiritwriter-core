# Lilit Test Plan — spiritwriter-core Integration Testing

**Date:** 2026-04-17
**Goal:** Exercise spiritwriter-core standalone and with MemPalace in real OpenClaw sessions. Collect token savings data, friction points, and entity resolution accuracy for open source launch.

## Setup

```bash
# Ensure both are installed
pip install -e ".[dev,sealed,network]"
pip install mempalace

# Verify integration auto-discovery
python -c "from spiritwriter.integrations import available_providers; print(available_providers())"
# Should show: {'mempalace': <MemPalaceProvider ...>}
```

## Test 1: Standalone Spiritwriter (No MemPalace)

**What we're measuring:** Token efficiency of structured atoms vs raw text. Can an agent use ShardStore as its primary memory across a multi-conversation task?

### Steps

1. Pick a real multi-step task (a code review, a research question, something that spans 3+ conversation turns).

2. At the end of each turn, extract what you learned into a shard:

```python
from spiritwriter.trace.shard import MemoryShard, ShardAtom, AtomKind, DecayClass
from spiritwriter.trace.store import ShardStore

store = ShardStore("~/.openclaw/shards")

shard = MemoryShard(
    atoms=[
        # Extract structured facts, not raw conversation
        ShardAtom(text="...", kind=AtomKind.FACT,
                  entity="...", key="...", value="..."),
        # Decisions with rationale
        ShardAtom(text="...", kind=AtomKind.DECISION,
                  entity="...", key="...", value="..."),
    ],
    scope="project:whatever-you-are-working-on",
    origin="lilit",
    decay_class=DecayClass.STABLE,
)
ref = store.put(shard)
```

3. At the start of the next turn, hydrate your context:

```python
# Load all shards for this project
shards = store.by_scope("project:whatever-you-are-working-on")
for s in shards:
    context = s.hydrate_context()
    print(f"  {s.shard_id[:12]}: {s.token_estimate} tokens, {len(s.atoms)} atoms")
```

### What to record

- **Per shard:** atom count, `token_estimate`, scope, decay class
- **Per turn:** how many shards hydrated, total tokens injected
- **Comparison:** estimate what the raw conversation text would have been (word count / 4). What's the ratio?
- **Qualitative:** Did the structured atoms capture what you needed? Did you miss anything that raw text would have had?
- **Friction:** Anything awkward about the API? Missing convenience methods?

### Expected outcome

Structured atoms should be 5-10x more token-efficient than raw conversation recall. If it's less than 3x, note what's inflating it (long text fields? too many atoms?).

---

## Test 2: With MemPalace (Semantic Search)

**What we're measuring:** Does the MemPalace integration actually help find the right shards? Is the auto-discovery seamless? What's the retrieval quality?

### Prerequisite

A MemPalace palace with some content. Either use the spiritwriter-core docs palace we already mined, or mine your own project:

```bash
mempalace init ~/your-project --yes
mempalace mine ~/your-project
```

### Steps

1. Search through the provider protocol:

```python
from spiritwriter.integrations import get_provider
from spiritwriter.integrations.base import SearchQuery

mp = get_provider("mempalace")
print(f"available: {mp.is_available()}, drawers: {mp.count()}")

results = mp.search(SearchQuery(text="your natural language query", top_k=5))
for r in results:
    print(f"  {r.score:.3f} | {r.text[:80]}...")
```

2. Try several queries — factual, conceptual, temporal:
   - A specific fact: "what encryption does spiritwriter use?"
   - A concept: "how does entity resolution work?"
   - Something vague: "security concerns"

3. Compare: would `store.by_scope()` or `store.get()` have found the same thing? Semantic search should find things that scope/key lookup can't.

### What to record

- **Per query:** query text, top result score, was the right document in top 5?
- **Recall:** out of N queries, how many found the right answer in top 5? (R@5)
- **Latency:** any noticeable delay from the integration layer?
- **Auto-discovery:** did `get_provider("mempalace")` just work, or did you have to configure anything?
- **Friction:** anything confusing about the SearchQuery/SearchResult API?

---

## Test 3: Entity Resolution (Phalanx)

**What we're measuring:** Does Phalanx correctly link entities across conversations? Any false merges or missed matches?

### Steps

1. Set up a registry for people you mention in your work:

```python
from spiritwriter.trace.canonicalize import CanonicalRegistry, CanonicalSchema

schema = CanonicalSchema(
    name="person",
    ess_fields=["name", "role"],
    fuzzy_fields={"name": 0.85},
    context_fields=["project", "team"],
)

registry = CanonicalRegistry("~/.openclaw/entities.db", schema)
```

2. Across your sessions, resolve people as you encounter them:

```python
candidate = {"name": "Aaron", "role": "founder", "project": "spiritwriter"}
result = registry.resolve(candidate)
print(f"  tier: {result.tier.value}, confidence: {result.confidence}")
registry.upsert(candidate, result, source_name="session", source_id="session-N")
```

3. Intentionally test edge cases:
   - Same person, different context (different project/room)
   - Name variants ("Max" vs "Maxwell")
   - Different people with similar names
   - Same name, different role

### What to record

- **Per resolution:** name, tier (T1/T2/T3/T4/NO_MATCH), confidence, correct?
- **False merges:** did Phalanx merge two people who are actually different? (this is the critical failure mode)
- **Missed matches:** did it create a new entity for someone already in the registry?
- **Stats at the end:**

```python
print(registry.stats())
# entities, sightings, merges, sources
```

---

## Test 4: ShardBackend (MemPalace + spiritwriter bridge)

**What we're measuring:** Does the ShardBackend drop-in work as a MemPalace storage layer? Does encryption survive restart? Does lineage tracking work?

### Steps

1. Create a ShardBackend and add some drawers:

```python
from spiritwriter.integrations.mempalace import ShardBackend

backend = ShardBackend("~/.openclaw/shard-palace")

backend.add(
    documents=["some verbatim text from your session"],
    ids=["drawer_001"],
    metadatas=[{"wing": "my_project", "room": "planning"}],
)

# Check content address
print(backend.get_shard_id("drawer_001"))
print(backend.stats())
```

2. Test upsert and lineage:

```python
backend.upsert(
    documents=["updated version of that text"],
    ids=["drawer_001"],
    metadatas=[{"wing": "my_project", "room": "planning"}],
)

history = backend.get_drawer_history("drawer_001")
print(f"revisions: {len(history)}")
for h in history:
    print(f"  {h.shard_id[:12]} | parent={h.parent_shard_id and h.parent_shard_id[:12]}")
```

3. Test encrypted backend:

```python
from spiritwriter.trace.crypto import generate_job_key

key = generate_job_key()
enc_backend = ShardBackend("~/.openclaw/encrypted-palace", encryption_key=key)
enc_backend.add(documents=["secret stuff"], ids=["secret_001"], metadatas=[{"wing": "private"}])
print(f"count: {enc_backend.count()}")

# Simulate restart — create new backend with same path + key
del enc_backend
enc_backend2 = ShardBackend("~/.openclaw/encrypted-palace", encryption_key=key)
print(f"count after restart: {enc_backend2.count()}")
result = enc_backend2.get(ids=["secret_001"])
print(f"recovered: {result['documents']}")
```

### What to record

- Does `add/upsert/get/delete/count` behave as expected?
- Does encrypted backend survive restart?
- Any errors or surprising behavior?

---

## Report Template

After completing the tests, write up findings in this format:

```markdown
# spiritwriter-core Integration Test Report
Date: YYYY-MM-DD
Tester: Lilit

## Token Efficiency (Test 1)
- Shards created: N
- Total atoms: N
- Avg tokens per shard (hydrated): N
- Estimated raw text equivalent: N tokens
- Compression ratio: Nx

## MemPalace Search (Test 2)
- Queries tested: N
- R@5 (correct in top 5): N/N = X%
- Auto-discovery: worked / needed config / failed
- Notable friction: ...

## Entity Resolution (Test 3)
- Entities resolved: N
- T1 (exact): N
- T2 (strong): N
- T3 (fuzzy): N
- False merges: N (describe)
- Missed matches: N (describe)

## ShardBackend (Test 4)
- Basic CRUD: pass / fail
- Lineage tracking: pass / fail
- Encrypted restart: pass / fail
- Issues: ...

## Bugs Found
- ...

## API Friction
- ...

## Suggestions
- ...
```

Save the report to `docs/test-reports/lilit-2026-04-XX.md` and any raw data (shard counts, query results) alongside it.
