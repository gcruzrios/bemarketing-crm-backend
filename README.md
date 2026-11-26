# BeMarketing CRM — Backend

FastAPI + SQLAlchemy 2 (async) + PostgreSQL (Neon). Especificación: `BEMARKETING_CRM_Backend.md`.

## Local
```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env          # completar DATABASE_URL (Neon) y SECRET_KEY
.venv/bin/alembic upgrade head
.venv/bin/python -m app.cli create-admin tu@correo.com   # imprime contraseña generada
.venv/bin/uvicorn app.main:app --reload                  # http://localhost:8000/docs
```
`DATABASE_URL` se acepta tal cual la entrega Neon (`postgresql://…?sslmode=require&channel_binding=require`).

## Documentación de la API
Swagger UI en `/docs` y esquema OpenAPI en `/openapi.json`, también en producción. Para ocultarlos: `ENABLE_DOCS=false`.

## Pruebas (BD REAL, se vacía al terminar — nunca producción)
```bash
TEST_DATABASE_URL=postgresql://… .venv/bin/pytest
```

## Agente (n8n)
1. Admin: `POST /api/v1/api-keys` → guarda la key (se muestra una vez).
2. n8n llama `POST /api/v1/agent/leads/upsert` con `X-API-Key` e `Idempotency-Key`.
3. `missing_fields` en la respuesta indica qué preguntar a continuación.

## Tareas diarias
`python -m app.jobs.daily` (facturas vencidas, cotizaciones expiradas, actividades por vencer). En Heroku: Scheduler.

## Purga Ley 8968
`python -m app.cli purge lead|contact <id>` (irreversible).

## Heroku
```bash
heroku create <app>
heroku buildpacks:add --index 1 heroku-community/apt   # libs de WeasyPrint (Aptfile)
heroku buildpacks:add heroku/python
heroku config:set APP_ENV=production SECRET_KEY=$(openssl rand -hex 32) \
  DATABASE_URL='<neon url>' CORS_ORIGINS=https://tu-frontend COOKIE_SECURE=true
git push heroku main        # el release phase corre alembic upgrade head
heroku run python -m app.cli create-admin tu@correo.com
```
Si el frontend vive en otro dominio (no mismo sitio), usar `COOKIE_SAMESITE=none` (requiere `COOKIE_SECURE=true`).
Los PDF congelados se guardan en la BD (el filesystem de Heroku es efímero).
