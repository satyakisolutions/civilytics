from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config.settings import settings

engine = create_async_engine(settings.database_url, echo=False, pool_size=10, max_overflow=20)
async_session = async_sessionmaker(engine, expire_on_commit=False)
