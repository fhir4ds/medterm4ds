"""Shared ``MEDTERM4DS_*`` environment-variable parsing helpers.

QC-467 (LOW): ``_env_int``/``_env_bool`` were triplicated across
``apps/api.py``, ``apps/mcp.py``, and ``apps/fhir_api.py`` with drifting
error behavior — MCP raised a clean named ``ValueError`` for
``MEDTERM4DS_THREADS=abc`` (QC-419) while api/fhir crashed with a raw
``ValueError: invalid literal for int()``, and the fhir copy's comment
claimed parity it did not have. Per GLOBAL_RULES single-source-of-truth,
the hardened helpers live here and every app imports them.

QC-473 (LOW): ``minimum=`` lets call sites reject negative values at parse
time with the variable named (``MEDTERM4DS_THREADS=-1`` previously crashed
all three servers in lifespan with an anonymous
``duckdb.SyntaxException: Must have at least 1 thread!``).
"""

from __future__ import annotations

import os


def env_int(name: str, *, minimum: int | None = None) -> int | None:
    """Parse ``name`` as int. Unset/empty -> None; invalid -> named ValueError.

    ``minimum`` (when given) additionally rejects values below it, e.g.
    ``env_int("MEDTERM4DS_THREADS", minimum=1)``.
    """
    value = os.getenv(name)
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        # QC-419 (LOW): a bare int() error gave the operator no pointer to
        # the misconfigured variable. Name the env var and expected type.
        raise ValueError(
            f"environment variable {name} must be an integer, got {value!r}"
        ) from None
    if minimum is not None and parsed < minimum:
        raise ValueError(
            f"environment variable {name} must be >= {minimum}, got {parsed!r}"
        )
    return parsed


def env_bool(name: str, default: bool) -> bool:
    """Parse ``name`` as a boolean. Unset/blank -> default; else 1/true/yes/on.

    CR-042 (review-5 finding 4): a blank value previously evaluated as an
    explicit ``False``, so ``MEDTERM4DS_PREPARE_CACHE=`` (exported empty)
    silently disabled cache preparation across all three servers. Blank is
    now unset, matching the ``env_str``/``env_int`` contract documented
    above (QC-465 sibling).
    """
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_str(name: str, default: str | None = None) -> str | None:
    """Read ``name`` as a string; unset/blank -> ``default``.

    Blank values are treated as unset so an exported-but-empty variable
    does not silently override a documented default (QC-465 sibling).
    """
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value


__all__ = ["env_bool", "env_int", "env_str"]
