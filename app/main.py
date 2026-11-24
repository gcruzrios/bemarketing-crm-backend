import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy.exc import IntegrityError

from app.api.v1 import (
    activities, agent, api_keys, audit_logs, auth, companies, contacts, dashboard, health, invoices,
    leads, notifications, opportunities, pipeline, quotes, search, services, timeline, users,
)
from app.core.config import settings
from app.core.limiter import limiter

log = logging.getLogger("app")

app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description="API REST del CRM comercial de BeMarketing (B2B). Leads alimentados por un agente externo vía API key.",
    docs_url="/docs" if settings.is_dev or settings.APP_ENV == "staging" else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.is_dev or settings.APP_ENV == "staging" else None,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(
    CORSMiddleware, allow_origins=settings.cors_origins_list, allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "Idempotency-Key"],
)


@app.exception_handler(IntegrityError)
async def integrity_handler(request: Request, exc: IntegrityError):
    log.warning("IntegrityError: %s", exc.orig)
    return JSONResponse(status_code=409, content={"detail": "Conflicto de integridad de datos"})


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Cache-Control", "no-store")
    return resp


for r in (auth, users, api_keys, agent, leads, companies, contacts, services, pipeline, opportunities, quotes,
          invoices, activities, notifications, search, timeline, dashboard, audit_logs, health):
    app.include_router(r.router, prefix="/api/v1")


@app.get("/", include_in_schema=False)
async def root():
    return {"app": settings.APP_NAME, "docs": "/docs", "health": "/api/v1/health"}
