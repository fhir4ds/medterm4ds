"""HTTP response helpers.

Single source of truth for the 50 MiB streaming read cap used across the
codebase. Previously duplicated (with drift risk) in:

  - engines/api/engine.py (RemoteApiEngine client)
  - domains/evidence.py (external evidence fetcher)
  - services/data_setup.py (UTS/CDC download during DB build)

If a CVE-style fix is ever needed (e.g. decompression-bomb guard, tighter
cap, partial-read cleanup), it lands here and all three call sites get it.
"""

from __future__ import annotations

# Hard cap on any single HTTP response body. 50 MiB is generous for
# terminology-server / UTS payloads but bounded enough to fail fast on
# a misbehaving endpoint that streams indefinitely.
MAX_RESPONSE_BYTES = 50 * 1024 * 1024


def read_capped(response, *, source_label: str = "HTTP response") -> bytes:
    """Read at most MAX_RESPONSE_BYTES from ``response`` using streaming reads.

    Args:
        response: An HTTPResponse-like object with a ``read(n)`` method.
        source_label: Short description of the response source for the
            error message if the cap is exceeded (e.g. "Remote API response",
            "External evidence response", "UTS download").

    Raises:
        RuntimeError: if the response exceeds MAX_RESPONSE_BYTES.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise RuntimeError(
                f"{source_label} exceeded {MAX_RESPONSE_BYTES} byte cap; aborting"
            )
        chunks.append(chunk)
    return b"".join(chunks)
