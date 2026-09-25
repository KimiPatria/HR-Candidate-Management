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
    """Create tables, then apply the handful of changes create_all cannot make itself.

    Fine for MVP; swap both halves for Alembic once the schema stabilises. Order matters:
    create_all first so a brand-new database has every table before the migrations
    inspect it, and the migrations second so an existing one catches up. Both are
    idempotent.
    """
    import app.models  # noqa: F401  (ensure all models are registered)
    from app.core import migrate

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await migrate.run(conn)
