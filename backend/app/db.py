from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


engine: AsyncEngine | None = None
session_factory: async_sessionmaker[AsyncSession] | None = None


def initialize_database(database_url: str) -> AsyncEngine:
    global engine, session_factory

    if engine is not None:
        return engine

    engine = create_async_engine(
        database_url,
        pool_pre_ping=True,
        pool_recycle=1800,
        future=True,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine


async def get_session() -> AsyncIterator[AsyncSession]:
    if session_factory is None:
        raise RuntimeError("Database has not been initialized")

    async with session_factory() as session:
        yield session


async def dispose_database() -> None:
    global engine, session_factory

    if engine is not None:
        await engine.dispose()
    engine = None
    session_factory = None
