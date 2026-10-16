"""HistWarDB: a dynamic, Wikidata-based historical war dataset toolkit."""

from importlib import metadata as _metadata

from .core import FetchError, HistWarDB
from .query import build_entity_query, build_sparql_query

# Backward-compatible alias used in earlier notebooks/scripts.
FastWikidataWarDataset = HistWarDB

__all__ = ["FastWikidataWarDataset", "FetchError", "HistWarDB", "build_entity_query", "build_sparql_query"]

try:
    # Single source of truth: pyproject.toml's [project] version, read from
    # the installed package's metadata. A hardcoded string here drifts from
    # pyproject.toml the moment one of the two is bumped and the other isn't.
    __version__ = _metadata.version("histwardb")
except _metadata.PackageNotFoundError:
    # Running from a source checkout that was never `pip install`-ed.
    __version__ = "0.0.0+unknown"
