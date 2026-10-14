"""Database session and connection engine configuration."""
from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.core.config import get_settings

app_settings = get_settings()


async_engine = create_async_engine(
    app_settings.DATABASE_URL,
    echo=True,
    future=True,
    pool_pre_ping=True,      # Tests connection before using it
    pool_recycle=300,        # Recycles connection after 5 minutes
    connect_args={
        "statement_cache_size": 0  # Required for PgBouncer/LayerBase poolers
    }
)


SessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()