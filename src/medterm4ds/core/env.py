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


def resolve_device(explicit: str | None = None) -> str:
    """Resolve the torch device for model inference (GLiNER, SapBERT).

    Priority: explicit argument > ``MEDTERM4DS_DEVICE`` env var > ``"auto"``.
    ``"auto"`` picks cuda when available, then mps, then cpu. An explicit
    cuda/mps request that is unavailable raises instead of silently falling
    back — an operator who asked for a GPU should hear about it, not get
    quiet CPU performance.

    torch is imported lazily so this module stays importable without it
    (core env parsing must not require the heavy ML stack).
    """
    value = explicit if explicit is not None else env_str("MEDTERM4DS_DEVICE", "auto")
    value = value.strip().lower()
    if not value:
        value = "auto"

    import torch

    if value == "auto":
        if torch.cuda.is_available():
            return "cuda"
        try:
            if torch.backends.mps.is_available():
                return "mps"
        except AttributeError:
            pass
        return "cpu"

    try:
        dev = torch.device(value)
    except (RuntimeError, ValueError):
        raise ValueError(
            "MEDTERM4DS_DEVICE must be one of auto, cpu, cuda, cuda:<n>, or mps "
            f"(got {value!r})"
        ) from None
    if dev.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError(
            "MEDTERM4DS_DEVICE requests CUDA but torch.cuda.is_available() is "
            "False — install a CUDA-enabled torch build or set "
            "MEDTERM4DS_DEVICE=cpu"
        )
    if dev.type == "mps":
        try:
            mps_ok = torch.backends.mps.is_available()
        except AttributeError:
            mps_ok = False
        if not mps_ok:
            raise RuntimeError(
                "MEDTERM4DS_DEVICE requests MPS but the backend is unavailable "
                "on this platform — set MEDTERM4DS_DEVICE=cpu"
            )
    return value


__all__ = ["env_bool", "env_int", "env_str", "resolve_device"]
