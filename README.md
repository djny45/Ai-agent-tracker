# Universal AI Agent Tracker

Discover and catalogue publicly visible AI-agent projects, then build toward opt-in runtime monitoring from participating operators.

> **Current stage: bootstrap.** This repository discovers public GitHub repository candidates. It does not yet provide universal live tracking, and repository discovery is not evidence that an agent is running.

## Current capabilities

- FastAPI service with health, agent-list, discovery, and observation endpoints.
- GitHub public repository discovery through the official REST API.
- PostgreSQL persistence via SQLAlchemy.
- Scheduled discovery (minimum 15-minute interval).
- Docker Compose deployment scaffold with the API bound to localhost by default.
- Initial unit tests.

## Quick start (Docker Compose)

```bash
cp .env.example .env
```

Edit `.env` and replace `POSTGRES_PASSWORD` with a long random secret. Ensure `DATABASE_URL` uses the same password, then run:

```bash
docker compose up --build -d
docker compose logs -f api
```

The API listens on `127.0.0.1:8000` on the host. Check health at `http://127.0.0.1:8000/health`; interactive API documentation is at `http://127.0.0.1:8000/docs`.

For local development, install `requirements.txt` and run `uvicorn app.main:app --reload`. The application defaults to a local SQLite database if `DATABASE_URL` is not set.

## API endpoints

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/health` | Database-backed health check |
| GET | `/api/agents` | List discovered project candidates; supports `search`, `limit`, `offset` |
| POST | `/api/discovery/github` | Trigger GitHub discovery |
| GET | `/api/observations?agent_id=1` | Read stored observations |

**Security note:** the discovery trigger is not authenticated in this bootstrap. Keep the API private and localhost-bound until authentication and authorization are implemented. Never expose secrets in client code or commit a real `.env` file.

## Architecture

- **Discovery source:** GitHub repository search API.
- **Service:** FastAPI / Uvicorn.
- **Persistence:** SQLAlchemy with PostgreSQL in Compose (SQLite fallback for development).
- **Scheduler:** APScheduler.
- **Deployment:** Docker Compose; PostgreSQL is on an internal network and the API port is bound to host loopback.

## Roadmap

1. Add source connectors and deduplication beyond GitHub search.
2. Add operator-registered endpoints with strict SSRF protections, DNS/IP validation, redirect controls, allowlists, and request budgets.
3. Add opt-in endpoint health checks and timestamped observations.
4. Add authentication, role-based access, audit logging, rate limits, and migrations.
5. Build a dashboard, alerting, metrics/telemetry ingestion, and operational backups.
6. Add CI, integration tests, and a verified VPS deployment.

## What “universal tracking” can and cannot mean

There is no universal directory or protocol that reveals every AI agent. Public repository metadata can help discover projects, but private agents, local agents, ephemeral sessions, and agents that publish no telemetry cannot be reliably detected globally. Live monitoring requires an observable endpoint or explicit telemetry and permission from its operator.

See [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) for the current implementation boundary and deployment checklist.
