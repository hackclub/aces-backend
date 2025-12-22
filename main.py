"""Aces Backend"""

# from fastapi import FastAPI
# from typing import Annotated
# import asyncpg
import os
from contextlib import asynccontextmanager
from logging import basicConfig
from typing import Any

# import orjson
# import os
import dotenv
from fastapi import Depends, FastAPI, HTTPException, Request  # , Form
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse  # , RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi_pagination import add_pagination
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

# from pyairtable import Api
# from pyairtable.formulas import match
# from slowapi import Limiter
# from api.auth import client
# from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
# from sqlalchemy.ext.asyncio import AsyncSession
# from sqlalchemy.ext.asyncio import async_sessionmaker
from api.v1.auth import (
    Permission,
    permission_dependency,
    require_auth,  # , is_user_authenticated
)
from api.v1.auth import router as auth_router
from api.v1.devlogs import router as devlogs_router
from api.v1.projects import router as projects_router
from api.v1.users import router as users_router
from db import engine, run_migrations_async  # , get_db
from lib.ratelimiting import limiter

# from api.users import foo

dotenv.load_dotenv()

log_level = os.getenv("LOG_LEVEL", "INFO").upper()
basicConfig(level=log_level)

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
    await run_migrations_async()

    yield

    await engine.dispose()  # shutdown


class CloudflareRealIPMiddleware(BaseHTTPMiddleware):
    """Middleware to extract real client IP from Cloudflare headers"""

    async def dispatch(self, request: Request, call_next: Any):
        headers = request.headers

        real_ip = (
            headers.get("cf-connecting-ip")
            or headers.get("true-client-ip")
            or headers.get("x-forwarded-for", "").split(",")[0].strip()
        )

        if real_ip and request.scope.get("client"):
            request.scope["client"] = (real_ip, request.scope["client"][1])

        return await call_next(request)


app = FastAPI(
    lifespan=lifespan,
    title="Aces Backend API",
    redoc_url=None,
    swagger_ui_oauth2_redirect_url=None,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore
if os.getenv("CLOUDFLARE_IP", "false").lower() == "true":
    app.add_middleware(CloudflareRealIPMiddleware)
app.add_middleware(SlowAPIMiddleware)

ALLOWED_ORIGINS = [o for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=[
        "content-type",
        "authorization",
        "accept",
        "accept-language",
        "content-language",
        "x-airtable-secret",
        "origin",
        "user-agent",
        "cache-control",
    ],
    max_age=3600,
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
app.include_router(devlogs_router, prefix="/api/v1/devlogs", tags=["devlogs"])

add_pagination(app)

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
@require_auth
async def serve_projects_test(request: Request):  # pylint: disable=unused-argument
    """Projects test page"""
    return FileResponse("static/projectstest.html")


@app.get("/admin")
@require_auth
async def serve_admin(
    request: Request,  # pylint: disable=unused-argument
    _permission: Any = Depends(permission_dependency(Permission.ADMIN)),
) -> str:
    """Admin page"""
    return "test"


# @app.post("/login")
# async def handle_login(email: Annotated[str, Form()], otp: Annotated[int, Form()]):
#     pass

app.mount("/static", StaticFiles(directory="static"), name="static")
