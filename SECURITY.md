# medterm4ds Security Model

## API and MCP server exposure (Tier B hardening, 2026-06-26)

The `medterm4ds.apps.api` (FastAPI) and `medterm4ds.apps.mcp` (FastMCP) servers
are designed as **local-only multi-process sidecars**:

- **Local-only**: bound to `127.0.0.1` by default via `medterm4ds.apps.api.main()`.
  Local processes (notebooks, scripts, other MCP clients on the same host) can
  connect via `http://127.0.0.1:PORT`. External networks cannot reach the
  server because the OS does not route remote traffic to localhost-bound ports.
- **Multi-process**: any process on the same machine can connect; there is no
  per-user isolation. This is intentional -- it's the typical sidecar pattern
  for one long-running engine serving many short-lived local clients.
- **No auth by default**: there's no API key or session mechanism. A local
  attacker with the ability to bind to 127.0.0.1 already has equivalent access
  to the host.

## What's enforced

- **Request size cap**: every batch endpoint limits `codes` to
  `MAX_CODES_PER_REQUEST = 10_000`. A single misbehaving local client cannot
  lock the read-only DuckDB connection with a 100k-code POST.
- **Query length cap**: `search_names` rejects queries >256 characters at both
  the service layer (`services.discovery`) and the API model.
- **`/health` is sanitized**: returns readiness, sources, memory profile, and
  cache status, but NOT the DB filesystem path. Local clients needing the path
  should read `MEDTERM4DS_DB` from their env, not probe `/health`.
- **HTTP body-size caps**: all external HTTP responses (openFDA, PubMed, UTS,
  CDC) are streamed through a 50 MB cap (`MAX_RESPONSE_BYTES`) so a
  compromised external endpoint cannot OOM the process.
- **Zip-slip guard**: `services.data_setup.download_release(extract=True)`
  validates each archive member stays inside `extract_dir` before extraction.
- **Filename validation**: download filenames from UTS release metadata must
  match `^[A-Za-z0-9._-]+$`; otherwise refused.

## What's NOT enforced

- **No TLS**: localhost traffic is unencrypted. Acceptable for local-only
  deployment; if you tunnel remotely via SSH, the tunnel handles encryption.
- **No per-user rate limiting**: any local process can send 1k req/s. The
  request-size cap prevents individual requests from being catastrophic; the
  engine itself is single-threaded against one DB so throughput is naturally
  bounded.
- **No audit log**: requests are not logged. Add middleware if you need this.
- **No auth on MCP tools**: same local-only assumption as the API.

## When to override

Override the localhost binding only if you've configured an authenticating
reverse proxy in front (nginx with auth_request, OAuth2 proxy, mTLS terminator,
etc.):

```bash
MEDTERM4DS_API_HOST=0.0.0.0 python -m medterm4ds.apps.api
```

The server logs a startup warning if bound to anything other than
`127.0.0.1`/`::1`/`localhost`.

## HF Spaces Docker deployment — auth divergence

`deploy/hf-spaces/fhir-server/app.py` deliberately sets
`MEDTERM4DS_API_HOST=0.0.0.0` (HF Spaces requires binding to 7860 to be
reachable from the platform's reverse proxy). This **diverges from the
local-only contract** above — anyone with the Space URL can call `$lookup`,
`$validate-code`, `$translate`, `$subsumes`, `$expand`, `$closure`, `$search`,
and `$extract` without authentication.

Mitigations:

- **Use a private Space** if available — HF Spaces private spaces require
  authentication via the launching user's HF account.
- **Front the deployment with an authenticating proxy** (Cloudflare Access,
  OAuth2 Proxy, mTLS terminator) for any non-personal deployment.
- **The container is intentionally read-only on the UMLS data**: even if a
  caller reaches `$extract` or `$lookup`, they cannot corrupt or exfiltrate
  the underlying `lookup.duckdb` (the engine opens the file read-only).
- **No rate limiting** is applied at the FHIR layer; HF Spaces' own platform
  limits apply. For local-only sidecars, the request size caps in Tier B
  hardening bound individual requests.

If you need to add auth to the Docker image itself, wrap the FastAPI app in
an auth middleware before invoking `create_fhir_app()`.

## Reporting security issues

If you find a vulnerability, please open a private security advisory on the
GitHub repo rather than a public issue.
