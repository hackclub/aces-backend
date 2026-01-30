"""Aces Backend"""

# from fastapi import FastAPI
# from typing import Annotated
# import asyncpg
import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

# import orjson
# import os
import dotenv
import sentry_sdk
from fastapi import Depends, FastAPI, Request  # , Form
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
)  # , RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi_pagination import add_pagination
from sentry_sdk.integrations.logging import EventHandler, LoggingIntegration
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
from jobs import (
    cleanup_deleted_users,
    run_cleanup,
    run_devlog_review_sync,
    run_pyramid_sync,
)
from lib.ratelimiting import limiter

# from api.users import foo

dotenv.load_dotenv()

sentry_dsn = os.getenv("SENTRY_DSN")
if sentry_dsn:
    SENSITIVE_KEYS = frozenset(
        {
            "authorization",
            "cookie",
            "x-airtable-secret",
            "password",
            "secret",
            "token",
            "jwt",
            "api_key",
            "apikey",
            "access_token",
            "refresh_token",
            "jwt_secret",
            "hca_client_secret",
            "airtable_api_key",
            "airtable_pyramid_api_key",
            "airtable_review_key",
            "redis_password",
            "postgres_password",
            "sql_connection_str",
            "hackatime_api_key",
        }
    )

    def scrub_sensitive_data(event: dict, hint: dict) -> dict:
        if "request" in event:
            request_data = event["request"]
            if "headers" in request_data:
                for key in list(request_data["headers"].keys()):
                    if key.lower() in SENSITIVE_KEYS:
                        request_data["headers"][key] = "[REDACTED]"
            if "data" in request_data and isinstance(request_data["data"], dict):
                for key in list(request_data["data"].keys()):
                    if key.lower() in SENSITIVE_KEYS:
                        request_data["data"][key] = "[REDACTED]"
            if "cookies" in request_data:
                request_data["cookies"] = "[REDACTED]"
        return event

    sentry_sdk.init(
        dsn=sentry_dsn,
        enable_logs=True,
        send_default_pii=True,
        before_send=scrub_sensitive_data,
        integrations=[
            LoggingIntegration(
                sentry_logs_level=logging.INFO,
                level=logging.INFO,
                event_level=logging.ERROR,
            ),
        ],
        environment=os.getenv("ENVIRONMENT", "development"),
    )

log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)

# Configure aces loggers explicitly (uvicorn can override basicConfig)
_log_formatter = logging.Formatter(
    "%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)
_log_handler = logging.StreamHandler()
_log_handler.setFormatter(_log_formatter)
_sentry_handler = EventHandler(level=logging.ERROR) if sentry_dsn else None
for _logger_name in ("aces.access", "aces.security"):
    _logger = logging.getLogger(_logger_name)
    _logger.setLevel(log_level)
    _logger.addHandler(_log_handler)
    if _sentry_handler:
        _logger.addHandler(_sentry_handler)
    _logger.propagate = False

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
    try:
        await cleanup_deleted_users()
    except Exception:
        pass
    cleanup_task = asyncio.create_task(run_cleanup())
    pyramid_sync_task = asyncio.create_task(run_pyramid_sync())
    devlog_review_task = asyncio.create_task(run_devlog_review_sync())

    yield

    cleanup_task.cancel()
    pyramid_sync_task.cancel()
    devlog_review_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    try:
        await pyramid_sync_task
    except asyncio.CancelledError:
        pass
    try:
        await devlog_review_task
    except asyncio.CancelledError:
        pass

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


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log HTTP requests for observability and security monitoring"""

    async def dispatch(self, request: Request, call_next: Any):
        start_time = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            logging.getLogger("aces.access").exception(
                "Unhandled exception in request %s %s", request.method, request.url.path
            )
            raise

        duration_ms = (time.perf_counter() - start_time) * 1000
        client_ip = request.client.host if request.client else "unknown"
        user_id = self._get_user_identifier(request)

        method = request.method
        path = request.url.path
        status_code = response.status_code

        if status_code >= 500:
            log_level = logging.ERROR
        elif status_code >= 400:
            log_level = logging.WARNING
        else:
            log_level = logging.INFO

        logger = logging.getLogger("aces.access")
        logger.log(
            log_level,
            "%s %s %d %.0fms ip=%s user=%s",
            method,
            path,
            status_code,
            duration_ms,
            client_ip,
            user_id,
        )

        return response

    def _get_user_identifier(self, request: Request) -> str:
        """Get user identifier from authenticated request state"""
        user = getattr(request.state, "user", None)
        if user and isinstance(user, dict):
            user_id = user.get("id")
            if user_id is not None:
                return str(user_id)
        return "anon"


app = FastAPI(
    lifespan=lifespan,
    title="Aces Backend API",
    redoc_url=None,
    swagger_ui_oauth2_redirect_url=None,
)
app.state.limiter = limiter


def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    """Custom rate limit handler with logging"""
    rate_logger = logging.getLogger("aces.security")
    client_ip = request.client.host if request.client else "unknown"
    rate_logger.warning(
        "RATE_LIMIT_EXCEEDED ip=%s path=%s method=%s",
        client_ip,
        request.url.path,
        request.method,
    )
    return _rate_limit_exceeded_handler(request, exc)


app.add_exception_handler(RateLimitExceeded, rate_limit_handler)  # type: ignore
if os.getenv("CLOUDFLARE_IP", "false").lower() == "true":
    app.add_middleware(CloudflareRealIPMiddleware)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(RequestLoggingMiddleware)

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
    logging.getLogger("aces.access").debug("Validation error: %s", exc.errors())
    errors = exc.errors()
    messages = []
    for err in errors:
        loc = " -> ".join(str(error) for error in err.get("loc", []))
        msg = err.get("msg", "Unknown error")
        messages.append(f"{loc}: {msg}" if loc else msg)
    return JSONResponse(
        status_code=400,
        content={"detail": "; ".join(messages), "errors": errors},
    )


app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(users_router, prefix="/api/v1/users", tags=["users"])
app.include_router(projects_router, prefix="/api/v1/projects", tags=["projects"])
app.include_router(devlogs_router, prefix="/api/v1/devlogs", tags=["devlogs"])

add_pagination(app)

# @app.get("/test")
# async def test():
#     return foo()

# @app.post("/login")
# async def handle_login(email: Annotated[str, Form()], otp: Annotated[int, Form()]):
#     pass

# Development-only test routes
if os.getenv("ENVIRONMENT", "development") == "development":
    app.mount("/static", StaticFiles(directory="static"), name="static")

    @app.get("/test-devlog-sync")
    @require_auth
    async def serve_test_devlog_sync(request: Request):
        return FileResponse("static/test-devlog-sync.html")

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

    @app.get("/test-devlog-create")
    @require_auth
    async def serve_test_devlog_create(request: Request):  # pylint: disable=unused-argument
        """Devlog creation test page"""
        return FileResponse("static/test-devlog-create.html")

    @app.get("/admin")
    @require_auth
    async def serve_admin(
        request: Request,  # pylint: disable=unused-argument
        _permission: Any = Depends(permission_dependency(Permission.ADMIN)),
    ) -> str:
        """Admin page"""
        return "test"
