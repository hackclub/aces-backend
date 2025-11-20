# from fastapi import FastAPI
from typing import Annotated
from fastapi import FastAPI, HTTPException, Request, Form, Depends
from fastapi.responses import FileResponse, RedirectResponse, HTMLResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles

# from pyairtable import Api
# from pyairtable.formulas import match
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

# from api.auth import client
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

# from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker
from api.auth import require_auth, is_user_authenticated
from api.auth import router as auth_router
from api.users.main import router as users_router
from db import get_db, engine
from models.user import Base
import orjson
import os
import dotenv
import asyncpg
from contextlib import asynccontextmanager
from contextlib import asynccontextmanager
# from api.users import foo


dotenv.load_dotenv()

app = FastAPI()


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    raise HTTPException(400)


app.include_router(auth_router)
app.include_router(users_router)

# engine = create_async_engine(
#     url=os.getenv("SQL_CONNECTION_STR", ""),
#     echo=True,
# )

# async_session_generator = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# @asynccontextmanager
# async def get_session():
#     async_session = async_session_generator()
#     try:
#         async with async_session as session:
#             yield session
#     except:
#         await async_session.rollback()
#         raise
#     finally:
#         await async_session.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:  # startup: create tables if not exist yet
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield

    await engine.dispose()  # shutdown


# @app.get("/test")
# async def test():
#     return foo()


@app.get("/")
async def home(request: Request):
    # if client.is
    # if client.isAuthenticated() is False:
    #     return HTMLResponse("Not authenticated <a href='/sign-in'>Sign in</a>")

    # return HTMLResponse("Authenticated <a href='/sign-out'>Sign out</a>")
    return FileResponse("static/login.html")


@app.get("/protectedroute")
@require_auth
async def protected_route(request: Request):
    user_email = request.state.user["sub"]
    return HTMLResponse(
        f"<h1>Hello World! This is authenticated! Your email is {user_email}! Your full string shuold be {request.state.user}</h1>"
    )


@app.get("/login")
async def serve_login(request: Request):
    return FileResponse("static/login.html")


@app.get("/projectstest")
async def serve_projects_test(request: Request):
    return FileResponse("static/projectstest.html")


# @app.post("/login")
# async def handle_login(email: Annotated[str, Form()], otp: Annotated[int, Form()]):
#     pass

app.mount("/static", StaticFiles(directory="static"), name="static")
