"""HistWarDB: a dynamic, Wikidata-based historical war dataset toolkit."""

from .core import FetchError, HistWarDB
from .query import build_entity_query, build_sparql_query

# Backward-compatible alias used in earlier notebooks/scripts.
FastWikidataWarDataset = HistWarDB

__all__ = ["FastWikidataWarDataset", "FetchError", "HistWarDB", "build_entity_query", "build_sparql_query"]

__version__ = "0.3.0"
