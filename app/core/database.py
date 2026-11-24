from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# Neon usa pgbouncer (pooler): sin cache de prepared statements.
engine = create_async_engine(
    settings.async_database_url,
    pool_size=5,
    max_overflow=5,
    pool_pre_ping=True,
    pool_recycle=300,
    connect_args={
        "ssl": settings.db_ssl,
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
    },
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session():
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
