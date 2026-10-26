from __future__ import annotations

import asyncio
import hmac
import ipaddress
import logging
import os
import socket
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import DateTime, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("uaat")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./uaat-dev.db"
    github_token: str = ""
    admin_api_token: str = ""
    discovery_interval_minutes: int = 30
    endpoint_check_interval_minutes: int = 5
    github_search_query: str = "topic:ai-agent"
    monitor_allowed_hosts: str = ""

    @property
    def allowed_hosts(self) -> set[str]:
        return {
            item.strip().lower().rstrip(".")
            for item in self.monitor_allowed_hosts.split(",")
            if item.strip()
        }


settings = Settings()
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
bearer_scheme = HTTPBearer(auto_error=False)


class Base(DeclarativeBase):
    pass


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_type: Mapped[str] = mapped_column(String(40), default="github")
    source_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    source_url: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    stars: Mapped[int] = mapped_column(Integer, default=0)
    language: Mapped[str | None] = mapped_column(String(100), nullable=True)
    license_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    runtime_status: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    classification: Mapped[str] = mapped_column(String(32), default="candidate")


class Observation(Base):
    __tablename__ = "observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(Integer, index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    observation_type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str] = mapped_column(Text, default="")


class MonitoredEndpoint(Base):
    __tablename__ = "monitored_endpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(Integer, index=True)
    url: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str] = mapped_column(String(32), default="UNKNOWN")


Base.metadata.create_all(bind=engine)
scheduler = AsyncIOScheduler()


class EndpointRegistration(BaseModel):
    agent_id: int = Field(ge=1)
    url: HttpUrl


def require_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> None:
    expected = settings.admin_api_token
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Administrative API is disabled until ADMIN_API_TOKEN is configured.",
        )
    if credentials is None or credentials.scheme.lower() != "bearer" or not hmac.compare_digest(
        credentials.credentials, expected
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid bearer token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Universal-Agent-Tracker/0.2",
    }
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    return headers


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def validate_monitor_url(raw_url: str) -> str:
    if len(raw_url) > 2048:
        raise HTTPException(status_code=422, detail="Endpoint URL is too long.")
    parsed = urlsplit(raw_url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() != "https" or not host:
        raise HTTPException(status_code=422, detail="Only HTTPS endpoint URLs are accepted.")
    if parsed.username or parsed.password or parsed.fragment:
        raise HTTPException(status_code=422, detail="Endpoint URL must not contain credentials or fragments.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise HTTPException(status_code=422, detail="IP-literal endpoint URLs are not accepted.")
    if host not in settings.allowed_hosts:
        raise HTTPException(
            status_code=403,
            detail="Hostname is not in MONITOR_ALLOWED_HOSTS; add an exact hostname you control.",
        )
    return host


async def resolve_public_host(host: str) -> None:
    """Reject DNS answers that include private, loopback, link-local, or reserved IPs."""
    loop = asyncio.get_running_loop()
    try:
        answers = await loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("DNS resolution failed") from exc
    addresses = {answer[4][0].split("%", 1)[0] for answer in answers}
    if not addresses:
        raise ValueError("DNS returned no addresses")
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as exc:
            raise ValueError("DNS returned an invalid address") from exc
        if not parsed.is_global:
            raise ValueError("DNS resolved to a non-public address")


async def discover_github() -> dict[str, Any]:
    """Discover public repository candidates; this does not claim they are running agents."""
    params = {"q": settings.github_search_query, "sort": "updated", "order": "desc", "per_page": 50}
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=False, trust_env=False) as client:
        response = await client.get(
            "https://api.github.com/search/repositories",
            params=params,
            headers=github_headers(),
        )
    if response.status_code == 403 and response.headers.get("X-RateLimit-Remaining") == "0":
        raise HTTPException(status_code=429, detail="GitHub API rate limit reached; retry after reset.")
    if response.status_code >= 400:
        logger.warning("GitHub discovery failed with status %s", response.status_code)
        raise HTTPException(status_code=502, detail=f"GitHub discovery failed (HTTP {response.status_code}).")

    payload = response.json()
    items = payload.get("items", [])
    now = datetime.now(timezone.utc)
    inserted = 0
    updated = 0

    with SessionLocal() as session:
        for item in items:
            source_id = str(item.get("id", ""))
            name = str(item.get("full_name") or item.get("name") or "unknown")[:255]
            source_url = str(item.get("html_url") or "")
            if not source_id or not source_url.startswith("https://github.com/"):
                continue
            record = session.scalar(select(Agent).where(Agent.source_id == source_id))
            license_info = item.get("license") or {}
            values = {
                "source_type": "github",
                "source_id": source_id,
                "name": name,
                "source_url": source_url,
                "description": str(item.get("description") or "")[:10000],
                "stars": int(item.get("stargazers_count") or 0),
                "language": item.get("language"),
                "license_name": license_info.get("spdx_id") if isinstance(license_info, dict) else None,
                "source_updated_at": parse_datetime(item.get("updated_at")),
                "last_seen_at": now,
            }
            if record is None:
                session.add(Agent(**values))
                inserted += 1
            else:
                for key, value in values.items():
                    setattr(record, key, value)
                updated += 1
        session.commit()

    logger.info("GitHub discovery completed: inserted=%s updated=%s received=%s", inserted, updated, len(items))
    return {"source": "github", "received": len(items), "inserted": inserted, "updated": updated, "checked_at": now.isoformat()}


async def check_endpoint(endpoint_id: int) -> dict[str, Any]:
    with SessionLocal() as session:
        endpoint = session.get(MonitoredEndpoint, endpoint_id)
        if endpoint is None or not endpoint.enabled:
            return {"endpoint_id": endpoint_id, "status": "SKIPPED"}
        url = endpoint.url
        agent_id = endpoint.agent_id

    now = datetime.now(timezone.utc)
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    try:
        # Revalidate every run; redirects are disabled so a target cannot redirect the
        # monitor to a different host. Exact operator allowlisting is mandatory.
        validated_host = validate_monitor_url(url)
        await resolve_public_host(validated_host)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(5.0, connect=3.0),
            follow_redirects=False,
            trust_env=False,
            verify=True,
            headers={"User-Agent": "Universal-Agent-Tracker-Monitor/0.2"},
        ) as client:
            response = await client.get(url)
        if 200 <= response.status_code < 300:
            result_status = "UP"
            detail = f"HTTP {response.status_code}"
        elif 300 <= response.status_code < 400:
            result_status = "REDIRECT_BLOCKED"
            detail = f"Redirect response HTTP {response.status_code}; redirects are not followed"
        else:
            result_status = "HTTP_ERROR"
            detail = f"HTTP {response.status_code}"
    except (HTTPException, ValueError) as exc:
        result_status = "BLOCKED"
        detail = str(exc.detail if isinstance(exc, HTTPException) else exc)[:500]
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        result_status = "UNREACHABLE"
        detail = type(exc).__name__
    except Exception as exc:
        logger.exception("Endpoint monitor failed for endpoint_id=%s", endpoint_id)
        result_status = "ERROR"
        detail = type(exc).__name__

    with SessionLocal() as session:
        endpoint = session.get(MonitoredEndpoint, endpoint_id)
        if endpoint is None:
            return {"endpoint_id": endpoint_id, "status": "SKIPPED"}
        endpoint.last_checked_at = now
        endpoint.last_status = result_status
        session.add(
            Observation(
                agent_id=agent_id,
                observed_at=now,
                observation_type="endpoint_health",
                status=result_status,
                detail=f"endpoint_id={endpoint_id}; host={host}; {detail}"[:1000],
            )
        )
        agent = session.get(Agent, agent_id)
        if agent is not None:
            agent.runtime_status = result_status
        session.commit()
    return {"endpoint_id": endpoint_id, "status": result_status, "detail": detail, "checked_at": now.isoformat()}


async def run_endpoint_checks() -> None:
    with SessionLocal() as session:
        endpoint_ids = session.scalars(
            select(MonitoredEndpoint.id)
            .where(MonitoredEndpoint.enabled.is_(True))
            .order_by(MonitoredEndpoint.id.asc())
            .limit(100)
        ).all()
    for endpoint_id in endpoint_ids:
        await check_endpoint(endpoint_id)


async def scheduled_discovery() -> None:
    try:
        await discover_github()
    except Exception:
        logger.exception("Scheduled GitHub discovery failed")


async def scheduled_endpoint_checks() -> None:
    try:
        await run_endpoint_checks()
    except Exception:
        logger.exception("Scheduled endpoint checks failed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.discovery_interval_minutes > 0:
        scheduler.add_job(
            scheduled_discovery,
            "interval",
            minutes=max(15, settings.discovery_interval_minutes),
            id="github-discovery",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
    if settings.endpoint_check_interval_minutes > 0:
        scheduler.add_job(
            scheduled_endpoint_checks,
            "interval",
            minutes=max(1, settings.endpoint_check_interval_minutes),
            id="endpoint-health-checks",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
    if scheduler.get_jobs():
        scheduler.start()
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title="Universal AI Agent Tracker",
    version="0.2.0",
    description="Public AI agent project discovery and opt-in endpoint health metadata. Discovery is not proof of runtime execution.",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict[str, str]:
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
        return {"status": "ok", "database": "ok"}
    except Exception as exc:
        logger.error("Health check database failure: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Database unavailable") from exc


@app.get("/api/agents")
def list_agents(
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    with SessionLocal() as session:
        query = select(Agent)
        if search:
            query = query.where(Agent.name.ilike(f"%{search}%"))
        query = query.order_by(Agent.stars.desc(), Agent.name.asc()).limit(limit).offset(offset)
        rows = session.scalars(query).all()
        return {
            "items": [
                {
                    "id": row.id,
                    "name": row.name,
                    "source_type": row.source_type,
                    "source_url": row.source_url,
                    "description": row.description,
                    "stars": row.stars,
                    "language": row.language,
                    "license": row.license_name,
                    "source_updated_at": row.source_updated_at.isoformat() if row.source_updated_at else None,
                    "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
                    "runtime_status": row.runtime_status,
                    "classification": row.classification,
                }
                for row in rows
            ],
            "limit": limit,
            "offset": offset,
        }


@app.post("/api/discovery/github", dependencies=[Depends(require_admin)])
async def run_github_discovery() -> dict[str, Any]:
    return await discover_github()


@app.post("/api/endpoints", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
def register_endpoint(payload: EndpointRegistration) -> dict[str, Any]:
    url = str(payload.url)
    validate_monitor_url(url)
    with SessionLocal() as session:
        if session.get(Agent, payload.agent_id) is None:
            raise HTTPException(status_code=404, detail="Agent not found. Discover or add the agent first.")
        existing = session.scalar(
            select(MonitoredEndpoint).where(
                MonitoredEndpoint.agent_id == payload.agent_id,
                MonitoredEndpoint.url == url,
            )
        )
        if existing is not None:
            existing.enabled = True
            session.commit()
            return {"id": existing.id, "agent_id": existing.agent_id, "url": existing.url, "enabled": existing.enabled}
        endpoint = MonitoredEndpoint(agent_id=payload.agent_id, url=url, enabled=True)
        session.add(endpoint)
        session.commit()
        session.refresh(endpoint)
        return {"id": endpoint.id, "agent_id": endpoint.agent_id, "url": endpoint.url, "enabled": endpoint.enabled}


@app.get("/api/endpoints", dependencies=[Depends(require_admin)])
def list_endpoints() -> dict[str, Any]:
    with SessionLocal() as session:
        rows = session.scalars(select(MonitoredEndpoint).order_by(MonitoredEndpoint.id.asc()).limit(500)).all()
        return {
            "items": [
                {
                    "id": row.id,
                    "agent_id": row.agent_id,
                    "url": row.url,
                    "enabled": row.enabled,
                    "last_checked_at": row.last_checked_at.isoformat() if row.last_checked_at else None,
                    "last_status": row.last_status,
                }
                for row in rows
            ]
        }


@app.post("/api/endpoints/{endpoint_id}/check", dependencies=[Depends(require_admin)])
async def trigger_endpoint_check(endpoint_id: int) -> dict[str, Any]:
    with SessionLocal() as session:
        endpoint = session.get(MonitoredEndpoint, endpoint_id)
        if endpoint is None:
            raise HTTPException(status_code=404, detail="Endpoint not found.")
    return await check_endpoint(endpoint_id)


@app.post("/api/endpoints/{endpoint_id}/disable", dependencies=[Depends(require_admin)])
def disable_endpoint(endpoint_id: int) -> dict[str, Any]:
    with SessionLocal() as session:
        endpoint = session.get(MonitoredEndpoint, endpoint_id)
        if endpoint is None:
            raise HTTPException(status_code=404, detail="Endpoint not found.")
        endpoint.enabled = False
        session.commit()
        return {"id": endpoint.id, "enabled": endpoint.enabled}


@app.get("/api/observations")
def list_observations(
    agent_id: int = Query(ge=1),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    with SessionLocal() as session:
        rows = session.scalars(
            select(Observation)
            .where(Observation.agent_id == agent_id)
            .order_by(Observation.observed_at.desc())
            .limit(limit)
        ).all()
        return {
            "items": [
                {
                    "id": row.id,
                    "agent_id": row.agent_id,
                    "observed_at": row.observed_at.isoformat(),
                    "type": row.observation_type,
                    "status": row.status,
                    "detail": row.detail,
                }
                for row in rows
            ]
        }
}
