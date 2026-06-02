"""LLM-aligned familiarization — docs → high-quality, deduped shard.

The "get familiar with my app" path, built along the CMC spec
(``docs/specs/cmc-spec-v0.1.md`` / ``cmc-lite-v0.1.md``) rather than the
flat-atom + fuzzy-string approach those specs were written to replace.

  1. **Extract** (``extract_atoms_llm``) — an LLMProvider reads each doc
     and returns atoms enriched with the two load-bearing CMC signals:
     a ``key_definition`` (the EDC "Define" step) and an entity ``sense``
     (sense_type / scoped_to / domain — the Bear-Problem disambiguator).
  2. **Align** (``align_atoms``) — *tiered* resolution, no embedding stack:
       a. exact-after-normalization merge (deterministic),
       b. **sense gate** — incompatible senses can't be the same entity,
       c. **LLM adjudication** of the ambiguous residual (SAME/DIFFERENT/
          SUBSUMES). LLMs fire only on the hard pairs, per Graphiti's
          "deterministic first, LLM for ambiguity" pattern — not on
          everything (that's the variance/token-burn trap, CMC §9.1).
  3. **Store** (``familiarize``) — one ``MemoryShard`` under a named ref.

Why LLM adjudication instead of string fuzzy: string similarity on slot
keys/entities yields only 20-30% recall on true duplicates (CMC §1).
Comparing *definitions and senses* — which an LLM does directly — is how
``database``/``datastore`` collapse and ``Postgres``/``PostgreSQL``
resolve while ``Bear`` (dog) stays apart from ``bear`` (animal).

The provider is injected: the same code runs against a live
``AnthropicProvider`` or a ``MockLLMProvider`` for offline/CI. With
``provider=None`` (or ``adjudicate=False``) alignment degrades to the
deterministic exact tier only.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from spiritwriter.llm.base import LLMProvider
from spiritwriter.llm.anthropic import JSONExtractor
from spiritwriter.ingest import load_documents
from spiritwriter.fabric.shard import (
    AtomKind, DecayClass, EntitySense, MemoryShard, ShardAtom, ShardRef,
)
from spiritwriter.fabric.store import ShardStore


# ── Extraction ──────────────────────────────────────────────────────


EXTRACTION_PROMPT = """\
You are building a durable knowledge brief about a software project so an
AI coding agent can get oriented instantly in future sessions — without
re-reading the docs. Read the document below and extract its DURABLE
knowledge as atoms.

Extract only things worth remembering next session: architectural facts,
decisions and their rationale, always/never conventions, and concrete
backlog tasks. Skip prose, restatements, and filler.

For each atom emit:
  - kind: one of fact | decision | convention | preference | instruction
  - entity: the thing it's about. Use a STABLE, consistent name and reuse
    the same spelling across atoms.
  - key: a short snake_case attribute name (e.g. "database", "auth_method")
  - value: the answer, if the atom is a fact/decision (else omit)
  - key_definition: a one-sentence definition of what `key` means, specific
    enough to distinguish it from similar keys (e.g. for "database":
    "The primary persistent datastore the service reads and writes.")
  - text: a one-sentence, self-contained natural-language statement
  - confidence: 0.0-1.0, how clearly the document states this
  - sense: a disambiguator for `entity`, with:
      - sense_type: proper_name | common_noun | brand | organization | place
      - scoped_to: the parent entity that "owns" this name, or null
      - domain: a category like "software_project", "datastore", "person"

Return ONLY JSON:
{
  "atoms": [
    {"kind": "fact", "entity": "...", "key": "...", "value": "...",
     "key_definition": "...", "text": "...", "confidence": 0.9,
     "sense": {"sense_type": "organization", "scoped_to": null,
               "domain": "software_project"}}
  ]
}

Document (source: {source_ref}):
---
{text}
---
"""


def build_extraction_prompt(text: str, source_ref: str) -> str:
    """Render the extraction prompt for one document."""
    return EXTRACTION_PROMPT.replace("{source_ref}", source_ref).replace("{text}", text)


def _sense_from_obj(obj: Any) -> Optional[EntitySense]:
    if not isinstance(obj, dict):
        return None
    stype = obj.get("sense_type")
    if not stype:
        return None
    ctx = obj.get("context") or obj.get("distinguishing_context") or []
    return EntitySense(
        sense_type=str(stype).strip().lower(),
        scoped_to=(obj.get("scoped_to") or None),
        domain=(obj.get("domain") or None),
        gloss=(obj.get("gloss") or None),
        context=[str(c) for c in ctx] if isinstance(ctx, list) else [],
    )


def _atom_from_obj(obj: Dict[str, Any], source_ref: Optional[str]) -> Optional[ShardAtom]:
    """Map one LLM JSON object to a ShardAtom. Returns None if unusable."""
    text = (obj.get("text") or "").strip()
    if not text:
        return None
    raw_kind = str(obj.get("kind", "context")).strip().lower()
    try:
        kind = AtomKind(raw_kind)
    except ValueError:
        kind = AtomKind.CONTEXT
    try:
        confidence = float(obj.get("confidence", 1.0))
    except (TypeError, ValueError):
        confidence = 1.0
    # Strip identity-bearing fields: stray whitespace on entity/key would
    # otherwise poison exact (entity, key, kind) dedup and the canonical
    # display name (a single mention with a trailing space could win).
    return ShardAtom(
        text=text,
        kind=kind,
        entity=((obj.get("entity") or "").strip() or None),
        key=((obj.get("key") or "").strip() or None),
        value=((obj.get("value") or "").strip() or None),
        confidence=max(0.0, min(1.0, confidence)),
        source_ref=source_ref,
        key_definition=(obj.get("key_definition") or None),
        sense=_sense_from_obj(obj.get("sense")),
    )


async def extract_atoms_llm(
    text: str,
    provider: LLMProvider,
    *,
    source_ref: Optional[str] = None,
    model: Optional[str] = None,
) -> List[ShardAtom]:
    """Extract structured atoms from one document via an LLMProvider.

    Robust to the usual LLM-JSON quirks (code fences, trailing prose) via
    ``JSONExtractor``. Returns ``[]`` rather than raising if the model
    returns nothing parseable — one bad doc shouldn't sink the run.
    """
    prompt = build_extraction_prompt(text, source_ref or "unknown")
    kwargs = {"model": model} if model else {}
    response = await provider.query(prompt, **kwargs)
    try:
        data = JSONExtractor.extract(response)
    except ValueError:
        return []
    atoms: List[ShardAtom] = []
    for obj in data.get("atoms", []):
        if isinstance(obj, dict):
            atom = _atom_from_obj(obj, source_ref)
            if atom is not None:
                atoms.append(atom)
    return atoms


# ── Text / sense helpers ────────────────────────────────────────────


_PUNCT = str.maketrans({c: " " for c in "-_/.,:;()[]{}'\"`"})

# Sense-type pairs that may still be the same referent (CMC §5.5.4).
_COMPATIBLE_SENSE_PAIRS = {
    ("proper_name", "brand"), ("brand", "proper_name"),
    ("proper_name", "organization"), ("organization", "proper_name"),
}

# Function/filler words dropped before comparing definitions, so blocking
# keys off content tokens — not "the/rule/where" — keeping LLM calls to
# genuinely related pairs. Deliberately NOT spiritwriter.stopwords: that
# list is tuned for theme/topic classification; this one is a tiny
# definition-blocking filter with different members (e.g. "rule", "value").
_STOP = {
    "the", "a", "an", "of", "to", "and", "or", "for", "in", "on", "is", "are",
    "be", "this", "that", "it", "its", "where", "when", "what", "which", "may",
    "must", "can", "with", "by", "as", "at", "from", "into", "about", "used",
    "use", "uses", "rule", "value", "thing", "given", "relative", "service's",
}


def _norm(s: str) -> str:
    return " ".join(s.translate(_PUNCT).lower().split())


def _tokens(s: str) -> set:
    return {t for t in _norm(s).split() if len(t) > 2 and t not in _STOP}


def _defs_related(a: str, b: str, threshold: float) -> bool:
    """Stricter relatedness for atom granularity pairs: two shared content
    tokens, or high definition similarity. Tighter than ``_lex_related``
    so the LLM is only asked about genuinely candidate duplicates."""
    if len(_tokens(a) & _tokens(b)) >= 2:
        return True
    return _ratio(a, b) >= max(threshold, 0.72)


def _ratio(a: str, b: str) -> float:
    """Cheap char-bigram similarity (SequenceMatcher-free, deterministic)."""
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ba = {a[i:i + 2] for i in range(len(a) - 1)}
    bb = {b[i:i + 2] for i in range(len(b) - 1)}
    if not ba or not bb:
        return 0.0
    return 2 * len(ba & bb) / (len(ba) + len(bb))


def _lex_related(a: str, b: str, threshold: float) -> bool:
    """Could two strings plausibly be the same thing? (blocking pre-filter)"""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na in nb or nb in na:
        return True
    if _tokens(a) & _tokens(b):
        return True
    return _ratio(a, b) >= threshold


def _senses_compatible(a: Optional[EntitySense], b: Optional[EntitySense]) -> bool:
    """Hard gate: can these two entity senses refer to the same thing?"""
    if a is None or b is None:
        return True  # unknown sense → don't gate, let adjudication decide
    if a.sense_type != b.sense_type:
        if (a.sense_type, b.sense_type) not in _COMPATIBLE_SENSE_PAIRS:
            return False
    if a.scoped_to and b.scoped_to and a.scoped_to != b.scoped_to:
        return False
    if a.domain and b.domain and a.domain != b.domain:
        if not (set(a.context) & set(b.context)):
            return False
    return True


def _merge_source_refs(a: Optional[str], b: Optional[str]) -> Optional[str]:
    seen: List[str] = []
    for blob in (a, b):
        if not blob:
            continue
        for part in blob.split(","):
            part = part.strip()
            if part and part not in seen:
                seen.append(part)
    return ", ".join(seen) if seen else None


class _DSU:
    """Tiny union-find over hashable items."""

    def __init__(self, items):
        self.parent = {x: x for x in items}

    def find(self, x):
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


# ── LLM adjudication ────────────────────────────────────────────────


_ENTITY_ADJ_PROMPT = """\
Two entity mentions from a project's docs. Do they refer to the SAME
real-world entity (e.g. the same datastore, service, or component),
possibly under different spellings or abbreviations?

A: "{a}"  (sense: {sa})
B: "{b}"  (sense: {sb})

Reply ONLY JSON: {"verdict": "SAME" | "DIFFERENT", "confidence": 0.0-1.0}
"""

_ATOM_ADJ_PROMPT = """\
Two knowledge atoms about the entity "{entity}", extracted from project
docs. Do they express the SAME underlying fact?

A: key="{ka}" definition="{da}" value="{va}" — "{ta}"
B: key="{kb}" definition="{db}" value="{vb}" — "{tb}"

- SAME: the same fact, possibly phrased differently
- SUBSUMES: one is strictly more specific than the other
- DIFFERENT: genuinely different facts

Reply ONLY JSON:
{"verdict": "SAME" | "SUBSUMES" | "DIFFERENT", "more_specific": "A" | "B" | null, "confidence": 0.0-1.0}
"""


async def _adjudicate_entities(
    provider: LLMProvider, a: str, sa: Optional[EntitySense], b: str, sb: Optional[EntitySense]
) -> Tuple[str, float]:
    prompt = (_ENTITY_ADJ_PROMPT
              .replace("{a}", a).replace("{b}", b)
              .replace("{sa}", sa.sense_type if sa else "unknown")
              .replace("{sb}", sb.sense_type if sb else "unknown"))
    try:
        data = JSONExtractor.extract(await provider.query(prompt))
    except ValueError:
        return "DIFFERENT", 0.0
    return str(data.get("verdict", "DIFFERENT")).upper(), float(data.get("confidence", 0.0) or 0.0)


async def _adjudicate_atoms(
    provider: LLMProvider, entity: str, a: ShardAtom, b: ShardAtom
) -> Tuple[str, Optional[str], float]:
    prompt = (_ATOM_ADJ_PROMPT
              .replace("{entity}", entity)
              .replace("{ka}", a.key or "").replace("{da}", a.key_definition or "")
              .replace("{va}", a.value or "").replace("{ta}", a.text)
              .replace("{kb}", b.key or "").replace("{db}", b.key_definition or "")
              .replace("{vb}", b.value or "").replace("{tb}", b.text))
    try:
        data = JSONExtractor.extract(await provider.query(prompt))
    except ValueError:
        return "DIFFERENT", None, 0.0
    return (str(data.get("verdict", "DIFFERENT")).upper(),
            data.get("more_specific"),
            float(data.get("confidence", 0.0) or 0.0))


# ── Alignment ───────────────────────────────────────────────────────


@dataclass
class AlignmentResult:
    """Outcome of aligning a batch of atoms."""
    atoms: List[ShardAtom]
    input_count: int
    output_count: int
    exact_merges: int                  # deterministic (entity,key,kind) collapses
    entities_merged_by_llm: int        # surface-form clusters joined by adjudication
    facts_merged_by_llm: int           # granularity duplicates joined by adjudication
    llm_calls: int
    review: List[Dict[str, Any]] = field(default_factory=list)  # gated/declined pairs


def _better_keeper(a: ShardAtom, b: ShardAtom) -> bool:
    """Should `a` be preferred over `b` as the surviving atom?"""
    return (a.confidence, len(a.value or ""), len(a.text)) > (
        b.confidence, len(b.value or ""), len(b.text))


async def _canonicalize_entities(
    atoms: List[ShardAtom], provider: Optional[LLMProvider], lex_threshold: float, adjudicate: bool
) -> Tuple[Dict[int, str], int, int, List[Dict[str, Any]]]:
    """Cluster entity-bearing atoms → {atom_index: canonical_display}.

    Per-atom (not per-string) so the sense gate can keep homonyms apart
    even when their surface forms normalize identically — ``Bear`` (dog,
    scoped to Aaron) must not fold into ``bear`` (animal) just because
    both lowercase to the same token. That's the whole point of the gate.
    """
    ent_idx = [i for i, a in enumerate(atoms) if a.entity]
    dsu = _DSU(ent_idx)
    review: List[Dict[str, Any]] = []
    llm_calls = 0
    merged_by_llm = 0

    # Tier 1 — exact-after-normalization fold, but SENSE-GATED: same
    # normalized surface form only folds when senses are compatible.
    by_norm: Dict[str, List[int]] = defaultdict(list)
    for i in ent_idx:
        by_norm[_norm(atoms[i].entity)].append(i)
    for idxs in by_norm.values():
        for a_ in range(len(idxs)):
            for b_ in range(a_ + 1, len(idxs)):
                ia, ib = idxs[a_], idxs[b_]
                if dsu.find(ia) == dsu.find(ib):
                    continue
                if _senses_compatible(atoms[ia].sense, atoms[ib].sense):
                    dsu.union(ia, ib)

    # Tier 2 — LLM adjudication across DIFFERENT normalized clusters that
    # are lexically related and sense-compatible.
    if provider is not None and adjudicate:
        # Known v0.1 trade-off: one representative atom per cluster. If a
        # cluster's rep isn't its lexically-closest member to another
        # cluster (e.g. {"PG","Postgres"} repped by "PG" vs {"PostgreSQL"}),
        # _lex_related can miss a merge that "Postgres"↔"PostgreSQL" would
        # have made. Acceptable for now; revisit with all-pairs blocking if
        # recall matters more than the extra adjudication calls.
        roots: Dict[int, int] = {}
        for i in ent_idx:
            roots.setdefault(dsu.find(i), i)  # one representative atom per cluster
        rep_list = list(roots.values())
        for x in range(len(rep_list)):
            for y in range(x + 1, len(rep_list)):
                ai, aj = rep_list[x], rep_list[y]
                if dsu.find(ai) == dsu.find(aj):
                    continue
                ea, eb = atoms[ai].entity, atoms[aj].entity
                if _norm(ea) == _norm(eb):
                    continue  # same surface, different sense → keep apart
                if not _lex_related(ea, eb, lex_threshold):
                    continue
                if not _senses_compatible(atoms[ai].sense, atoms[aj].sense):
                    review.append({"kind": "entity", "a": ea, "b": eb, "verdict": "SENSE_GATED"})
                    continue
                verdict, conf = await _adjudicate_entities(
                    provider, ea, atoms[ai].sense, eb, atoms[aj].sense)
                llm_calls += 1
                if verdict == "SAME" and conf >= 0.5:
                    dsu.union(ai, aj)
                    merged_by_llm += 1
                else:
                    review.append({"kind": "entity", "a": ea, "b": eb,
                                   "verdict": verdict, "confidence": round(conf, 3)})

    # Canonical display per cluster: longest surface form (tie → alpha),
    # ranked and emitted on the STRIPPED form so a stray-whitespace mention
    # can't win the name or carry padding into storage.
    members_by_root: Dict[int, List[int]] = defaultdict(list)
    for i in ent_idx:
        members_by_root[dsu.find(i)].append(i)
    idx_display: Dict[int, str] = {}
    for members in members_by_root.values():
        disp = sorted(
            {atoms[m].entity.strip() for m in members},
            key=lambda s: (-len(s), s),
        )[0]
        for m in members:
            idx_display[m] = disp
    return idx_display, llm_calls, merged_by_llm, review


async def align_atoms(
    atoms: List[ShardAtom],
    provider: Optional[LLMProvider] = None,
    *,
    lex_threshold: float = 0.6,
    adjudicate: bool = True,
) -> AlignmentResult:
    """Tiered alignment: exact merge → sense gate → LLM adjudication.

    With no provider (or ``adjudicate=False``) only the deterministic
    exact tier runs — safe, offline, but blind to definition/sense
    equivalence. With a provider, ambiguous residual pairs are
    adjudicated by the LLM; the sense gate keeps homonyms apart cheaply
    before any call is made.

    Mutates the atoms in ``atoms`` in place — entity fields are rewritten
    to their canonical display form and surviving atoms' ``source_ref``s
    are unioned. Pass copies if you need the originals intact.
    """
    input_count = len(atoms)

    # Tier 1 — entity canonicalization (sense-gated fold + LLM unions).
    idx_display, ent_calls, entities_merged, review = await _canonicalize_entities(
        atoms, provider, lex_threshold, adjudicate
    )
    for i, disp in idx_display.items():
        atoms[i].entity = disp

    # Tier 2 — atom dedup. Exact (entity,key,kind) first, then LLM
    # adjudication of same-entity / different-key granularity variants.
    n = len(atoms)
    dsu = _DSU(range(n))
    seen: Dict[tuple, int] = {}
    exact_merges = 0
    for idx, a in enumerate(atoms):
        if a.entity is None and a.key is None:
            dkey = ("\x00text", a.text, a.kind)
        else:
            dkey = (a.entity, a.key, a.kind)
        if dkey in seen:
            dsu.union(seen[dkey], idx)
            exact_merges += 1
        else:
            seen[dkey] = idx

    llm_calls = ent_calls
    facts_merged = 0
    if provider is not None and adjudicate:
        groups: Dict[tuple, List[int]] = defaultdict(list)
        for idx, a in enumerate(atoms):
            if a.entity is not None and a.key is not None:
                groups[(a.entity, a.kind)].append(idx)
        for (entity, _kind), idxs in groups.items():
            for i in range(len(idxs)):
                for j in range(i + 1, len(idxs)):
                    ia, ib = idxs[i], idxs[j]
                    if dsu.find(ia) == dsu.find(ib):
                        continue
                    A, B = atoms[ia], atoms[ib]
                    if A.key == B.key:
                        continue  # handled by the exact tier
                    if not _defs_related(A.key_definition or A.key or A.text,
                                         B.key_definition or B.key or B.text, lex_threshold):
                        continue
                    if not _senses_compatible(A.sense, B.sense):
                        continue
                    verdict, _more, conf = await _adjudicate_atoms(provider, entity, A, B)
                    llm_calls += 1
                    if verdict in ("SAME", "SUBSUMES") and conf >= 0.5:
                        dsu.union(ia, ib)
                        facts_merged += 1
                    else:
                        review.append({"kind": "fact", "entity": entity,
                                       "a": A.key, "b": B.key,
                                       "verdict": verdict, "confidence": round(conf, 3)})

    # Collapse each component to one keeper, unioning provenance, first-seen order.
    comps: Dict[int, List[int]] = defaultdict(list)
    for idx in range(n):
        comps[dsu.find(idx)].append(idx)
    order: List[int] = []
    for idx in range(n):
        r = dsu.find(idx)
        if r not in order:
            order.append(r)
    aligned: List[ShardAtom] = []
    for r in order:
        members = comps[r]
        keeper = members[0]
        src = None
        for m in members:
            if _better_keeper(atoms[m], atoms[keeper]):
                keeper = m
            src = _merge_source_refs(src, atoms[m].source_ref)
        atoms[keeper].source_ref = src
        aligned.append(atoms[keeper])

    return AlignmentResult(
        atoms=aligned,
        input_count=input_count,
        output_count=len(aligned),
        exact_merges=exact_merges,
        entities_merged_by_llm=entities_merged,
        facts_merged_by_llm=facts_merged,
        llm_calls=llm_calls,
        review=review,
    )


# ── Orchestration ───────────────────────────────────────────────────


@dataclass
class FamiliarizeResult:
    """What ``familiarize`` produced."""
    shard: MemoryShard
    ref: ShardRef
    ref_name: str
    raw_atom_count: int
    alignment: Optional[AlignmentResult]


async def familiarize(
    sources: Union[str, Path, Dict[str, str]],
    provider: LLMProvider,
    store: ShardStore,
    ref_name: str,
    *,
    scope: str,
    origin: str = "familiarize",
    decay_class: DecayClass = DecayClass.STABLE,
    tags: Optional[List[str]] = None,
    model: Optional[str] = None,
    align: bool = True,
    lex_threshold: float = 0.6,
) -> FamiliarizeResult:
    """Read docs → LLM-extract enriched atoms → align → store one shard.

    The brief is then one call away:
    ``store.resolve_ref(ref_name).hydrate_context()``.

    ``sources`` is anything ``spiritwriter.ingest.load_documents`` accepts:
    a directory (markdown/text/PDF), a single file, or a pre-loaded
    ``{source_ref: text}`` mapping.
    """
    texts = load_documents(sources)

    raw_atoms: List[ShardAtom] = []
    for source_ref, text in texts.items():
        raw_atoms.extend(
            await extract_atoms_llm(text, provider, source_ref=source_ref, model=model)
        )

    alignment: Optional[AlignmentResult] = None
    atoms = raw_atoms
    if align:
        alignment = await align_atoms(raw_atoms, provider, lex_threshold=lex_threshold)
        atoms = alignment.atoms

    shard = MemoryShard(
        atoms=atoms,
        scope=scope,
        origin=origin,
        decay_class=decay_class,
        tags=tags or [],
    )
    ref = store.put(shard)
    store.set_ref(ref_name, ref.shard_id)

    return FamiliarizeResult(
        shard=shard,
        ref=ref,
        ref_name=ref_name,
        raw_atom_count=len(raw_atoms),
        alignment=alignment,
    )
