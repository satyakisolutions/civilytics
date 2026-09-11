from collections.abc import AsyncGenerator


async def get_db() -> AsyncGenerator:
    # db.engine (Task 3) — lazy import to avoid breaking health tests before Task 3
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.db.engine import async_session  # noqa: PLC0415

    async with async_session() as session:
        yield session
