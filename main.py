"""Aces Backend"""

# from fastapi import FastAPI
# from typing import Annotated
# import asyncpg
from contextlib import asynccontextmanager
from typing import Any

# import orjson
# import os
import dotenv
from fastapi import Depends, FastAPI, HTTPException, Request  # , Form
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse  # , RedirectResponse
from fastapi.staticfiles import StaticFiles

# from pyairtable import Api
# from pyairtable.formulas import match
# from slowapi import Limiter
# from slowapi.util import get_remote_address
# from slowapi.errors import RateLimitExceeded
# from slowapi.middleware import SlowAPIMiddleware
# from api.auth import client
# from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
# from sqlalchemy.ext.asyncio import AsyncSession
# from sqlalchemy.ext.asyncio import async_sessionmaker
from api.v1.auth import require_auth  # , is_user_authenticated
from api.v1.auth import router as auth_router
from api.v1.auth.main import Permission, permission_dependency
from api.v1.projects import router as projects_router
from api.v1.users import router as users_router
from db import engine  # , get_db
from models.user import Base

# from api.users import foo


dotenv.load_dotenv()

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
async def lifespan(_app: FastAPI):
    """DB setup/teardown"""
    async with engine.begin() as conn:  # startup: create tables if not exist yet
        # await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield

    await engine.dispose()  # shutdown


app = FastAPI(
    lifespan=lifespan,
    title="Aces Backend API",
    redoc_url=None,
    swagger_ui_oauth2_redirect_url=None,
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request: Request, exc: RequestValidationError):
    """Invalid request handler"""
    raise HTTPException(
        status_code=400,
        detail={"errors": exc.errors(), "body": exc.body},
    )


app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(users_router, prefix="/api/v1/users", tags=["users"])
app.include_router(projects_router, prefix="/api/v1/projects", tags=["projects"])

# @app.get("/test")
# async def test():
#     return foo()


@app.get("/")
async def home(_request: Request):
    """Home route"""
    # if client.is
    # if client.isAuthenticated() is False:
    #     return HTMLResponse("Not authenticated <a href='/sign-in'>Sign in</a>")

    # return HTMLResponse("Authenticated <a href='/sign-out'>Sign out</a>")
    return FileResponse("static/login.html")


@app.get("/protectedroute")
@require_auth
async def protected_route(request: Request):
    """Protected route example"""
    user_email = request.state.user["sub"]
    return HTMLResponse(
        f"<h1>Hello World! This is authenticated! Your email is {user_email}! <br>"
        f"Your full string should be {request.state.user}</h1>"
    )


@app.get("/login")
async def serve_login(_request: Request):
    """Login page"""
    return FileResponse("static/login.html")


@app.get("/projectstest")
async def serve_projects_test(_request: Request):
    """Projects test page"""
    return FileResponse("static/projectstest.html")


@app.get("/admin")
async def serve_admin(
    _request: Request,
    _permission: Any = Depends(permission_dependency(Permission.ADMIN)),
) -> str:
    """Admin page"""
    return "test"


# @app.post("/login")
# async def handle_login(email: Annotated[str, Form()], otp: Annotated[int, Form()]):
#     pass

app.mount("/static", StaticFiles(directory="static"), name="static")
