"""Database manager"""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

import dotenv
from alembic import command, config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

dotenv.load_dotenv()

log_level = os.getenv("LOG_LEVEL", "INFO").upper()
connection_str = os.getenv("SQL_CONNECTION_STR", "").strip()

engine = create_async_engine(
    url=connection_str,
    echo=log_level == "DEBUG",
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

if not connection_str:
    raise RuntimeError("SQL_CONNECTION_STR is not set; cannot run migrations.")

base_dir = Path(__file__).resolve().parent.parent
alembic_ini = base_dir / "alembic.ini"


def run_migrations_blocking() -> None:
    """Run Alembic migrations synchronously (blocking)."""
    cfg = config.Config(str(alembic_ini))
    cfg.set_main_option("script_location", str(base_dir / "migrations"))
    cfg.attributes["connection"] = connection_str
    command.upgrade(cfg, "head")


async def run_migrations_async() -> None:
    """Run Alembic migrations inside an event loop without nesting loops."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, run_migrations_blocking)


async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=True,
    autocommit=False,
)


@asynccontextmanager
async def get_session():  # context manager
    """Get a database session context manager"""
    async with async_session_maker() as session:
        try:
            yield session
        except Exception:  # type: ignore # pylint: disable=broad-exception-caught
            if session.in_transaction():
                await session.rollback()
            raise


async def get_db():  # pass in as a depends to get a session
    """Get a database session"""
    async with get_session() as session:
        yield session
