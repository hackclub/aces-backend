import os
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
import dotenv

dotenv.load_dotenv()

engine = create_async_engine(
    url=os.getenv("SQL_CONNECTION_STR", ""),
    echo=True,
    pool_pre_ping=True, 
    pool_size=10,
    max_overflow=20
)

async_session_maker = async_sessionmaker(
    engine, 
    class_=AsyncSession, 
    expire_on_commit=False
)

@asynccontextmanager
async def get_session(): # context manager
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

async def get_db(): # pass in as a depends to get a session
    async with get_session() as session:
        yield session