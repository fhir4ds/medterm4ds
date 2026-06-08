"""Local DuckDB terminology engine."""

from medterm4ds.core.config import LocalDuckDBConfig, LocalLiteConfig

from .engine import LocalDuckDBEngine, LocalLiteEngine

__all__ = ["LocalDuckDBConfig", "LocalDuckDBEngine", "LocalLiteConfig", "LocalLiteEngine"]
