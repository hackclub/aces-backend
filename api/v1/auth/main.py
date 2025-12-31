"""Auth API routes"""

import asyncio
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from enum import Enum
from functools import wraps
from typing import Any, Awaitable, Callable, Optional


import jwt

# import asyncio
import redis.asyncio as redis
import sqlalchemy
import validators
from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import HTTPException  # , RequestValidationError
from fastapi.responses import RedirectResponse
from pyairtable import Api
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db
from lib.hackatime import get_account
from lib.ratelimiting import limiter
from lib.responses import SimpleResponse
from models.main import User

logger = logging.getLogger(__name__)

TOKEN_EXPIRY_SECONDS = 604800  # 7 days

HOST = "redis" if os.getenv("USING_DOCKER") == "true" else "localhost"
r = redis.Redis(password=os.getenv("REDIS_PASSWORD", ""), host=HOST)

api = Api(
    os.environ["AIRTABLE_API_KEY"]
)  # key with only write permissions to OTP table
otp_table = api.table(os.environ["AIRTABLE_BASE_ID"], os.environ["AIRTABLE_TABLE_ID"])


class Permission(Enum):
    """User permissions"""

    ADMIN = 0


class OtpClientRequest(BaseModel):
    """OTP send request from client"""

    email: str


# class SessionClientRequest(BaseModel):
#     """Session refresh request from client"""

#     email: str


class OTPSuccessResponse(BaseModel):
    """OTP success response to client"""

    success: bool
    sessionId: str


class OtpClientResponse(BaseModel):
    """OTP validation request from client"""

    email: str
    otp: int

    @field_validator("otp")
    @classmethod
    def validate_otp(cls, v: int):
        """Validate that OTP is a 6-digit number"""
        if not 100000 <= v <= 999999:
            raise ValueError("OTP must be a 6-digit number")
        return v


# class SessionRefreshRequest(BaseModel):
#     sess
router = APIRouter()


# @router.route("/callback")
# async def callback(request: Request):
#     try:
#         await client.handleSignInCallback(str(request.url)) # Handle a lot of stuff
#         return RedirectResponse("/") # Redirect the user to the home page after a sign-in
#     except Exception as e:
#         # Change this to your error handling logic
#         raise HTTPException(500)

# @router.route("/sign-in")
# async def sign_in(request: Request):
#     # Get the sign-in URL and redirect the user to it
#     return RedirectResponse(await client.signIn(
#         redirectUri="http://localhost:8000/callback",
#     ))

# @router.route("/sign-up")
# async def sign_up(request: Request):
#     # Get the sign-in URL and redirect the user to it
#     return RedirectResponse(await client.signIn(
#         redirectUri="http://localhost:8000/callback",
#         interactionMode="signUp", # Show the sign-up page on the first screen
#     ))

# @router.route("/sign-out")
# async def sign_out(request: Request):
#     return RedirectResponse(
#         # Redirect the user to the home page after a successful sign-out
#         await client.signOut(postLogoutRedirectUri="http://localhost:8000/")
#     )


# this is how we should do basic auth!
def require_auth(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Require authentication"""

    @wraps(func)
    async def wrapper(request: Request, *args: Any, **kwargs: Any) -> Any:
        user_data = await is_user_authenticated(request)
        if not user_data:
            return RedirectResponse("/login", status_code=401)
        request.state.user = user_data
        return await func(request, *args, **kwargs)

    return wrapper


# @decorator
# def require_auth(func):
#     async def wrapper(*args, request: Request, **kwargs):
#         if not await is_user_authenticated(request):
#             return RedirectResponse("/login", status_code=401)
#         return await func(request, *args, **kwargs)
#     return wrapper


def permission_dependency(permission: Permission):
    """Permission dependency"""

    async def verifier(
        request: Request,
        session: AsyncSession = Depends(get_db),
    ):
        payload = await is_user_authenticated(request)
        result = await session.execute(
            select(User).where(User.email == payload.get("sub"))
        )
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=401)
        if permission.value not in (user.permissions or []):
            raise HTTPException(status_code=403)
        request.state.user = user

    return verifier


# @decorator
# def require_admin(func):
#     """Require admin status"""

#     async def wrapper(*args, request: Request, **kwargs):
#         if not await is_user_authenticated(request) or not await is_user_admin():
#             return RedirectResponse("/login", status_code=418)
#         elif await is_user_authenticated(request) and not await is_user_admin():
#             return RedirectResponse("/home", status_code=403)
#         return await func(request, *args, **kwargs)

#     return wrapper


# @decorator
# def require_reviewer(func):
#     """Require reviewer status"""

#     async def wrapper(*args, request: Request, **kwargs):
#         if not await is_user_authenticated(request) or not await is_user_reviewer():
#             return RedirectResponse("/login", status_code=418)
#         elif await is_user_authenticated(request) and not await is_user_reviewer():
#             return RedirectResponse("/home", status_code=403)
#         return await func(request, *args, **kwargs)

#     return wrapper


class AuthJwt(dict[str, str | int]):
    """Authentication JWT model"""

    sub: str
    iat: int
    email: str


async def is_user_authenticated(request: Request) -> AuthJwt:
    """Check if user is authenticated"""
    session_id = request.cookies.get("sessionId")

    if session_id is None:
        raise HTTPException(status_code=401, detail="No session ID provided")
    try:
        secret = os.getenv("JWT_SECRET")
        if not secret:
            raise HTTPException(status_code=500, detail="Server configuration error")

        decoded_jwt = jwt.decode(
            session_id,
            secret,
            algorithms=["HS256"],
            options={
                "require_sub": True,
                "require_iat": True,
                "verify_exp": True,
                "verify_signature": True,
            },
        )

        return decoded_jwt

    except jwt.ExpiredSignatureError as e:
        raise HTTPException(status_code=401, detail="Token expired") from e
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail="Invalid token") from e
    except Exception as e:
        raise HTTPException(status_code=401, detail="Authentication failed") from e


@router.post("/refresh_session")
@limiter.limit("10/hour")  # type: ignore
async def refresh_token(request: Request, response: Response) -> SimpleResponse:
    """Refresh JWT session token"""
    curr_session_id = request.cookies.get("sessionId")
    if curr_session_id is None:
        raise HTTPException(status_code=401)
    try:
        if not os.getenv("JWT_SECRET"):
            raise HTTPException(status_code=500)
        decoded_jwt = jwt.decode(
            curr_session_id, os.getenv("JWT_SECRET", ""), ["HS256"]
        )
        if datetime.now(timezone.utc) - timedelta(days=7) > datetime.fromtimestamp(
            decoded_jwt["iat"], timezone.utc
        ):
            raise HTTPException(status_code=401)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error refreshing session token")
        raise HTTPException(status_code=401) from e
    ret_jwt = await generate_session_id(decoded_jwt["sub"])
    response.set_cookie(
        key="sessionId", value=ret_jwt, httponly=True, secure=True, max_age=604800
    )
    return SimpleResponse(success=True)


async def send_otp_code(to_email: str, old_email: Optional[str] = None) -> bool:
    """Send OTP to the user's email"""
    if not validators.email(to_email):
        raise HTTPException(status_code=400, detail="Invalid email address")

    otp = secrets.SystemRandom().randrange(100000, 999999)
    await r.setex(f"otp-{to_email}", 600, json.dumps({"otp": otp, "old": old_email}))

    try:
        await asyncio.to_thread(
            lambda: otp_table.create({"OTP": str(otp), "Email": to_email})
        )
    except Exception as e:
        logger.exception("Error sending OTP email")
        raise HTTPException(status_code=500, detail="Error sending OTP email") from e

    return True


@router.post("/send_otp")
@limiter.limit("10/minute")  # type: ignore
async def send_otp(
    request: Request,  # pylint: disable=W0613
    response: Response,  # pylint: disable=W0613
    otp_request: OtpClientRequest,
) -> SimpleResponse:
    """Send OTP to the user's email"""
    await send_otp_code(to_email=otp_request.email)
    return SimpleResponse(success=True)


@router.post("/validate_otp")
@limiter.limit("10/minute")  # type: ignore
async def validate_otp(
    request: Request,  # pylint: disable=W0613
    response: Response,
    otp_client_response: OtpClientResponse,
    session: AsyncSession = Depends(get_db),
) -> OTPSuccessResponse:
    """Validate the OTP provided by the user"""

    if not os.getenv("JWT_SECRET"):
        raise HTTPException(status_code=500)
    redis_data = await r.get(f"otp-{otp_client_response.email}")
    if redis_data is None:
        raise HTTPException(status_code=401, detail="Invalid OTP")
    try:
        stored_otp_json = json.loads(redis_data)
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid OTP") from e
    if not stored_otp_json:
        raise HTTPException(status_code=401, detail="Invalid OTP")
    stored_otp = stored_otp_json.get("otp")
    old_email = stored_otp_json.get("old")
    if not stored_otp:
        raise HTTPException(status_code=401, detail="Invalid OTP")
    if stored_otp != otp_client_response.otp:
        raise HTTPException(status_code=401, detail="Invalid OTP")

    await r.delete(f"otp-{otp_client_response.email}")
    ret_jwt = await generate_session_id(otp_client_response.email)

    result = await session.execute(
        sqlalchemy.select(User).where(User.email == otp_client_response.email)
    )

    if result.scalar_one_or_none() is None:
        if old_email is not None:
            # updating email flow
            user_raw = await session.execute(
                sqlalchemy.select(User).where(User.email == old_email)
            )
            user = user_raw.scalar_one_or_none()
            if user is None:
                raise HTTPException(status_code=404)  # user doesn't exist
            user.email = otp_client_response.email
            try:
                await session.commit()
                await session.refresh(user)
            except IntegrityError as e:
                await session.rollback()
                raise HTTPException(
                    status_code=409,
                    detail="User with this email already exists",
                ) from e
            except Exception as e:  # type: ignore # pylint: disable=broad-exception-caught
                logger.exception("Error updating user email")
                raise HTTPException(
                    status_code=500, detail="Error updating email"
                ) from e
        else:
            # new user flow
            hackatime_data = None
            try:
                hackatime_data = await get_account(otp_client_response.email)
            except Exception:  # type: ignore # pylint: disable=broad-exception-caught
                pass  # unable to fetch hackatime data, continue anyway
            user = User(
                email=otp_client_response.email,
                hackatime_id=hackatime_data.id if hackatime_data else None,
                username=hackatime_data.username if hackatime_data else None,
            )
            try:
                session.add(user)
                await session.commit()
                await session.refresh(user)
            except IntegrityError as e:
                await session.rollback()
                if "email" in str(e.orig).lower():
                    raise HTTPException(
                        status_code=409,
                        detail="User with this email already exists",
                    ) from e
                if "hackatime_id" in str(e.orig).lower():
                    raise HTTPException(
                        status_code=409,
                        detail="User with this hackatime_id already exists",
                    ) from e
                raise HTTPException(
                    status_code=409,
                    detail="User integrity error",
                ) from e
            except Exception as e:  # type: ignore # pylint: disable=broad-exception-caught
                logger.exception("Error creating new user")
                raise HTTPException(
                    status_code=500, detail="Error creating user"
                ) from e

    response.set_cookie(
        key="sessionId", value=ret_jwt, httponly=True, secure=True, max_age=604800
    )
    return OTPSuccessResponse(success=True, sessionId=ret_jwt)


async def generate_session_id(email: str) -> str:
    """Generate a JWT session ID for the given email"""
    secret = os.getenv("JWT_SECRET")

    if not secret:
        raise HTTPException(status_code=500, detail="Server configuration error")

    now = datetime.now(timezone.utc)
    payload: "dict[str, Any]" = {
        "sub": email,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=TOKEN_EXPIRY_SECONDS)).timestamp()),
    }

    token = jwt.encode(payload, secret, algorithm="HS256")
    return token
