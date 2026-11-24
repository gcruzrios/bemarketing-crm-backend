import math
from typing import Any

from fastapi import Query
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession


class PageParams:
    def __init__(self, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)):
        self.page = page
        self.page_size = page_size


async def paginate(session: AsyncSession, stmt: Select, params: PageParams, schema: Any, unique: bool = False) -> dict:
    total = (await session.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    result = await session.execute(stmt.limit(params.page_size).offset((params.page - 1) * params.page_size))
    rows = (result.unique() if unique else result).scalars().all()
    return {
        "items": [schema.model_validate(r) for r in rows],
        "total": total,
        "page": params.page,
        "page_size": params.page_size,
        "total_pages": max(1, math.ceil(total / params.page_size)),
    }
