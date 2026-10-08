"""HistWarDB: a dynamic, Wikidata-based historical war dataset toolkit."""

from .core import HistWarDB
from .query import build_sparql_query

# Backward-compatible alias used in earlier notebooks/scripts.
FastWikidataWarDataset = HistWarDB

__all__ = ["FastWikidataWarDataset", "HistWarDB", "build_sparql_query"]

__version__ = "0.1.0"
