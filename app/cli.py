"""Comandos de administración.  python -m app.cli create-admin EMAIL [PASSWORD]"""
import asyncio
import secrets
import sys
import uuid

from sqlalchemy import delete, func, select

from app.core.database import SessionLocal, engine
from app.core.security import hash_password
from app.models import Activity, Contact, Lead, LeadEvent, User, UserRole


async def create_admin(email: str, password: str | None):
    password = password or secrets.token_urlsafe(14)
    async with SessionLocal() as s:
        if (await s.execute(select(User.id).where(func.lower(User.email) == email.lower(), User.deleted_at.is_(None)))).first():
            print("Ya existe un usuario con ese email"); return
        s.add(User(first_name="Admin", last_name="BeMarketing", email=email.lower(),
                   password_hash=hash_password(password), role=UserRole.admin))
        await s.commit()
    print(f"Admin creado: {email}\nContraseña: {password}")


async def purge(kind: str, id_: str):
    """Purga física bajo solicitud (Ley 8968). Irreversible."""
    uid = uuid.UUID(id_)
    async with SessionLocal() as s:
        if kind == "lead":
            await s.execute(delete(LeadEvent).where(LeadEvent.lead_id == uid))
            await s.execute(delete(Activity).where(Activity.lead_id == uid))
            await s.execute(Contact.__table__.update().where(Contact.lead_id == uid).values(lead_id=None))
            r = await s.execute(delete(Lead).where(Lead.id == uid))
        elif kind == "contact":
            await s.execute(delete(Activity).where(Activity.contact_id == uid))
            r = await s.execute(delete(Contact).where(Contact.id == uid))
        else:
            print("kind debe ser lead|contact"); return
        await s.commit()
        print(f"Registros eliminados: {r.rowcount}")


async def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if cmd == "create-admin" and len(sys.argv) >= 3:
            await create_admin(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
        elif cmd == "purge" and len(sys.argv) == 4:
            await purge(sys.argv[2], sys.argv[3])
        else:
            print(__doc__ + "  python -m app.cli purge lead|contact ID")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
