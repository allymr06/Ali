from __future__ import annotations

from app.research.errors import CitationIntegrityError
from app.research.models import ResearchReport


def validate_citations(report: ResearchReport) -> None:
    source_ids = [source.source_id for source in report.sources]
    if len(source_ids) != len(set(source_ids)):
        raise CitationIntegrityError("Research source identifiers must be unique.")
    known = set(source_ids)
    for claim in report.claims:
        if not claim.source_ids:
            raise CitationIntegrityError("Every research claim requires a citation.")
        if len(claim.source_ids) != len(set(claim.source_ids)):
            raise CitationIntegrityError("A claim contains duplicate citations.")
        missing = set(claim.source_ids) - known
        if missing:
            raise CitationIntegrityError("A claim cites an unknown source.")
