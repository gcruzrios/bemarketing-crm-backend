# BeMarketing CRM — Especificación de Backend (REST API)

**Versión:** 1.0 (derivada de la spec final del CRM Clínica Satori v2.0)
**Backend:** Python 3.12 + FastAPI
**Base de datos:** PostgreSQL 16
**ORM:** SQLAlchemy 2.x (async) + Alembic
**Autenticación:** JWT de vida corta + refresh token en cookie httpOnly; API key para el agente de ingesta
**Arquitectura:** REST API

> Este documento parte del CRM de Satori y conserva lo que ya fue revisado y resuelto allá (soft delete, numeración atómica de documentos, conversión transaccional, RBAC por capacidades, paginación estándar). Los cambios específicos de BeMarketing están marcados con `[BM]`.

---

## 0. Qué cambia respecto a Satori

| Tema | Satori | BeMarketing CRM |
|---|---|---|
| Tipo de cliente | B2C + B2B | **Solo B2B** (empresas) |
| Pipelines | `b2c` y `b2b`, tabla `pipelines` | **Un único pipeline**, solo tabla `pipeline_stages` |
| Comunicaciones | Módulo completo (email/WhatsApp, inbox, webhooks, ventana 24h) | **Fuera del CRM** por ahora: sin tabla `communications`, sin endpoints de envío ni inbox |
| Origen de leads | Webhooks directos de Meta/email dentro del CRM | **Agente externo** (WhatsApp + Mail) que crea y va completando leads vía API |
| Worker / Redis | Obligatorio (Celery/arq) | **No en el MVP** (ver §14) |
| Tiempo real | WebSocket | **No**; notificaciones por polling |
| Roles | admin, manager, sales, therapist, accounting | admin, manager, sales, accounting |
| Cumplimiento | Ley 8968 con foco en datos de pacientes | Ley 8968 aplicada a datos de contacto de personas en empresas (§13) |

Alcance se mantiene como **CRM comercial**: leads, empresas, contactos, oportunidades, cotizaciones, facturas internas, actividades.

---

## 1. Objetivo

CRM sencillo para BeMarketing (servicios de marketing digital y reservaciones directas para hoteles, restaurantes y cafés en Costa Rica). Los leads llegan por **WhatsApp y Mail** a través de un agente que conversa con el prospecto y va rellenando la ficha del lead; el CRM es el lugar donde el equipo comercial revisa, califica, convierte y da seguimiento.

Ciclo:

```
WhatsApp / Mail
      ↓ (agente)
Lead (se va completando) → Calificación → Company + Contact → Opportunity → Quote → Invoice
```

---

## 2. Arquitectura

```
   WhatsApp ─┐                                    ┌──────────────────────┐
             ├─► Agente (n8n + LLM) ── API key ──►│      FastAPI         │◄── JWT ── React + Tailwind
   Mail ─────┘   (fuera del CRM)                  │      REST API        │
                                                  └──────────┬───────────┘
                                                             │
                                                      ┌──────┴──────┐
                                                      │ PostgreSQL  │
                                                      └─────────────┘
```

**[BM] Principio clave:** el CRM **no habla** con WhatsApp ni con el correo. Toda la conversación vive en el agente (n8n + proveedor de WhatsApp + buzón). El agente solo le entrega al CRM **datos estructurados** de lo que va aprendiendo del prospecto. Esto elimina del CRM webhooks de proveedor, validación de firmas de Meta, ventana de 24h y almacenamiento de mensajes.

El agente es un cliente más de la API, con credenciales y permisos propios (§5.2, §8).

---

## 3. Base de datos

No hay base provisionada todavía para este proyecto. Se crea un esquema nuevo (Alembic desde la migración inicial; no se mantiene un `schema.sql` paralelo).

- Extensiones: `pgcrypto`, `pg_trgm`.
- **ENUM nativos** para todo campo de estado/tipo.
- **Soft delete** (`deleted_at TIMESTAMPTZ`) en `users`, `leads`, `contacts`, `companies`, `services`, `opportunities`, `quotes`, `invoices`, `activities`. Índices únicos parciales (`WHERE deleted_at IS NULL`).
- Todas las fechas en `TIMESTAMPTZ`, almacenadas en UTC; la zona horaria de presentación es `America/Costa_Rica`.
- Numeración de documentos atómica con `document_counters` + función `next_document_number()` (`INSERT ... ON CONFLICT DO UPDATE`). Formato: `COT-2026-000001`, `FAC-2026-000001`, reinicia por año.
- Credenciales: nunca en el repositorio; `.env` local y secret manager en despliegue.

### 3.1 Tablas

**users**
`id UUID PK, first_name, last_name, email (único parcial), password_hash (argon2id), role (enum), is_active, created_at, updated_at, deleted_at`

Roles: `admin | manager | sales | accounting`.

**refresh_tokens**
`id, user_id FK, token_hash, expires_at, revoked_at, created_at, user_agent, ip_address`

**api_keys** `[BM]`
Credenciales del agente de ingesta.
`id, name, key_prefix (para identificarla), key_hash, scopes (text[]), is_active, last_used_at, created_by FK users, created_at, revoked_at`

**leads**
Un lead es una empresa prospecto (con su persona de contacto) todavía no convertida.

```
id UUID PK

-- Empresa prospecto
company_name VARCHAR(255)
company_type ENUM(hotel, restaurant, cafe, tour_operator, other)  [BM, ver §17]
website VARCHAR(255)
city VARCHAR(100)

-- Persona de contacto
contact_first_name VARCHAR(100)
contact_last_name  VARCHAR(100)
contact_job_title  VARCHAR(150)
email VARCHAR(255)
phone VARCHAR(50)              -- normalizado
whatsapp VARCHAR(50)           -- normalizado

-- Origen
source ENUM(whatsapp, email, website, referral, phone, social, other)
source_reference VARCHAR(255)  -- id de conversación/hilo en el agente, opcional
first_message TEXT             -- mensaje inicial del prospecto (tal cual)

-- Necesidad (lo que el agente va aprendiendo)
interest_summary TEXT          -- resumen del agente de lo que busca
interested_services UUID[]     -- opcional, ids de services
budget_hint VARCHAR(100)
timeline_hint VARCHAR(100)

-- Gestión
status ENUM(new, contacted, qualified, unqualified, converted, lost)
score INTEGER
assigned_to UUID FK users

-- Enriquecimiento agéntico [BM]
enrichment_status ENUM(incomplete, complete, needs_review)
completeness SMALLINT          -- 0–100, calculado por el backend
manual_fields TEXT[]           -- campos editados por una persona; el agente no los pisa
field_meta JSONB               -- { "email": {"by":"agent","confidence":0.9,"at":"..."} }

created_at, updated_at, converted_at, deleted_at
```

**lead_events** `[BM]`
Bitácora ligera del lead (no es historial de mensajes): qué cambió, quién lo cambió y de dónde.
`id, lead_id FK, actor_type ENUM(user, agent, system), actor_id UUID NULL, event_type (created | enriched | field_updated | status_changed | assigned | converted | flagged_for_review), changes JSONB, note TEXT, created_at`

**ingest_events** `[BM]`
Idempotencia de la ingesta del agente (reemplaza a `webhook_events`).
`id, api_key_id FK, idempotency_key (único por api_key), payload JSONB, result_lead_id, status ENUM(processed, rejected), created_at`

**companies**
`id, name, legal_name, tax_id (único parcial si no es nulo), company_type (enum), industry, email, phone, website, address, city, country, notes, created_at, updated_at, deleted_at`

**contacts**
`id, first_name, last_name, email, phone, whatsapp, job_title, company_id FK (obligatorio) , lead_id FK, notes, created_at, updated_at, deleted_at`

**[BM]** Todo contacto pertenece a una empresa: `company_id NOT NULL`.

**services** (catálogo)
`id, name, code (único parcial), description, service_type ENUM(...), price NUMERIC(12,2), currency ENUM(CRC, USD), billing_type ENUM(one_time, monthly, hourly, custom), is_active, created_at, updated_at, deleted_at`

`service_type` queda como un enum editable por migración; valores iniciales sugeridos en §17.

**pipeline_stages** `[BM]` (un solo pipeline)
`id, name, stage_order, probability, color, is_won, is_lost, created_at`

Etapas iniciales (tomadas del pipeline B2B de Satori; editables):
`Lead Empresarial → Contacto → Reunión → Diagnóstico → Propuesta → Negociación → Cierre → Ganado → Perdido`

Restricciones: exactamente una etapa `is_won` y una `is_lost`; no se puede borrar una etapa con oportunidades abiertas.

**opportunities**
`id, name, stage_id FK, lead_id FK, company_id FK (NOT NULL), contact_id FK, expected_value, currency, probability, expected_close_date, status ENUM(open, won, lost), lost_reason, assigned_to FK, notes, created_at, updated_at, closed_at, deleted_at`

**[BM]** `company_id` es siempre obligatorio. Se elimina el trigger b2b/b2c de Satori. Se mantiene: `contact_id`, si existe, debe pertenecer a `company_id`.

**opportunity_services**
`id, opportunity_id, service_id, quantity, unit_price, discount, total`
Snapshot de precio al momento de agregarse.

**quotes** / **quote_items**
Igual que Satori: `quote_number`, `opportunity_id`, `company_id`, `contact_id`, `status ENUM(draft, sent, viewed, accepted, rejected, expired, cancelled)`, `subtotal, discount, tax, total, currency, valid_until, notes, pdf_path, created_by, created_at, updated_at, sent_at, accepted_at, deleted_at`.
Items: `quote_id, service_id, description, quantity, unit_price, discount, tax, total` (snapshot de precio).

**invoices** / **invoice_items**
Igual que Satori: `invoice_number`, `quote_id`, `opportunity_id`, `company_id`, `contact_id`, `status ENUM(draft, issued, sent, partial, paid, overdue, cancelled)`, `subtotal, discount, tax, total, currency, issue_date, due_date, paid_date, notes, pdf_path, ...`.

> **Importante:** estas facturas son **documentos comerciales internos**, no comprobantes electrónicos válidos ante Hacienda. La facturación electrónica oficial queda fuera del MVP y se resuelve con un sistema aparte o una fase posterior. Ver §17.

**activities**
`id, type ENUM(call, email, whatsapp, meeting, task, note, follow_up), subject, description, lead_id, contact_id, company_id, opportunity_id, assigned_to, status ENUM(pending, completed, cancelled), due_date, completed_at, created_at, deleted_at`

Son tareas y notas **registradas por personas** (o creadas por el agente como tarea de seguimiento). No reemplazan una bandeja de mensajes.

**notifications**
`id, user_id, type, title, body, entity_type, entity_id, read_at, created_at`

**audit_logs**
`id, user_id NULL, actor_type ENUM(user, agent, system), api_key_id NULL, action, entity_type, entity_id, old_data JSONB, new_data JSONB, ip_address, created_at`

**document_counters**
`doc_type, year, last_number` (PK compuesta).

### 3.2 Integridad de negocio (triggers / constraints)

- La etapa de una oportunidad debe existir; al pasar a `is_won`/`is_lost` el backend setea `status` y `closed_at`.
- `contact.company_id` obligatorio; `opportunity.contact_id` debe pertenecer a `opportunity.company_id`.
- `quotes.accepted_at` solo si `status = accepted`.
- Índices `pg_trgm` en `leads(company_name, contact_first_name, contact_last_name, email)`, `contacts`, `companies(name)`.
- Índices: `leads(email, phone, whatsapp, status, source, assigned_to)`, `opportunities(status, stage_id, company_id)`, `quotes(status)`, `invoices(status)`, `activities(due_date)`.

---

## 4. Estructura del proyecto

```
backend/
├── app/
│   ├── main.py
│   ├── core/
│   │   ├── config.py
│   │   ├── security.py          # argon2id, JWT, cookies, verificación de API keys
│   │   ├── database.py
│   │   └── permissions.py       # matriz rol → capacidades (§6)
│   ├── models/                  # User, RefreshToken, ApiKey, Lead, LeadEvent, IngestEvent,
│   │                            # Company, Contact, Service, PipelineStage, Opportunity,
│   │                            # Quote, Invoice, Activity, Notification, AuditLog
│   ├── schemas/
│   ├── api/v1/
│   │   ├── auth.py
│   │   ├── users.py
│   │   ├── api_keys.py          # [BM]
│   │   ├── agent.py             # [BM] endpoints de ingesta para el agente
│   │   ├── leads.py
│   │   ├── companies.py
│   │   ├── contacts.py
│   │   ├── services.py
│   │   ├── pipeline.py
│   │   ├── opportunities.py
│   │   ├── quotes.py
│   │   ├── invoices.py
│   │   ├── activities.py
│   │   ├── notifications.py
│   │   ├── search.py
│   │   ├── timeline.py
│   │   ├── dashboard.py
│   │   └── health.py
│   ├── services/
│   │   ├── lead_service.py          # dedupe, merge del agente, completitud, conversión
│   │   ├── quote_service.py
│   │   ├── invoice_service.py
│   │   ├── pdf_service.py           # WeasyPrint
│   │   └── notification_service.py
│   └── repositories/
├── migrations/                  # Alembic
├── tests/
├── requirements.txt
├── alembic.ini
└── .env
```

No existen `email_service.py`, `whatsapp_service.py`, `webhooks.py`, `communications.py`, `workers/` ni `realtime/`.

---

## 5. Autenticación

### 5.1 Usuarios (personas)

- **Access token JWT:** 15 minutos; el frontend lo guarda solo en memoria.
- **Refresh token:** opaco, guardado hasheado en `refresh_tokens`, cookie `httpOnly + Secure + SameSite=Strict`, rotación en cada refresh, revocable.

```http
POST /auth/login             → set-cookie refresh_token; body: { access_token, user }
POST /auth/refresh           → rota refresh token, devuelve access_token
POST /auth/logout            → revoca refresh token actual
GET  /auth/me                → { user, capabilities: [...] }
POST /auth/forgot-password   → token de un solo uso, vida 30 min
POST /auth/reset-password
```

`/auth/me` devuelve **capacidades explícitas** para que el frontend no mantenga su propia matriz.

### 5.2 Agente (máquina a máquina) `[BM]`

- Header `Authorization: Bearer <api_key>` (o `X-API-Key`). La key se muestra **una sola vez** al crearla; en BD solo se guarda el hash y un prefijo.
- Cada key tiene `scopes`. Scopes iniciales: `leads:ingest`, `leads:read`, `activities:create`.
- Una API key **no** puede usar endpoints de usuarios (`/users`, `/quotes`, `/invoices`, etc.). Solo los de `/agent/*`.
- Revocable en cualquier momento (`revoked_at`), con `last_used_at` visible para detectar uso anómalo.
- Gestión (solo `admin`):

```http
GET    /api-keys
POST   /api-keys              → devuelve la key en texto plano una sola vez
POST   /api-keys/{id}/revoke
```

---

## 6. RBAC — Matriz de permisos

| Recurso | admin | manager | sales | accounting |
|---|---|---|---|---|
| Leads (CRUD + convertir + asignar) | ✅ | ✅ | ✅ | ❌ |
| Empresas / Contactos | ✅ | ✅ | ✅ | ver |
| Pipeline / Oportunidades | ✅ | ✅ | ✅ | ver |
| Configurar etapas del pipeline | ✅ | ✅ | ❌ | ❌ |
| Cotizaciones | ✅ | ✅ | ✅ (crear/enviar) | ver |
| Facturas | ✅ | ✅ | ver | ✅ (crear/marcar pagada) |
| Actividades | ✅ | ✅ | ✅ | ❌ |
| Servicios (catálogo) | ✅ | ✅ | ver | ver |
| Usuarios / API keys / Configuración | ✅ | ❌ | ❌ | ❌ |
| Dashboard | ✅ | ✅ | cartera propia | financiero |
| Auditoría | ✅ | ver | ❌ | ❌ |

Implementación: `Depends(require_capability("quotes:send"))` contra `core/permissions.py`. Un solo archivo define la matriz.

Capacidades base: `leads:read|write|convert|assign`, `companies:read|write`, `contacts:read|write`, `opportunities:read|write`, `pipeline:move|configure`, `quotes:read|write|send`, `invoices:read|write|mark_paid`, `activities:read|write`, `services:read|write`, `users:manage`, `apikeys:manage`, `audit:read`, `dashboard:read`.

`sales` ve y edita todo lo comercial del equipo salvo que se decida limitarlo a "solo lo asignado" (decisión abierta, §17).

---

## 7. REST API

Base: `/api/v1`. Todo listado usa paginación estándar (§9).

### Auth, Users, API keys
Ver §5. Users (solo admin):
```http
GET/POST /users
GET/PUT  /users/{id}
POST     /users/{id}/deactivate        # soft delete + revoca refresh_tokens
POST     /users/{id}/reset-password
```

### Leads
```http
GET    /leads?status&source&company_type&enrichment_status&assigned_to&created_from&created_to&search&page&page_size
POST   /leads                       # alta manual
GET    /leads/{id}
PUT    /leads/{id}                  # actualización parcial: solo los campos enviados se agregan a manual_fields
DELETE /leads/{id}                  # soft delete
POST   /leads/{id}/assign
POST   /leads/{id}/convert
POST   /leads/{id}/resolve-review   # persona confirma/rechaza lo que el agente dejó en needs_review
GET    /leads/{id}/events           # lead_events
```

### Agente `[BM]`
Ver §8.
```http
POST /agent/leads/upsert
GET  /agent/leads/lookup?email=&phone=&whatsapp=
GET  /agent/leads/{id}
POST /agent/leads/{id}/activities
```

### Companies / Contacts / Services
```http
GET/POST/GET{id}/PUT{id}/DELETE{id}  /companies
GET/POST/GET{id}/PUT{id}/DELETE{id}  /contacts
GET/POST/GET{id}/PUT{id}/DELETE{id}  /services
```
`?page&page_size&search`. Al crear `companies`/`contacts` directamente, se devuelve `possible_duplicate: {id, name}` sin bloquear (por `tax_id` o nombre trigram para empresas; por email para contactos).

### Pipeline `[BM]`
```http
GET   /pipeline/stages
POST  /pipeline/stages
PUT   /pipeline/stages/{id}
POST  /pipeline/stages/reorder       # body: [{id, stage_order}]
DELETE /pipeline/stages/{id}         # 409 si tiene oportunidades abiertas
GET   /pipeline/board                # etapas + oportunidades abiertas agrupadas, para el kanban
```

### Opportunities
```http
GET/POST   /opportunities
GET/PUT/DELETE /opportunities/{id}
POST /opportunities/{id}/move        # body: { stage_id }
POST /opportunities/{id}/won
POST /opportunities/{id}/lost        # body: { lost_reason }
```

### Quotes
```http
GET/POST       /quotes
GET/PUT/DELETE /quotes/{id}
POST /quotes/{id}/send               # genera y congela el PDF; no envía correo (ver §10)
POST /quotes/{id}/accept
POST /quotes/{id}/reject
POST /quotes/{id}/convert-to-invoice # solo si status = accepted
GET  /quotes/{id}/pdf
```

### Invoices
```http
GET/POST   /invoices
GET/PUT    /invoices/{id}
POST /invoices/{id}/send             # marca como enviada y congela el PDF
POST /invoices/{id}/mark-paid
POST /invoices/{id}/cancel
GET  /invoices/{id}/pdf
```

### Activities
```http
GET/POST/PUT/DELETE /activities[..]
POST /activities/{id}/complete
```

### Notifications, Search, Timeline, Dashboard, Health
```http
GET  /notifications?unread_only=true
POST /notifications/{id}/read
POST /notifications/read-all

GET  /search?q=texto                 # leads, companies, contacts, quotes.number, invoices.number

GET  /timeline?entity_type=lead&entity_id={id}
     # mezcla: lead_events + activities + cambios de etapa + quotes/invoices relacionadas

GET  /dashboard

GET  /health
GET  /health/ready                   # valida conexión a DB
```

---

## 8. Ingesta agéntica de leads `[BM]`

Esta es la parte que más difiere de Satori. Objetivo: que el agente pueda **crear un lead con lo mínimo que tenga** y **seguir completándolo** a medida que conversa, sin duplicar y sin pisar lo que una persona ya corrigió.

### 8.1 `POST /agent/leads/upsert`

Scope: `leads:ingest`. Header recomendado: `Idempotency-Key: <id único del evento/mensaje>`.

```json
{
  "source": "whatsapp",
  "source_reference": "conv_8f21",
  "first_message": "Hola, tenemos un hotel en Quepos y queremos recibir más reservas directas",
  "fields": {
    "company_name": "Hotel Ejemplo",
    "company_type": "hotel",
    "contact_first_name": "Ana",
    "whatsapp": "+506 8888-8888",
    "city": "Quepos",
    "interest_summary": "Quiere motor de reservas y reducir comisiones de OTAs"
  },
  "confidence": { "company_type": 0.95, "city": 0.9 },
  "agent_note": "Mencionó que hoy dependen de Booking."
}
```

Comportamiento:

1. **Idempotencia:** si el `Idempotency-Key` ya existe para esa API key, se responde `200` con el resultado guardado y no se reprocesa.
2. **Normalización** de `phone`/`whatsapp` (quita espacios y guiones, homologa `+506`) y de `email` (minúsculas).
3. **Deduplicación:** buscar lead existente (no eliminado, no `converted`) por email → teléfono → whatsapp. Si no hay lead pero hay un **contacto** existente con el mismo email/teléfono, el lead se crea igual y se devuelve `existing_contact: {id, company_id}` para que el humano decida; no se hace nada automático.
4. **Merge** si el lead existe, campo por campo:
   - Si el campo está en `manual_fields` → **no se modifica** (se registra como `skipped_manual` en la respuesta y en `lead_events`).
   - Si el campo está vacío → se llena.
   - Si ya tiene valor distinto → se sobrescribe solo si `confidence` nueva es mayor que la guardada en `field_meta`; en otro caso se deja el valor y se marca el lead `needs_review` con el conflicto en `lead_events`.
5. Se recalcula `completeness` y `enrichment_status`.
6. Se escribe `lead_events` (`created` o `enriched`) y `audit_logs` con `actor_type = agent`.
7. Si es un lead nuevo, se genera `notification` a quien corresponda (asignación automática o a `manager`).

Respuesta:

```json
{
  "lead_id": "…",
  "created": false,
  "completeness": 70,
  "enrichment_status": "incomplete",
  "missing_fields": ["email", "contact_last_name", "interested_services"],
  "skipped_manual": [],
  "conflicts": [],
  "existing_contact": null
}
```

`missing_fields` le dice al agente qué preguntar a continuación; así el CRM define *qué* falta y el agente decide *cómo* preguntarlo.

### 8.2 Completitud

Se calcula en backend (no en el agente) con una lista configurable de campos con peso. Propuesta inicial:

| Campo | Peso |
|---|---|
| company_name | 20 |
| contact_first_name | 10 |
| email **o** whatsapp/phone | 20 |
| company_type | 10 |
| city | 5 |
| interest_summary | 20 |
| interested_services | 10 |
| contact_job_title | 5 |

`enrichment_status = complete` cuando `completeness ≥ 80` y están los obligatorios (empresa + medio de contacto + interés). La lista y los umbrales viven en configuración, no en el código del agente.

### 8.3 Otros endpoints del agente

```http
GET  /agent/leads/lookup?email=&phone=&whatsapp=     # ¿ya existe? devuelve id, status, missing_fields
GET  /agent/leads/{id}                               # estado actual del lead (solo campos de ficha, sin notas internas)
POST /agent/leads/{id}/activities                    # crea tarea de seguimiento "pendiente humano"
```

El agente **no puede**: cambiar `status` a `qualified/converted/lost`, asignar vendedores, convertir, ni leer cotizaciones o facturas. Calificar y convertir son decisiones humanas. (Si más adelante se quiere que sugiera calificación, que lo haga como `score` y nota, no cambiando el estado.)

### 8.4 Alta de leads por correo

El agente (n8n) recibe el mail, extrae los campos y llama al mismo `POST /agent/leads/upsert` con `source = email`. El CRM **no** guarda el correo ni el hilo; `source_reference` apunta al hilo en el buzón.

---

## 9. Paginación

`page` (default 1), `page_size` (default 25, máx. 100):

```json
{ "items": [], "total": 245, "page": 1, "page_size": 25, "total_pages": 10 }
```

---

## 10. Cotizaciones y facturas — alcance

- Numeración atómica (§3). Montos como `NUMERIC(12,2)`; los totales se calculan **en el backend**; el frontend solo los muestra.
- Items son **snapshot**: cambiar el precio en `services` no altera cotizaciones ya creadas.
- `POST /quotes/{id}/send` y `POST /invoices/{id}/send` **no envían correos** (el CRM no tiene salida de mail). Hacen tres cosas: cambian el estado a `sent`, generan y guardan una copia inmutable del PDF (`pdf_path`) y registran el evento. La persona descarga el PDF y lo envía por su propio canal.
- Moneda por documento: `CRC` o `USD`. Impuesto (IVA) configurable por ítem con valor por defecto en configuración (13 % actualmente; confirmar con contabilidad).
- PDF con WeasyPrint, generado on-demand y congelado al enviar.

---

## 11. Conversión de lead

`POST /leads/{id}/convert` ejecuta **una sola transacción**:

```python
async with session.begin():
    company = match_or_create_company(lead)      # por tax_id si viene; si no, por nombre similar → advertencia
    contact = create_contact(lead, company)      # contact.company_id = company.id
    opportunity = create_opportunity(lead, company, contact, stage=first_stage)
    lead.status = "converted"
    lead.converted_at = now()
```

- Body opcional: `company_id` (usar una empresa existente en vez de crear), `create_opportunity` (default true), `opportunity_name`, `expected_value`.
- Si cualquier paso falla, se revierte todo.
- **Idempotencia:** si el lead ya está `converted`, `409 Conflict` con referencia a la oportunidad existente.
- Si ya existe una empresa muy parecida, el endpoint responde `409` con `possible_duplicate` y el cliente reintenta con `company_id` explícito o con `force_create_company=true`. (Es la excepción al criterio de "advertir sin bloquear": en conversión una empresa duplicada genera ruido difícil de deshacer.)

---

## 12. Notificaciones

- Se generan desde `notification_service.py`: lead nuevo, lead asignado, lead marcado `needs_review`, cotización aceptada, factura vencida, actividad próxima a vencer.
- Entrega por **polling** del frontend (`GET /notifications?unread_only=true` cada ~60 s). No hay WebSocket en el MVP.
- Facturas vencidas y actividades próximas requieren una tarea programada diaria (cron del hosting o scheduler simple). Ver §14.

---

## 13. Seguridad y cumplimiento

- Hashing: **argon2id**. JWT HS256 con `SECRET_KEY` fuerte por ambiente.
- Rate limiting con `slowapi` (en memoria en el MVP), por IP y por usuario/API key; límite más estricto en `/auth/login`.
- CORS con orígenes explícitos por ambiente.
- Validación de entrada con Pydantic; SQL parametrizado vía SQLAlchemy.
- API keys del agente: hash en BD, scopes mínimos, revocables, rotación periódica recomendada.
- **El agente puede enviar texto libre del prospecto.** Tratar todo lo que llega por `/agent/*` como dato no confiable: se almacena y se muestra escapado; **nunca** se interpreta como instrucción ni se usa para construir consultas. Los campos de texto tienen longitud máxima.
- **Ley N.º 8968 (Costa Rica):** el CRM guarda nombre, email y teléfono de personas de contacto. Se requiere (a) aviso/consentimiento de tratamiento de datos en el primer contacto que hace el agente, (b) poder atender solicitudes de acceso, rectificación y eliminación (soft delete + `audit_logs` dan trazabilidad; agregar un procedimiento de purga física bajo solicitud), (c) política de retención. Es una guía técnica, no asesoría legal; validar con quien corresponda.
- Auditoría en `audit_logs` para CREATE, UPDATE, DELETE, CONVERT, SEND_QUOTE, ACCEPT_QUOTE, CREATE_INVOICE, MARK_PAID y toda escritura del agente (`actor_type = agent`).

---

## 14. Procesamiento asíncrono — decisión simplificada `[BM]`

Satori exigía Redis + Celery porque procesaba webhooks de Meta y enviaba mensajes. Aquí:

- La ingesta es una **llamada síncrona corta** del agente; no hay proveedor externo esperando un ACK rápido.
- No se envían mails ni WhatsApp desde el CRM.
- El PDF se genera on-demand (carga baja).

Por eso el MVP corre solo con **FastAPI + PostgreSQL**. Las tareas periódicas (marcar facturas `overdue`, expirar cotizaciones, notificar actividades) se resuelven con un cron diario que invoca un comando del proyecto (`python -m app.jobs.daily`).

Se agregará Redis + worker solo si aparece una necesidad real (volumen alto de ingesta, PDFs pesados, envíos desde el CRM). Con rate limiting en memoria, si se escala a más de una instancia del API hay que mover el límite a Redis.

---

## 15. Variables de entorno

```env
APP_NAME=BeMarketing CRM
APP_ENV=development

DATABASE_URL=postgresql+asyncpg://<usuario>:<password>@localhost:5432/bemarketing_crm
DATABASE_URL_SYNC=postgresql://<usuario>:<password>@localhost:5432/bemarketing_crm   # Alembic

SECRET_KEY=<openssl rand -hex 32>
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=15
REFRESH_TOKEN_EXPIRE_DAYS=7

CORS_ORIGINS=http://localhost:5173

DEFAULT_TAX_RATE=0.13
DEFAULT_CURRENCY=USD
TIMEZONE=America/Costa_Rica
```

No hay variables de proveedor de email ni de WhatsApp en el CRM: esas credenciales viven en el agente.

---

## 16. Docker (desarrollo)

```yaml
services:
  api:
    build: .
    env_file: .env
    depends_on: [postgres]
    ports: ["8000:8000"]

  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: bemarketing_crm
    volumes: ["pgdata:/var/lib/postgresql/data"]
    ports: ["5432:5432"]

volumes:
  pgdata:
```

---

## 17. Supuestos y decisiones abiertas

Cosas que asumí al adaptar la spec y que conviene confirmar antes de codificar:

1. **`company_type`** (hotel, restaurant, cafe, tour_operator, other): lo agregué por el enfoque hotelero de BeMarketing; no existía en Satori. Ajustar la lista.
2. **Catálogo de servicios:** `service_type` necesita valores propios de BeMarketing (por ejemplo: reservaciones directas, gestión de redes, contenido/video, SEO/GEO, consultoría). Lo dejé como enum por definir.
3. **Facturación:** asumí que las facturas son internas (control comercial). Si deben ser **facturas electrónicas de Hacienda**, esto cambia el alcance (integración con un proveedor autorizado) y hay que decidirlo antes de la Fase 2.
4. **Visibilidad de `sales`:** la matriz da acceso a todo lo comercial del equipo. Si prefieren que cada vendedor vea solo sus leads/oportunidades, se agrega un filtro por `assigned_to` en la capa de permisos.
5. **Asignación de leads nuevos del agente:** propuse notificar a `manager`/admin y asignar manualmente. Alternativa: round-robin o asignación fija. Hoy son un equipo pequeño, así que lo simple parece suficiente.
6. **Pipeline:** reutilicé las 9 etapas B2B de Satori. Probablemente haya que adaptarlas al proceso real de venta de BeMarketing (por ejemplo, ciclos largos con reuniones exploratorias).
7. **Moneda e IVA:** `USD` por defecto y 13 % de IVA son supuestos.
8. **Contenido de WhatsApp/Mail:** al no guardar mensajes en el CRM, el historial de conversación solo existe en el agente. Si el equipo quiere leerlo desde el lead, hay que dejar un enlace (`source_reference`) o reabrir la decisión de guardar un resumen (`interest_summary` ya cubre lo esencial).

---

## 18. Plan de desarrollo

**Fase 0 — Fundaciones**
Esquema Alembic inicial, auth completa (login/refresh/logout/me), matriz de permisos, paginación estándar, health checks, usuarios y API keys.

**Fase 1 — Núcleo comercial con ingesta**
Leads (+ dedupe + normalización), `/agent/leads/*` (upsert, merge, completitud, `lead_events`), conversión transaccional, Companies, Contacts, Services, Pipeline (etapas + board), Opportunities (move/won/lost). Con esto el agente ya puede alimentar el CRM.

**Fase 2 — Cotizar y facturar**
Quotes (numeración + PDF + send/accept/reject), conversión a factura, Invoices, Activities.

**Fase 3 — Operación**
Notificaciones, Search global, Timeline, Dashboard con métricas reales, auditoría completa, tareas diarias, procedimiento de purga (Ley 8968).

---

## 19. Definition of Done (por endpoint)

- [ ] Validación de entrada con Pydantic.
- [ ] Autorización contra la matriz de capacidades (o scope de API key en `/agent/*`).
- [ ] Paginación estándar si es listado.
- [ ] Escritura en `audit_logs` cuando aplique (incluye acciones del agente).
- [ ] Prueba de integración (happy path + al menos un 4xx). Para `/agent/leads/upsert`: idempotencia, dedupe, respeto de `manual_fields` y conflicto de confianza.
- [ ] Documentado en OpenAPI (`summary`, `description`, ejemplo).
