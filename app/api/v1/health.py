from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", summary="Liveness")
async def health():
    return {"status": "ok"}


@router.get("/ready", summary="Readiness (valida conexión a DB)")
async def ready(session: AsyncSession = Depends(get_session)):
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(503, "DB no disponible")
    return {"status": "ready"}
