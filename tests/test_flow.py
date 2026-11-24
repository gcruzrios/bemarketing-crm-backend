from tests.conftest import PW, login, uniq


# ───────── Auth / RBAC ─────────
def test_health(client):
    assert client.get("/api/v1/health/ready").json() == {"status": "ready"}


def test_login_fail_and_me(client, admin):
    assert client.post("/api/v1/auth/login", json={"email": "admin@test.cr", "password": "mal"}).status_code == 401
    me = client.get("/api/v1/auth/me", headers=admin).json()
    assert me["user"]["role"] == "admin" and "users:manage" in me["capabilities"]
    assert client.get("/api/v1/auth/me").status_code == 401


def test_refresh_rotation(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@test.cr", "password": PW})
    old = r.cookies.get("refresh_token")
    client.cookies.set("refresh_token", old, path="/api/v1/auth")
    r2 = client.post("/api/v1/auth/refresh")
    assert r2.status_code == 200 and r2.json()["access_token"]
    client.cookies.set("refresh_token", old, path="/api/v1/auth")
    assert client.post("/api/v1/auth/refresh").status_code == 401  # reutilización
    client.cookies.clear()


def test_rbac(client, users, admin):
    assert client.get("/api/v1/users", headers=users["sales"]["headers"]).status_code == 403
    assert client.get("/api/v1/leads", headers=users["accounting"]["headers"]).status_code == 403
    assert client.post("/api/v1/pipeline/stages", headers=users["sales"]["headers"], json={"name": "x"}).status_code == 403
    assert client.get("/api/v1/audit-logs", headers=users["manager"]["headers"]).status_code == 200
    assert client.get("/api/v1/audit-logs", headers=users["sales"]["headers"]).status_code == 403


def test_api_key_cannot_use_user_endpoints(client, agent):
    h, _ = agent
    assert client.get("/api/v1/users", headers=h).status_code == 401
    assert client.get("/api/v1/leads", headers=h).status_code == 401
    assert client.get("/api/v1/agent/leads/lookup?email=a@b.cr", headers=h).status_code == 200


def test_agent_requires_key(client):
    assert client.post("/api/v1/agent/leads/upsert", json={"source": "whatsapp", "fields": {}}).status_code == 401


# ───────── Ingesta del agente ─────────
def test_upsert_idempotency_dedupe_manual_conflict(client, agent, users):
    h, _ = agent
    sales = users["sales"]["headers"]
    n = uniq()
    body = {"source": "whatsapp", "first_message": "Hola", "fields": {
        "company_name": f"Hotel {n}", "company_type": "hotel", "whatsapp": "8888 7777", "city": "Quepos"},
        "confidence": {"company_type": 0.6, "city": 0.9}}
    r1 = client.post("/api/v1/agent/leads/upsert", headers={**h, "Idempotency-Key": f"k-{n}"}, json=body)
    assert r1.status_code == 200, r1.text
    j = r1.json()
    assert j["created"] is True and "email_or_phone" not in j["missing_fields"] and "interest_summary" in j["missing_fields"]
    lead_id = j["lead_id"]

    # idempotencia: mismo resultado, sin reprocesar
    r2 = client.post("/api/v1/agent/leads/upsert", headers={**h, "Idempotency-Key": f"k-{n}"}, json=body)
    assert r2.json() == j

    # dedupe por whatsapp normalizado (+506) → enriquece el mismo lead
    r3 = client.post("/api/v1/agent/leads/upsert", headers=h, json={
        "source": "whatsapp", "fields": {"whatsapp": "+506 8888-7777", "email": f"A{n}@Hotel.cr",
                                         "interest_summary": "Motor de reservas"}})
    assert r3.json()["lead_id"] == lead_id and r3.json()["created"] is False
    lead = client.get(f"/api/v1/leads/{lead_id}", headers=sales).json()
    assert lead["email"] == f"a{n}@hotel.cr" and lead["whatsapp"] == "+50688887777"

    # una persona edita la ciudad → el agente no la pisa
    assert client.put(f"/api/v1/leads/{lead_id}", headers=sales, json={"city": "Manuel Antonio"}).status_code == 200
    r4 = client.post("/api/v1/agent/leads/upsert", headers=h, json={
        "source": "whatsapp", "fields": {"email": f"a{n}@hotel.cr", "city": "Jacó"}, "confidence": {"city": 0.99}})
    assert r4.json()["skipped_manual"] == ["city"]
    assert client.get(f"/api/v1/leads/{lead_id}", headers=sales).json()["city"] == "Manuel Antonio"

    # conflicto: confianza menor no pisa y marca needs_review
    r5 = client.post("/api/v1/agent/leads/upsert", headers=h, json={
        "source": "whatsapp", "fields": {"whatsapp": "+50688887777", "company_type": "cafe"}, "confidence": {"company_type": 0.3}})
    assert r5.json()["conflicts"][0]["field"] == "company_type" and r5.json()["enrichment_status"] == "needs_review"
    # confianza mayor sí sobrescribe (pero sigue en needs_review hasta que una persona resuelva)
    r6 = client.post("/api/v1/agent/leads/upsert", headers=h, json={
        "source": "whatsapp", "fields": {"whatsapp": "+50688887777", "company_type": "restaurant"}, "confidence": {"company_type": 0.99}})
    assert r6.json()["conflicts"] == []
    assert client.get(f"/api/v1/leads/{lead_id}", headers=sales).json()["company_type"] == "restaurant"
    rr = client.post(f"/api/v1/leads/{lead_id}/resolve-review", headers=sales, json={"note": "ok"})
    assert rr.status_code == 200 and rr.json()["enrichment_status"] != "needs_review"

    # el agente no puede cambiar status
    r7 = client.post("/api/v1/agent/leads/upsert", headers=h, json={"source": "whatsapp", "fields": {"status": "qualified"}})
    assert r7.status_code == 422
    assert len(client.get(f"/api/v1/leads/{lead_id}/events", headers=sales).json()) >= 4

    # tarea de seguimiento
    ra = client.post(f"/api/v1/agent/leads/{lead_id}/activities", headers=h, json={"subject": "Llamar mañana"})
    assert ra.status_code == 201


def test_manager_notified_on_new_agent_lead(client, users):
    n = client.get("/api/v1/notifications?unread_only=true", headers=users["manager"]["headers"]).json()
    assert n["total"] >= 1


# ───────── Conversión + comercial ─────────
def test_full_commercial_flow(client, users, admin):
    sales, acc = users["sales"]["headers"], users["accounting"]["headers"]
    n = uniq()
    svc = client.post("/api/v1/services", headers=admin, json={"name": f"Reservas {n}", "code": f"RD-{n}", "price": "500.00",
                                                              "service_type": "direct_bookings"}).json()
    lead = client.post("/api/v1/leads", headers=sales, json={
        "company_name": f"Café Único {n}", "company_type": "cafe", "contact_first_name": "Ana", "email": f"ana{n}@cafe.cr",
        "interest_summary": "SEO", "source": "phone"}).json()
    assert lead["manual_fields"] and lead["completeness"] > 0

    conv = client.post(f"/api/v1/leads/{lead['id']}/convert", headers=sales, json={"expected_value": 1000})
    assert conv.status_code == 200, conv.text
    c = conv.json()
    assert client.post(f"/api/v1/leads/{lead['id']}/convert", headers=sales, json={}).status_code == 409  # idempotencia

    # empresa muy parecida → 409 con possible_duplicate
    lead2 = client.post("/api/v1/leads", headers=sales, json={"company_name": f"Café Único {n}"}).json()
    d = client.post(f"/api/v1/leads/{lead2['id']}/convert", headers=sales, json={})
    assert d.status_code == 409 and "possible_duplicate" in d.json()["detail"]
    ok = client.post(f"/api/v1/leads/{lead2['id']}/convert", headers=sales, json={"company_id": c["company_id"], "create_opportunity": False})
    assert ok.status_code == 200

    opp = client.get(f"/api/v1/opportunities/{c['opportunity_id']}", headers=sales).json()
    assert opp["status"] == "open"
    stages = client.get("/api/v1/pipeline/stages", headers=sales).json()
    mv = client.post(f"/api/v1/opportunities/{opp['id']}/move", headers=sales, json={"stage_id": stages[4]["id"]})
    assert mv.json()["probability"] == stages[4]["probability"]
    board = client.get("/api/v1/pipeline/board", headers=sales).json()
    assert any(b["opportunities"] for b in board)

    # cotización: totales en backend
    q = client.post("/api/v1/quotes", headers=sales, json={
        "opportunity_id": opp["id"], "items": [{"service_id": svc["id"], "quantity": 2, "discount": 100}]})
    assert q.status_code == 201, q.text
    q = q.json()
    assert q["quote_number"].startswith("COT-") and q["subtotal"] == "1000.00" and q["discount"] == "100.00"
    assert q["tax"] == "117.00" and q["total"] == "1017.00"
    assert client.post(f"/api/v1/quotes/{q['id']}/convert-to-invoice", headers=acc).status_code == 409  # no aceptada
    assert client.post(f"/api/v1/quotes/{q['id']}/accept", headers=sales).status_code == 409  # borrador
    s = client.post(f"/api/v1/quotes/{q['id']}/send", headers=sales)
    assert s.status_code == 200 and s.json()["status"] == "sent"
    pdf = client.get(f"/api/v1/quotes/{q['id']}/pdf", headers=sales)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert client.put(f"/api/v1/quotes/{q['id']}", headers=sales, json={"notes": "x"}).status_code == 409  # congelada
    assert client.post(f"/api/v1/quotes/{q['id']}/accept", headers=sales).json()["accepted_at"]

    # snapshot: cambiar precio no altera la cotización
    client.put(f"/api/v1/services/{svc['id']}", headers=admin, json={"price": "999.00"})
    assert client.get(f"/api/v1/quotes/{q['id']}", headers=sales).json()["total"] == "1017.00"

    # factura: contabilidad convierte, sales no puede
    assert client.post(f"/api/v1/quotes/{q['id']}/convert-to-invoice", headers=sales).status_code == 403
    inv = client.post(f"/api/v1/quotes/{q['id']}/convert-to-invoice", headers=acc)
    assert inv.status_code == 201 and inv.json()["invoice_number"].startswith("FAC-")
    assert client.post(f"/api/v1/quotes/{q['id']}/convert-to-invoice", headers=acc).status_code == 409
    iid = inv.json()["id"]
    assert client.post(f"/api/v1/invoices/{iid}/send", headers=acc).json()["status"] == "sent"
    assert client.post(f"/api/v1/invoices/{iid}/mark-paid", headers=sales).status_code == 403
    paid = client.post(f"/api/v1/invoices/{iid}/mark-paid", headers=acc).json()
    assert paid["status"] == "paid" and paid["paid_date"]
    assert client.get(f"/api/v1/invoices/{iid}/pdf", headers=acc).content.startswith(b"%PDF")

    # ganar oportunidad
    assert client.post(f"/api/v1/opportunities/{opp['id']}/won", headers=sales).json()["status"] == "won"

    # transversales
    assert client.get(f"/api/v1/search?q=Único {n}", headers=sales).json()["companies"]
    tl = client.get(f"/api/v1/timeline?entity_type=lead&entity_id={lead['id']}", headers=sales).json()
    assert {e["kind"] for e in tl} >= {"lead_event", "opportunity", "quote"}
    for role in ("sales", "accounting"):
        assert client.get("/api/v1/dashboard", headers=users[role]["headers"]).status_code == 200
    d = client.get("/api/v1/dashboard", headers=admin).json()
    assert d["won_this_month"]["count"] >= 1 and "receivable" in d


def test_pipeline_rules(client, users, admin):
    stages = client.get("/api/v1/pipeline/stages", headers=admin).json()
    assert client.delete(f"/api/v1/pipeline/stages/{stages[-1]['id']}", headers=admin).status_code == 409  # Perdido
    new = client.post("/api/v1/pipeline/stages", headers=users["manager"]["headers"], json={"name": "Demo", "probability": 50})
    assert new.status_code == 201
    assert client.delete(f"/api/v1/pipeline/stages/{new.json()['id']}", headers=admin).status_code == 204


def test_company_duplicates_and_soft_delete(client, users):
    sales = users["sales"]["headers"]
    n = uniq()
    a = client.post("/api/v1/companies", headers=sales, json={"name": f"Hotel Dup {n}", "tax_id": f"3-101-{n}"})
    assert a.status_code == 201
    assert client.post("/api/v1/companies", headers=sales, json={"name": "Otra", "tax_id": f"3-101-{n}"}).status_code == 409
    b = client.post("/api/v1/companies", headers=sales, json={"name": f"Hotel Dup {n}"}).json()
    assert b["possible_duplicate"]["id"] == a.json()["id"]
    assert client.delete(f"/api/v1/companies/{b['id']}", headers=sales).status_code == 204
    assert client.get(f"/api/v1/companies/{b['id']}", headers=sales).status_code == 404
    ct = client.post("/api/v1/contacts", headers=sales, json={"first_name": "X", "company_id": a.json()["id"]})
    assert ct.status_code == 201
    assert client.post("/api/v1/contacts", headers=sales, json={"first_name": "X"}).status_code == 422  # company_id obligatorio


def test_users_admin(client, admin, users):
    assert client.post("/api/v1/users", headers=admin, json={"first_name": "a", "last_name": "b", "email": "sales@test.cr",
                                                             "password": PW, "role": "sales"}).status_code == 409
    me = client.get("/api/v1/auth/me", headers=admin).json()["user"]["id"]
    assert client.post(f"/api/v1/users/{me}/deactivate", headers=admin).status_code == 409
    keys = client.get("/api/v1/api-keys", headers=admin).json()
    assert "key" not in keys[0] and keys[0]["last_used_at"]
    kid = keys[0]["id"]
    client.post(f"/api/v1/api-keys/{kid}/revoke", headers=admin)


def test_revoked_key_rejected(client, agent):
    h, _ = agent
    assert client.get("/api/v1/agent/leads/lookup?email=x@y.cr", headers=h).status_code == 401
