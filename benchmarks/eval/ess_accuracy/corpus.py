"""Corpus loader for the ESS accuracy harness.

A corpus directory contains:

    schema.json         CanonicalSchema as JSON
    entities.json       List of canonical entity records (dict)
    mutations.py        (optional) Domain-specific MutationFamily list

    Optional README.md  Where the entities came from, known biases.

Universal mutation families always apply. Domain-specific families
extend the universal set when present.
"""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path

from spiritwriter.fabric.canonicalize import CanonicalSchema

from benchmarks.eval.ess_accuracy.mutations import (
    UNIVERSAL_FAMILIES,
    MutationFamily,
)


@dataclass
class Corpus:
    """A loaded corpus: schema + canonical entities + mutation families."""
    name: str                              # short name (e.g. "people")
    path: Path                             # directory the corpus lives in
    schema: CanonicalSchema
    entities: list[dict]                   # canonical entity records
    families: list[MutationFamily]         # universal + domain-specific
    jaccard_fields: list[str] | None = None  # baseline tokenization fields;
                                             # None = all ess_fields. Set to
                                             # exclude strong anchors (DOB,
                                             # SSN) for honest comparison.


_SHIPPED_DATA_DIR = Path(__file__).resolve().parent / "data"


def load_corpus(corpus_arg: str, *, allow_untrusted_mutations: bool = False) -> Corpus:
    """Load a corpus by short name (``people``, ``publications``) or by path.

    Short names resolve against ``benchmarks/eval/ess_accuracy/data/<name>``.
    Anything containing a path separator (or starting with ``.`` / ``~``) is
    treated as an explicit filesystem path.

    **Security note.** A corpus directory may contain a ``mutations.py``
    file that is imported and executed. For shipped corpora (under
    ``benchmarks/eval/ess_accuracy/data/``) we always load it. For corpora
    outside that path we refuse by default — pass ``allow_untrusted_mutations
    =True`` (or the runner's ``--allow-untrusted-mutations`` flag) to opt
    in. Universal mutation families always apply; only the per-corpus
    ``mutations.py`` is gated.
    """
    path = _resolve_corpus_path(corpus_arg)
    if not path.is_dir():
        raise FileNotFoundError(
            f"Corpus directory not found: {path}\n"
            f"Pass a short name (people, publications) or an absolute path."
        )

    schema, jaccard_fields = _load_schema(path / "schema.json")
    entities = _load_entities(path / "entities.json")

    is_trusted = _is_under(path, _SHIPPED_DATA_DIR)
    domain_families = _load_domain_families(
        path,
        trusted=is_trusted or allow_untrusted_mutations,
    )
    families = list(UNIVERSAL_FAMILIES) + domain_families

    return Corpus(
        name=path.name,
        path=path,
        schema=schema,
        entities=entities,
        families=families,
        jaccard_fields=jaccard_fields,
    )


def _is_under(path: Path, ancestor: Path) -> bool:
    """True if path is at or below ancestor (resolved)."""
    path = path.resolve()
    ancestor = ancestor.resolve()
    try:
        path.relative_to(ancestor)
        return True
    except ValueError:
        return False


def _resolve_corpus_path(corpus_arg: str) -> Path:
    arg_path = Path(corpus_arg)
    if arg_path.is_absolute() or "/" in corpus_arg or "\\" in corpus_arg \
            or corpus_arg.startswith(("~", ".")):
        return arg_path.expanduser().resolve()
    # Short name: resolve under data/
    return Path(__file__).parent / "data" / corpus_arg


def _load_schema(schema_path: Path) -> tuple[CanonicalSchema, list[str] | None]:
    """Construct a CanonicalSchema from its JSON form.

    Tolerates either ``fuzzy_fields`` as a dict (real form) or a list of
    ``[field, threshold]`` pairs (sometimes easier to author by hand).

    Returns ``(schema, jaccard_fields)`` — ``jaccard_fields`` is the
    optional eval-only field list used to restrict the baseline
    tokenization, or None to use all ess_fields.
    """
    raw = json.loads(schema_path.read_text(encoding="utf-8"))
    fuzzy = raw.get("fuzzy_fields", {})
    if isinstance(fuzzy, list):
        fuzzy = {item[0]: item[1] for item in fuzzy}

    schema = CanonicalSchema(
        name=raw["name"],
        ess_fields=list(raw["ess_fields"]),
        fuzzy_fields=fuzzy,
        context_fields=list(raw.get("context_fields", [])),
        metadata_fields=list(raw.get("metadata_fields", [])),
        age_bucket_size=int(raw.get("age_bucket_size", 2)),
        temporal_window_days=int(raw.get("temporal_window_days", 7)),
    )
    jaccard_fields = raw.get("jaccard_fields")
    if jaccard_fields is not None:
        jaccard_fields = list(jaccard_fields)
    return schema, jaccard_fields


def _load_entities(entities_path: Path) -> list[dict]:
    data = json.loads(entities_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{entities_path} must contain a JSON array of records.")
    return data


def _load_domain_families(
    corpus_dir: Path, *, trusted: bool
) -> list[MutationFamily]:
    """Import ``mutations.py`` from a corpus directory if present.

    The module is expected to expose a module-level ``FAMILIES`` list of
    ``MutationFamily`` objects. Imports execute arbitrary Python from the
    corpus directory — for corpora outside the shipped ``data/`` tree,
    ``trusted`` must be True (the caller has opted in).
    """
    mut_path = corpus_dir / "mutations.py"
    if not mut_path.is_file():
        return []

    if not trusted:
        raise PermissionError(
            f"Refusing to load {mut_path} from an untrusted location. "
            f"`mutations.py` executes arbitrary Python on import. To opt "
            f"in, pass ``allow_untrusted_mutations=True`` to load_corpus() "
            f"or ``--allow-untrusted-mutations`` to the runner. "
            f"Universal mutation families are always applied; only the "
            f"per-corpus extensions are gated."
        )

    spec = importlib.util.spec_from_file_location(
        f"benchmarks.eval.ess_accuracy.data.{corpus_dir.name}.mutations",
        mut_path,
    )
    if spec is None or spec.loader is None:
        return []
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    families = getattr(module, "FAMILIES", None)
    if not isinstance(families, list):
        raise ValueError(
            f"{mut_path} must define a module-level FAMILIES list of "
            f"MutationFamily objects."
        )
    return families
