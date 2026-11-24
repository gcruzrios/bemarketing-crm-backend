"""Pruebas de integración contra una BD REAL.

Requieren TEST_DATABASE_URL (BD dedicada o rama de Neon). Al terminar se vacían las tablas de datos
(se conservan las etapas del pipeline). NUNCA apuntar a producción.
"""
import asyncio
import os
import uuid

import pytest

TEST_URL = os.environ.get("TEST_DATABASE_URL")
if not TEST_URL:
    pytest.skip("Defina TEST_DATABASE_URL para correr las pruebas de integración", allow_module_level=True)

os.environ["DATABASE_URL"] = TEST_URL
os.environ.setdefault("SECRET_KEY", "test-secret-key-test-secret-key-0000")
os.environ["APP_ENV"] = "development"
os.environ["COOKIE_SECURE"] = "false"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.core.limiter import limiter  # noqa: E402
from app.main import app  # noqa: E402

limiter.enabled = False  # el rate limit se prueba aparte

PW = "Password-12345"
DATA_TABLES = ("audit_logs, notifications, activities, invoice_items, invoices, quote_items, quotes, "
               "opportunity_services, opportunities, contacts, companies, services, lead_events, ingest_events, "
               "leads, api_keys, password_reset_tokens, refresh_tokens, users, document_counters")


def _admin_engine():
    return create_async_engine(settings.async_database_url, poolclass=NullPool,
                               connect_args={"ssl": settings.db_ssl, "statement_cache_size": 0})


async def _sql(stmt, **params):
    eng = _admin_engine()
    async with eng.begin() as c:
        await c.execute(text(stmt), params)
    await eng.dispose()


@pytest.fixture(scope="session")
def client():
    asyncio.run(_sql(f"TRUNCATE {DATA_TABLES} CASCADE"))
    asyncio.run(_sql(
        "INSERT INTO users (first_name,last_name,email,password_hash,role) VALUES ('Ad','Min','admin@test.cr',:h,'admin')",
        h=hash_password(PW)))
    with TestClient(app) as c:
        yield c
    asyncio.run(_sql(f"TRUNCATE {DATA_TABLES} CASCADE"))


def login(client, email, pw=PW):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="session")
def admin(client):
    return login(client, "admin@test.cr")


@pytest.fixture(scope="session")
def users(client, admin):
    out = {}
    for role in ("manager", "sales", "accounting"):
        r = client.post("/api/v1/users", headers=admin, json={
            "first_name": role, "last_name": "T", "email": f"{role}@test.cr", "password": PW, "role": role})
        assert r.status_code == 201, r.text
        out[role] = {"headers": login(client, f"{role}@test.cr"), "id": r.json()["id"]}
    return out


@pytest.fixture(scope="session")
def agent(client, admin):
    r = client.post("/api/v1/api-keys", headers=admin, json={"name": "n8n"})
    assert r.status_code == 201, r.text
    return {"X-API-Key": r.json()["key"]}, r.json()


def uniq() -> str:
    return uuid.uuid4().hex[:8]
