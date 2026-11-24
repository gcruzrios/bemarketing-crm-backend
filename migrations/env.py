import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.models import Base

config = context.config
target_metadata = Base.metadata


def do_run(connection):
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async():
    engine = create_async_engine(
        settings.async_database_url,
        connect_args={"ssl": settings.db_ssl, "statement_cache_size": 0, "prepared_statement_cache_size": 0},
    )
    async with engine.connect() as conn:
        await conn.run_sync(do_run)
    await engine.dispose()


def run_offline():
    context.configure(url=settings.async_database_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    asyncio.run(run_async())
