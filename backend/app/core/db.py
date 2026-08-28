from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models.base import Base

if settings.database_url.startswith("sqlite"):
    # Make sure the parent directory exists before SQLite tries to open the file.
    db_file = settings.database_url.split("///")[-1]
    Path(db_file).parent.mkdir(parents=True, exist_ok=True)

engine = create_async_engine(settings.database_url, echo=False, future=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """Create tables. Fine for MVP; swap for Alembic once the schema stabilises."""
    import app.models  # noqa: F401  (ensure all models are registered)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
