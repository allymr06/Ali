from __future__ import annotations


class ResearchError(RuntimeError):
    """Base error for bounded web research operations."""


class UnsafeURLError(ResearchError):
    """Raised when a URL could reach a disallowed network target."""


class FetchError(ResearchError):
    """Raised when a remote response cannot be safely collected."""


class ContentRejectedError(FetchError):
    """Raised when response metadata or content violates policy."""


class SearchError(ResearchError):
    """Raised when a configured search provider fails."""


class CitationIntegrityError(ResearchError):
    """Raised when synthesized claims contain invalid citations."""
