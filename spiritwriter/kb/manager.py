"""Knowledge base management — CRUD operations for multi-source knowledge projects.

Extracted from claude-studio-producer cli/kb.py. These are the library functions
(no CLI/Click dependencies) for resolving, loading, saving, and rebuilding
knowledge projects and graphs.
"""

import json
from pathlib import Path
from typing import Any, Optional, Dict, List

from spiritwriter.models.knowledge import (
    KnowledgeProject,
    KnowledgeGraph,
    CrossSourceLink,
)
from spiritwriter.models.document import DocumentAtom, AtomType
from spiritwriter.classify.content import is_theme_candidate


# Default KB directory (can be overridden)
DEFAULT_KB_DIR = Path("artifacts") / "kb"

# Known structural/noise terms that shouldn't be topics
STRUCTURAL_NOISE_TERMS = {
    # Block types (should never be topics)
    "figure", "figure caption", "caption", "header", "footer",
    "paragraph", "abstract", "section", "subsection", "title",
    "table", "equation", "citation", "reference", "bibliography",
    "author", "date", "keyword", "metadata", "page header", "page footer",
    # Document structure
    "volume information", "mathematical expression", "mathematical bracket",
    "author biography", "affiliations", "contact", "acknowledgments",
    # Generic academic
    "introduction", "conclusion", "related work", "background",
    "methodology", "methods", "results", "discussion", "evaluation",
    "experimental", "experiment", "future work",
}

# Stopwords that shouldn't be standalone themes
THEME_STOPWORDS = {
    "this", "that", "with", "from", "have", "been", "their", "which",
    "also", "more", "than", "into", "each", "such", "only", "other",
    "some", "these", "those", "over", "many", "most", "both", "does",
    "used", "using", "based", "however", "results", "experimental",
    "international", "introduction", "related", "conclusion", "nature",
    "conference", "proceedings", "references", "abstract", "proposed",
    "machine", "neural", "network", "networks", "learning", "training",
    "computer", "vision", "language", "intelligence", "artificial",
    "analysis", "system", "systems", "performance", "algorithm",
    "knowledge", "information", "processing", "research",
}


def resolve_project(project: str, kb_dir: Optional[Path] = None) -> Optional[Path]:
    """Resolve a project identifier to its directory.

    Tries: exact dir name, prefix match on ID, name match in project.json.

    Args:
        project: Project name, ID, or ID prefix.
        kb_dir: Override KB directory (default: artifacts/kb).

    Returns:
        Path to project directory, or None if not found.
    """
    kb_dir = kb_dir or DEFAULT_KB_DIR
    if not kb_dir.exists():
        return None

    # Exact match
    exact = kb_dir / project
    if exact.is_dir():
        return exact

    # Prefix match on directory name (also check without kb_ prefix)
    for d in kb_dir.iterdir():
        if d.is_dir() and (d.name.startswith(project) or d.name == f"kb_{project}"):
            return d

    # Name match (case-insensitive) via project.json
    for d in kb_dir.iterdir():
        if not d.is_dir():
            continue
        meta_path = d / "project.json"
        if meta_path.exists():
            try:
                with open(meta_path, encoding="utf-8") as f:
                    meta = json.load(f)
                if meta.get("name", "").lower() == project.lower():
                    return d
            except (json.JSONDecodeError, OSError):
                continue

    return None


def load_project(project_dir: Path) -> KnowledgeProject:
    """Load a KnowledgeProject from its directory.

    Args:
        project_dir: Path to the project directory containing project.json.

    Returns:
        KnowledgeProject instance.
    """
    meta_path = project_dir / "project.json"
    with open(meta_path, encoding="utf-8") as f:
        data = json.load(f)
    return KnowledgeProject.from_dict(data)


def save_project(project_dir: Path, project: KnowledgeProject) -> None:
    """Save a KnowledgeProject to its directory.

    Args:
        project_dir: Path to the project directory.
        project: KnowledgeProject instance to save.
    """
    meta_path = project_dir / "project.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(project.to_dict(), f, indent=2, ensure_ascii=False)


def rebuild_knowledge_graph(project_dir: Path, project: KnowledgeProject) -> KnowledgeGraph:
    """Rebuild the unified KnowledgeGraph from all source DocumentGraphs.

    Merges atoms, builds topic/entity indices, detects cross-source links
    by shared entity co-occurrence, and extracts key themes.

    Args:
        project_dir: Path to the project directory.
        project: KnowledgeProject with sources to merge.

    Returns:
        The rebuilt KnowledgeGraph (also saved to knowledge_graph.json).
    """
    all_atoms: Dict[str, DocumentAtom] = {}
    atom_sources: Dict[str, str] = {}
    topic_index: Dict[str, List[str]] = {}
    entity_index: Dict[str, List[str]] = {}

    sources_dir = project_dir / "sources"

    for source_id, source in project.sources.items():
        graph_path = sources_dir / source_id / "document_graph.json"
        if not graph_path.exists():
            continue

        with open(graph_path, encoding="utf-8") as f:
            graph_data = json.load(f)

        for atom_id, atom_data in graph_data.get("atoms", {}).items():
            atom = DocumentAtom(
                atom_id=atom_data["atom_id"],
                atom_type=AtomType(atom_data["atom_type"]),
                content=atom_data.get("content", ""),
                source_page=atom_data.get("source_page"),
                source_location=atom_data.get("source_location"),
                topics=atom_data.get("topics", []),
                entities=atom_data.get("entities", []),
                relationships=atom_data.get("relationships", []),
                importance_score=atom_data.get("importance_score", 0.5),
                caption=atom_data.get("caption"),
                figure_number=atom_data.get("figure_number"),
                data_summary=atom_data.get("data_summary"),
            )
            all_atoms[atom_id] = atom
            atom_sources[atom_id] = source_id

            for topic in atom.topics:
                if topic not in topic_index:
                    topic_index[topic] = []
                topic_index[topic].append(atom_id)

            for entity in atom.entities:
                if entity not in entity_index:
                    entity_index[entity] = []
                entity_index[entity].append(atom_id)

    # Detect cross-source links via shared entities
    cross_links: List[CrossSourceLink] = []
    link_counter = 0

    for entity, atom_ids in entity_index.items():
        sources_with_entity: Dict[str, List[str]] = {}
        for aid in atom_ids:
            sid = atom_sources.get(aid)
            if sid:
                if sid not in sources_with_entity:
                    sources_with_entity[sid] = []
                sources_with_entity[sid].append(aid)

        source_ids = list(sources_with_entity.keys())
        if len(source_ids) >= 2:
            for i in range(len(source_ids) - 1):
                for j in range(i + 1, len(source_ids)):
                    src_a = source_ids[i]
                    src_b = source_ids[j]
                    atom_a = sources_with_entity[src_a][0]
                    atom_b = sources_with_entity[src_b][0]

                    link_counter += 1
                    cross_links.append(CrossSourceLink(
                        link_id=f"link_{link_counter:04d}",
                        source_atom_id=atom_a,
                        target_atom_id=atom_b,
                        source_source_id=src_a,
                        target_source_id=src_b,
                        relationship="same_topic",
                        confidence=0.6,
                        created_by="auto",
                    ))

    # Identify key themes
    source_count = len(project.sources)
    key_themes = []
    if source_count > 0:
        for topic, atom_ids in sorted(topic_index.items(), key=lambda x: -len(x[1])):
            if topic.lower() in THEME_STOPWORDS:
                continue
            if ' ' not in topic and len(topic) < 6:
                continue
            if not is_theme_candidate(topic):
                continue
            topic_sources = set(atom_sources.get(aid) for aid in atom_ids)
            topic_sources.discard(None)
            if len(topic_sources) >= min(2, source_count):
                key_themes.append(topic)
            if len(key_themes) >= 10:
                break

    graph = KnowledgeGraph(
        project_id=project.project_id,
        atoms=all_atoms,
        atom_sources=atom_sources,
        cross_links=cross_links,
        topic_index=topic_index,
        entity_index=entity_index,
        key_themes=key_themes,
    )

    # Save
    graph_path = project_dir / "knowledge_graph.json"
    with open(graph_path, "w", encoding="utf-8") as f:
        json.dump(graph.to_dict(), f, indent=2, ensure_ascii=False)

    project.has_knowledge_graph = True
    return graph


def build_concept_from_kb(
    proj: KnowledgeProject,
    kg: KnowledgeGraph,
    prompt: str,
    source_filter: Optional[List[str]] = None,
) -> str:
    """Build a rich concept string from KB content for downstream pipelines.

    Assembles structured context from the knowledge graph — abstracts, quotes,
    figures, entities, cross-links — within a character budget.

    Args:
        proj: The KnowledgeProject.
        kg: The unified KnowledgeGraph.
        prompt: Production direction or focus prompt.
        source_filter: Optional list of source IDs to limit to.

    Returns:
        Formatted concept string ready for LLM consumption.
    """
    sections = []

    # Header
    sections.append(f"KNOWLEDGE BASE: {proj.name}")
    if proj.description:
        sections.append(f"DESCRIPTION: {proj.description}")

    # Sources summary
    source_lines = []
    for sid, src in proj.sources.items():
        if source_filter and sid not in source_filter:
            continue
        line = f"- {src.title} ({src.source_type.value}, {src.atom_count} atoms)"
        if src.one_sentence:
            line += f"\n  Summary: {src.one_sentence}"
        source_lines.append(line)
    if source_lines:
        sections.append("SOURCES:\n" + "\n".join(source_lines))

    sections.append(f"PRODUCTION DIRECTION: {prompt}")

    if kg.key_themes:
        themes_clean = [t.replace("\n", " ").replace("\r", "") for t in kg.key_themes]
        sections.append("KEY THEMES: " + ", ".join(themes_clean))

    # Collect atoms, filtering by source if needed
    atoms_to_use = []
    for atom_id, atom in kg.atoms.items():
        if source_filter:
            atom_source = kg.atom_sources.get(atom_id, "")
            if atom_source not in source_filter:
                continue
        atoms_to_use.append(atom)

    atoms_to_use.sort(key=lambda a: a.importance_score, reverse=True)

    char_budget = 4000
    chars_used = 0

    # 1. Abstracts
    abstracts = [a for a in atoms_to_use if a.atom_type == AtomType.ABSTRACT]
    if abstracts:
        abstract_lines = []
        for a in abstracts[:3]:
            if chars_used + len(a.content) > char_budget:
                break
            abstract_lines.append(a.content)
            chars_used += len(a.content)
        if abstract_lines:
            sections.append("ABSTRACTS:\n" + "\n\n".join(abstract_lines))

    # 2. Key quotes
    quotes = [a for a in atoms_to_use if a.atom_type == AtomType.QUOTE]
    if quotes:
        quote_lines = []
        for a in quotes[:5]:
            if chars_used + len(a.content) > char_budget:
                break
            quote_lines.append(f'"{a.content}"')
            chars_used += len(a.content)
        if quote_lines:
            sections.append("KEY QUOTES:\n" + "\n".join(quote_lines))

    # 3. Section headers
    headers = [a for a in atoms_to_use if a.atom_type == AtomType.SECTION_HEADER]
    if headers:
        header_texts = [a.content for a in headers[:15]]
        header_block = ", ".join(header_texts)
        if chars_used + len(header_block) <= char_budget:
            sections.append("DOCUMENT STRUCTURE: " + header_block)
            chars_used += len(header_block)

    # 4. High-importance paragraphs
    paragraphs = [a for a in atoms_to_use if a.atom_type == AtomType.PARAGRAPH]
    if paragraphs:
        para_lines = []
        for a in paragraphs[:10]:
            if chars_used >= char_budget:
                break
            text = a.content[:300]
            if len(a.content) > 300:
                text += "..."
            para_lines.append(text)
            chars_used += len(text)
        if para_lines:
            sections.append("CONTENT TO COVER:\n" + "\n\n".join(para_lines))

    # 5. Figure descriptions
    figures = [a for a in atoms_to_use if a.atom_type in (AtomType.FIGURE, AtomType.CHART, AtomType.DIAGRAM)]
    if figures:
        fig_lines = []
        for a in figures[:8]:
            if chars_used >= char_budget:
                break
            desc = a.caption or a.content or f"Figure {a.figure_number or '?'}"
            fig_lines.append(f"- {desc[:150]}")
            chars_used += len(desc[:150])
        if fig_lines:
            sections.append("FIGURES AVAILABLE:\n" + "\n".join(fig_lines))

    # 6. Entity list
    all_entities = set()
    for a in atoms_to_use:
        all_entities.update(a.entities[:3])
    if all_entities:
        entity_str = ", ".join(sorted(all_entities)[:20])
        sections.append(f"KEY ENTITIES: {entity_str}")

    # 7. Cross-source connections
    if kg.cross_links:
        link_descs = []
        for link in kg.cross_links[:5]:
            src_atom = kg.atoms.get(link.source_atom_id)
            tgt_atom = kg.atoms.get(link.target_atom_id)
            if src_atom and tgt_atom:
                link_descs.append(
                    f"- {link.relationship}: "
                    f"{src_atom.content[:60]} <-> {tgt_atom.content[:60]}"
                )
        if link_descs:
            sections.append("CROSS-SOURCE CONNECTIONS:\n" + "\n".join(link_descs))

    return "\n\n".join(sections)


def calculate_topic_quality(
    topics: Dict[str, List[str]],
    atom_sources: Dict[str, str],
) -> Dict[str, Any]:
    """Analyze topic quality and detect noise.

    Args:
        topics: Topic index mapping topic → list of atom IDs.
        atom_sources: Mapping of atom ID → source ID.

    Returns:
        Dict with score, noise_count, good_count, noise_topics, good_topics.
    """
    total_topics = len(topics)
    if total_topics == 0:
        return {"score": 0, "noise_count": 0, "noise_topics": [], "good_topics": []}

    noise_topics = []
    good_topics = []

    for topic, atom_ids in topics.items():
        topic_lower = topic.lower().strip()

        is_noise = False
        noise_reason = None

        if topic_lower in STRUCTURAL_NOISE_TERMS:
            is_noise = True
            noise_reason = "structural term"
        elif not is_theme_candidate(topic):
            is_noise = True
            noise_reason = "institutional/venue name"
        elif len(topic_lower) < 3:
            is_noise = True
            noise_reason = "too short"
        elif topic_lower.isdigit():
            is_noise = True
            noise_reason = "numeric only"
        elif ' ' not in topic_lower and len(topic_lower) < 6:
            is_noise = True
            noise_reason = "short single word"

        if is_noise:
            noise_topics.append({
                "topic": topic,
                "count": len(atom_ids),
                "reason": noise_reason,
            })
        else:
            sources = set(atom_sources.get(aid, "") for aid in atom_ids)
            sources.discard("")
            good_topics.append({
                "topic": topic,
                "count": len(atom_ids),
                "sources": len(sources),
            })

    noise_topics.sort(key=lambda x: x["count"], reverse=True)
    good_topics.sort(key=lambda x: x["count"], reverse=True)

    noise_count = len(noise_topics)
    good_count = len(good_topics)
    quality_score = int((good_count / total_topics) * 100) if total_topics > 0 else 0

    return {
        "score": quality_score,
        "total_topics": total_topics,
        "noise_count": noise_count,
        "good_count": good_count,
        "noise_topics": noise_topics[:20],
        "good_topics": good_topics[:20],
    }


def calculate_entity_quality(entities: Dict[str, List[str]]) -> Dict[str, Any]:
    """Analyze entity extraction quality.

    Args:
        entities: Entity index mapping entity → list of atom IDs.

    Returns:
        Dict with score, total_entities, acronyms, proper_names, other.
    """
    total = len(entities)
    if total == 0:
        return {"score": 0, "acronyms": [], "proper_names": [], "other": []}

    acronyms = []
    proper_names = []
    other = []

    for entity, atom_ids in entities.items():
        count = len(atom_ids)
        if entity.isupper() and 2 <= len(entity) <= 6:
            acronyms.append({"entity": entity, "count": count})
        elif ' ' in entity and entity[0].isupper():
            proper_names.append({"entity": entity, "count": count})
        else:
            other.append({"entity": entity, "count": count})

    acronyms.sort(key=lambda x: x["count"], reverse=True)
    proper_names.sort(key=lambda x: x["count"], reverse=True)
    other.sort(key=lambda x: x["count"], reverse=True)

    good_count = len(acronyms) + len(proper_names)
    quality_score = int((good_count / total) * 100) if total > 0 else 0

    return {
        "score": quality_score,
        "total_entities": total,
        "acronyms": acronyms[:10],
        "proper_names": proper_names[:10],
        "other": other[:10],
    }


def get_atom_type_distribution(atoms: Dict[str, DocumentAtom]) -> Dict[str, int]:
    """Count atoms by type.

    Args:
        atoms: Dict of atom_id → DocumentAtom.

    Returns:
        Dict of atom_type → count, sorted by count descending.
    """
    dist: Dict[str, int] = {}
    for atom in atoms.values():
        atom_type = atom.atom_type.value if hasattr(atom.atom_type, 'value') else str(atom.atom_type)
        dist[atom_type] = dist.get(atom_type, 0) + 1
    return dict(sorted(dist.items(), key=lambda x: -x[1]))


class KnowledgeBaseManager:
    """High-level manager for knowledge base operations.

    Wraps the module-level functions with a configurable KB directory.
    """

    def __init__(self, kb_dir: Optional[Path] = None):
        self.kb_dir = kb_dir or DEFAULT_KB_DIR

    def resolve(self, project: str) -> Optional[Path]:
        """Resolve a project identifier to its directory."""
        return resolve_project(project, self.kb_dir)

    def load(self, project_dir: Path) -> KnowledgeProject:
        """Load a project from its directory."""
        return load_project(project_dir)

    def save(self, project_dir: Path, project: KnowledgeProject) -> None:
        """Save a project to its directory."""
        save_project(project_dir, project)

    def rebuild_graph(self, project_dir: Path, project: KnowledgeProject) -> KnowledgeGraph:
        """Rebuild the unified knowledge graph."""
        return rebuild_knowledge_graph(project_dir, project)

    def build_concept(
        self,
        proj: KnowledgeProject,
        kg: KnowledgeGraph,
        prompt: str,
        source_filter: Optional[List[str]] = None,
    ) -> str:
        """Build a concept string from KB content."""
        return build_concept_from_kb(proj, kg, prompt, source_filter)
