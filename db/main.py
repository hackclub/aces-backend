"""Database manager"""

import os
from contextlib import asynccontextmanager

import dotenv
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

dotenv.load_dotenv()

log_level = os.getenv("LOG_LEVEL", "INFO").upper()
connection_str = os.getenv("SQL_CONNECTION_STR", "")

engine = create_async_engine(
    url=connection_str,
    echo=log_level == "DEBUG",
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

async_session_maker = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


@asynccontextmanager
async def get_session():  # context manager
    """Get a database session context manager"""
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_db():  # pass in as a depends to get a session
    """Get a database session"""
    async with get_session() as session:
        yield session
