# Universal AI Agent Tracker

Discover public AI-agent project candidates and monitor explicitly registered runtime endpoints with operator authorization.

> **Current stage: early implementation.** This is not a universal directory of every AI agent. Public repository discovery does not prove that an agent is running.

## Current capabilities

- FastAPI API and SQLAlchemy persistence.
- GitHub repository discovery.
- Authenticated administrative routes using a server-side bearer token.
- Operator-registered HTTPS health endpoints, restricted to an exact hostname allowlist.
- Scheduled endpoint checks with short timeouts and redirects disabled.
- Timestamped health observations and latest status per associated agent.
- Docker Compose deployment scaffold and initial CI tests.

## Quick start (Docker Compose)

```bash
cp .env.example .env
openssl rand -hex 32
```

Put the generated value into `ADMIN_API_TOKEN`. Replace `POSTGRES_PASSWORD` with a long random password and update the password in `DATABASE_URL` to match. Set `MONITOR_ALLOWED_HOSTS` to exact HTTPS hostnames you own and authorize, separated by commas. Leave it empty if you do not want endpoint monitoring enabled.

Start the service:

```bash
docker compose up --build -d
docker compose logs -f api
```

The API is exposed on host loopback at `127.0.0.1:8000`. Health check: `http://127.0.0.1:8000/health`. Interactive API documentation: `http://127.0.0.1:8000/docs`.

For local development, install `requirements.txt` and run `uvicorn app.main:app --reload`. The app uses a local SQLite database when `DATABASE_URL` is unset.

## API

| Method | Endpoint | Access | Purpose |
| --- | --- | --- | --- |
| GET | `/health` | Public | Database-backed health check |
| GET | `/api/agents` | Public | List discovered candidates; supports `search`, `limit`, `offset` |
| POST | `/api/discovery/github` | Admin token | Run GitHub discovery immediately |
| POST | `/api/endpoints` | Admin token | Register an authorized HTTPS endpoint for an existing agent |
| GET | `/api/endpoints` | Admin token | List registered endpoints and latest status |
| POST | `/api/endpoints/{id}/check` | Admin token | Trigger a health check |
| POST | `/api/endpoints/{id}/disable` | Admin token | Disable scheduled checks for an endpoint |
| GET | `/api/observations?agent_id=1` | Public | Read stored observations for an agent |

Admin calls use the header `Authorization: Bearer YOUR_ADMIN_API_TOKEN`. Do not place the token in frontend code or commit it to Git.

### Example registration

First run discovery, then inspect `GET /api/agents` and choose an existing agent ID. Register an HTTPS health endpoint whose hostname is in your allowlist:

```bash
curl -X POST http://127.0.0.1:8000/api/endpoints \
  -H "Authorization: Bearer $ADMIN_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agent_id": 1, "url": "https://status.example.com/health"}'
```

Replace the example URL with a health endpoint you own or have explicit permission to monitor. The service rejects non-HTTPS URLs, credentials, IP-literal URLs, unallowlisted hosts, and DNS answers that include non-public addresses. Redirects are not followed. The monitoring scheduler checks enabled endpoints on the configured interval.

## Architecture

- **Discovery:** GitHub REST API repository search.
- **API:** FastAPI / Uvicorn.
- **Persistence:** SQLAlchemy; PostgreSQL in Compose, SQLite for local development.
- **Scheduler:** APScheduler.
- **Deployment:** Docker Compose, internal database network, API bound to host loopback.

## Security boundaries

- Configure a long random `ADMIN_API_TOKEN`; admin routes are disabled if it is missing.
- Use only exact `MONITOR_ALLOWED_HOSTS` hostnames that you control. Do not use wildcards.
- Keep the API private behind TLS and an authenticated reverse proxy. Do not expose PostgreSQL.
- Use an outbound firewall to block internal/private and cloud metadata destinations.
- DNS is checked before each probe, but the HTTP client resolves the hostname again at connection time; allowlist only trusted hosts and use network egress controls.

## Roadmap

1. Pin monitored connections to the validated DNS address and add explicit egress controls.
2. Add database migrations, rate limits, audit logs, and richer authorization.
3. Add dashboard, alerts, metrics/telemetry ingestion, and more discovery sources.
4. Run the CI suite, deploy to the VPS, configure TLS, and validate backups/restore.

## What “universal tracking” can and cannot mean

There is no universal directory or protocol that reveals every AI agent. Private agents, local agents, ephemeral sessions, and agents that publish no telemetry cannot be reliably discovered. Live monitoring requires an observable endpoint or explicit telemetry and permission from its operator.

See [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) for current limits and deployment requirements.
