# Spiritwriter Core

Shared foundation for AI content pipelines — knowledge base, document ingestion, secrets management, and LLM abstraction.

Extracted from [claude-studio-producer](https://github.com/aaronmarkham/claude-studio-producer).

## Install

```bash
pip install -e .          # local dev
pip install -e ".[dev]"   # with test deps
```

## Usage

```python
from spiritwriter.kb import KnowledgeProject, KnowledgeGraph, KnowledgeSource
from spiritwriter.secrets import get_api_key
from spiritwriter.ingest import DocumentIngestor
from spiritwriter.models.document import DocumentAtom, AtomType
from spiritwriter.llm import LLMProvider
from spiritwriter.classify import is_theme_candidate
```

## Modules

| Module | Description |
|--------|-------------|
| `spiritwriter.models` | Data models — DocumentAtom, KnowledgeProject, KnowledgeGraph |
| `spiritwriter.kb` | Knowledge base CRUD — create, load, save, rebuild graphs |
| `spiritwriter.secrets` | OS keychain API key management |
| `spiritwriter.ingest` | PDF document ingestion (PyMuPDF + LLM classification) |
| `spiritwriter.llm` | Abstract LLM provider + Anthropic implementation |
| `spiritwriter.classify` | Content/theme quality classification |
| `spiritwriter.trace` | *(placeholder)* Provenance/audit trail — strands-trace integration |

## Architecture

```
spiritwriter/
├── models/      # Zero deps — extract first
│   ├── document.py
│   └── knowledge.py
├── secrets/     # Zero internal deps
├── classify/    # Depends on: models
├── llm/         # Depends on: secrets
│   ├── base.py      # Abstract LLM interface
│   └── anthropic.py # Anthropic/Claude implementation
├── ingest/      # Depends on: llm, models, classify
├── kb/          # Depends on: models, classify
└── trace/       # Placeholder
```

## License

Apache 2.0
