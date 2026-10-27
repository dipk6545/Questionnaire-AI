import asyncio
from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine
from app.core.config import get_settings
from app.models.user import Base
from app.models import examination  # noqa: F401 -- register metadata

target_metadata = Base.metadata


def migrate(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def online():
    url = get_settings().DATABASE_URL
    engine = create_async_engine(url, connect_args={'statement_cache_size': 0} if url.startswith('postgresql') else {})
    try:
        async with engine.connect() as connection:
            await connection.run_sync(migrate)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(url=get_settings().DATABASE_URL, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(online())
