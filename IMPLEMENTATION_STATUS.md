# Implementation Status

This document distinguishes committed code from planned capabilities.

## Implemented

- Read-only dashboard at `/` with search, summary counts, project links, reported status, and periodic refresh.

- FastAPI app with health, agent-list, GitHub discovery, endpoint, and observation routes.
- SQLAlchemy models for discovered repositories, observations, and explicitly registered endpoints.
- GitHub public repository search connector with optional server-side token.
- Periodic GitHub discovery job.
- Bearer-token authentication for administrative operations; administrative routes fail closed when ADMIN_API_TOKEN is unset.
- HTTPS endpoint registration restricted to exact MONITOR_ALLOWED_HOSTS entries.
- DNS resolution checks reject non-public addresses; monitoring requests do not follow redirects, use short timeouts, and do not trust proxy environment variables.
- Scheduled health checks record timestamped observations and update the associated agent's latest runtime status.
- Docker Compose scaffold with PostgreSQL on an internal network and API port bound to localhost.
- Initial unit tests and a GitHub Actions CI workflow.

## Not implemented / not yet verified

- Tests have not been executed in a live GitHub Actions runner or VPS during this change.
- No production-grade database migration framework; SQLAlchemy creates new tables at startup.
- No dashboard frontend, alert delivery, OpenTelemetry ingestion, or additional discovery connectors.
- No verified VPS deployment, TLS reverse proxy, backup/restore drill, or production load test.
- DNS is checked before a request, but HTTPX resolves the hostname again when connecting. Use an outbound firewall and only allowlist hostnames controlled by you; strict IP-pinned transport is a further hardening item.

## Security and deployment checklist

1. Set ADMIN_API_TOKEN to a long random secret and keep it server-side.
2. Set MONITOR_ALLOWED_HOSTS to exact HTTPS hostnames you own and authorize; no wildcard entries.
3. Keep the API bound to localhost behind an authenticated TLS reverse proxy. Do not expose PostgreSQL.
4. Add an outbound network policy/firewall that blocks private, loopback, link-local, and metadata-service destinations.
5. Add a migration framework, rate limiting, audit logging, backup/restore validation, and dependency scanning before production use.
6. Run CI and test the actual VPS environment before claiming production readiness.

## Accuracy policy

A public repository is a discovered project candidate, not proof that an agent is running. An HTTP health response proves only that the configured endpoint responded. Runtime execution requires explicit telemetry from a participating agent or operator.
