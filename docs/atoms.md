# Atoms

Atoms in Spiritwriter are the smallest units of understanding in a dialogue or document. These are more than objects/nouns and actions/verbs or entities; think more like a statement that's a decision, fact, convention, or preference. Memories in spiritwriter can be a collection of atoms, a skill file, context data, or some combination.

Other memory systems utilize a similar process to "...atomize complex dialogue flows into self-contained factual statements." (SimpleMem 2026, §2.1 Semantic Structured Compression). SimpleMem proves improved context management by reducing redundancy through compression while maintaining semantic accuracy. 

[introduce more memory management options like Zep]

Entity Sense Signatures (ESS) are how Spiritwriter achieves fast, reliable, local compression of atoms. Figuring which atoms are talking about the same thing can be achieved by applying disambiguation and coreference resolution (which of these are not like the other and which are "pretty close"). The basis for this kind of signature-based entity resolution was introduced in the 2018 paper by Zhang, et al, "Scalable Entity Resolution Using Probabilistic Signatures on Parallel Databases". Spiritwriter uses a simplified approach for this with a local sqlite db. 

Named entities frequently collide with common terms: "Bear" the dog, "bear" the animal, "Bear" the brand. This isn't an edge case — it occurs constantly with pet names, product names, place names, and people names. Without explicit sense disambiguation, simple entity alignment will group all "Bear" atoms together, and a naive scoring system would see high entity overlap between facts about completely different references. ESS adds a small number of tokens (~80) per atom at extraction time but eliminates entire categories of false merges that would otherwise require expensive LLM calls or, worse, silently corrupt the canonical registry. 

In 2024, another paper related to entity alignment came out and discusses using a knowledge graph to handle the ETL (extract/transform/load) and then canonicalize. "Extract, Define, Canonicalize: An LLM-based Framework for Knowledge Graph Construction" calls this EDC. 

Graph architects prefer using a strict ontology for mapping entities and creating associations. This works well in a pre-defined or well-known space, but it falls apart when new content is considered that doesn't align with the existing schema. EDC solves this problem though muli-stage LLM calls to first extract important data, then define a schema based on this data, and finally create the ontology by cannonicalizing the terms.

Spiritwriter deviates from these flow by using ESS instead. While the EDC technique may provide higher rates of precision, it comes at the cost of complexity. An atom will have "scope" and this scope provides a cheap way to graph relationships without all the overhead. A full KG can be useful, but when you're dealing with a conversation or a specific document, the back and forth isn't necessary, and keeping your context lean and in-memory is preferable. Conversely, it's easy to teach a delegate about spiritwriter primatives without actually installing anything or having access to a database.

[proven benchmarks about this]

[examples of atoms]

[benchmarks]
LoCoMo
LongMemEval-S

[cmc-lite]
* openclaw and Lilit references - why are they here? can we just talk about vec0?


[phalanx]

For each memory file:
    1. Read file, chunk into ~2000 char windows with 400 char overlap
    2. For each chunk:
        a. Run extraction (single exec, <60s)
        b. Get atoms back
        c. Store raw atoms to disk (checkpoint)
    3. After all chunks: run consensus across passes
    4. Upsert consensus atoms into registry
    5. Mark file as processed
    6. Next file


[example/extract_memory.py] The whole example is super dated... should refactor/remove
contains references to shingles but no other code does... this should be swapped out.
also has a hard-coded path: SW_PATH = Path.home() / "Documents" / "GitHub" / "spiritwriter-core"
has a hard-coded openai dependency:
from openai import OpenAI
also the memory location is hardcoded:
WORKSPACE = Path(__file__).parent.parent
STORE_PATH = WORKSPACE / "shards"
MEMORY_DIR = WORKSPACE / "memory"
MEMORY_MD = WORKSPACE / "MEMORY.md"

flavor_examples.py - not sure about this... needs better introduction as to why/what - mentions a flavor document without a link to one.

examples/0*_abcd >> these all are toy examples and won't do enough... we should assume that the user has setup their claude environment so they can run these cheaply and see real inputs/outputs; the mermaid diagrams should actually show the fan out and parallelism
example 5 doesn't have a readme


remove the toorcamp files from the repo
actually remove all of the spec files in favor of just docs


workshop/
these seem pretty good but they're quite focused on the tracing features and not enough on the audit process and how it might fail, or really how you scale this kind of thing with more confidence.
