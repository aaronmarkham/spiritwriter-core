"""Extraction rules by document type."""

from spiritwriter.models.document import DocumentType, ZoneRole

EXTRACTION_RULES = {
    DocumentType.SCIENTIFIC_PAPER: {
        "topic_zones": [ZoneRole.BODY],
        "entity_zones": [ZoneRole.BODY, ZoneRole.FRONT_MATTER],
        "metadata_zones": [ZoneRole.BIOGRAPHICAL, ZoneRole.BOILERPLATE, ZoneRole.BACK_MATTER],
    },
    DocumentType.NEWS_ARTICLE: {
        "topic_zones": [ZoneRole.BODY],
        "entity_zones": [ZoneRole.BODY, ZoneRole.FRONT_MATTER],
        "metadata_zones": [ZoneRole.BIOGRAPHICAL, ZoneRole.BOILERPLATE],
    },
    DocumentType.BLOG_POST: {
        "topic_zones": [ZoneRole.BODY],
        "entity_zones": [ZoneRole.BODY],
        "metadata_zones": [ZoneRole.BIOGRAPHICAL, ZoneRole.BOILERPLATE],
    },
    DocumentType.DATASET_README: {
        "topic_zones": [ZoneRole.BODY],
        "entity_zones": [ZoneRole.BODY],
        "metadata_zones": [ZoneRole.BIOGRAPHICAL, ZoneRole.BOILERPLATE, ZoneRole.BACK_MATTER],
    },
    DocumentType.GENERIC: {
        "topic_zones": [ZoneRole.BODY, ZoneRole.FRONT_MATTER],
        "entity_zones": [ZoneRole.BODY, ZoneRole.FRONT_MATTER],
        "metadata_zones": [ZoneRole.BOILERPLATE],
    },
}
