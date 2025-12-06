"""Auth API routes"""

import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from enum import Enum
from functools import wraps
from typing import Callable, Any, Awaitable, Optional

import aiosmtplib
import dotenv
import jwt

# import asyncio
import redis.asyncio as redis
import sqlalchemy
from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import HTTPException  # , RequestValidationError
from fastapi.responses import RedirectResponse, Response, JSONResponse
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select

from db import get_db
from models.user import User

dotenv.load_dotenv()

HOST = "redis" if os.getenv("USING_DOCKER") == "true" else "localhost"
r = redis.Redis(password=os.getenv("REDIS_PASSWORD", ""), host=HOST)

with open("./api/v1/auth/otp.html", "r", encoding="utf8") as f:
    OTP_EMAIL_TEMPLATE = f.read()


class Permission(Enum):
    """User permissions"""

    ADMIN = 0


class OtpClientRequest(BaseModel):
    """OTP send request from client"""

    email: str


class SessionClientRequest(BaseModel):
    """Session refresh request from client"""

    email: str


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
        raise HTTPException(status_code=401)
    try:
        if not os.getenv("JWT_SECRET"):
            raise HTTPException(status_code=500)
        decoded_jwt = jwt.decode(session_id, os.getenv("JWT_SECRET", ""), ["HS256"])
        if datetime.now(timezone.utc) - timedelta(days=7) > datetime.fromtimestamp(
            decoded_jwt["iat"], timezone.utc
        ):
            raise HTTPException(status_code=401)
    except Exception as e:
        raise HTTPException(status_code=401) from e
    return decoded_jwt


@router.post("/refresh_session")
async def refresh_token(
    request: Request, response: Response, session_request: SessionClientRequest
):
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
    except Exception as e:
        raise HTTPException(status_code=401) from e
    ret_jwt = await generate_session_id(session_request.email)
    response.set_cookie(
        key="sessionId", value=ret_jwt, httponly=True, secure=True, max_age=604800
    )
    return JSONResponse({"success": True}, status_code=200)


async def send_otp_code(to_email: str, old_email: Optional[str] = None) -> bool:
    """Send OTP to the user's email"""
    otp = secrets.SystemRandom().randrange(100000, 999999)
    await r.setex(f"otp-{to_email}", 600, json.dumps({"otp": otp, "old": old_email}))
    message = EmailMessage()
    message["From"] = os.getenv("SMTP_EMAIL", "example@example.com")
    message["To"] = to_email
    message["Subject"] = "Aces OTP Code"
    message.set_content(OTP_EMAIL_TEMPLATE.replace("{{OTP}}", str(otp)), subtype="html")

    try:
        await aiosmtplib.send(
            message,
            hostname=os.getenv("SMTP_SERVER", "smtp.example.com"),
            port=465,
            username=message["From"],
            password=os.getenv("SMTP_PWD", ""),
            use_tls=True,
        )
    except Exception as e:
        print(f"Error sending OTP email: {e}")
        raise HTTPException(status_code=500) from e

    return True


@router.post("/send_otp")
async def send_otp(_request: Request, otp_request: OtpClientRequest):
    """Send OTP to the user's email"""
    await send_otp_code(to_email=otp_request.email)
    return Response(status_code=204)


@router.post("/validate_otp")
async def validate_otp(
    otp_client_response: OtpClientResponse,
    session: AsyncSession = Depends(get_db),
):
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
            except Exception:  # type: ignore # pylint: disable=broad-exception-caught
                return Response(status_code=500)
        else:
            # new user flow
            user = User(email=otp_client_response.email)
            try:
                session.add(user)
                await session.commit()
                await session.refresh(user)
            except IntegrityError as e:
                await session.rollback()
                raise HTTPException(
                    status_code=409,
                    detail="User already exists",
                ) from e
            except Exception:  # type: ignore # pylint: disable=broad-exception-caught
                return Response(status_code=500)

    json_response = JSONResponse({"success": True}, status_code=200)
    json_response.set_cookie(
        key="sessionId", value=ret_jwt, httponly=True, secure=True, max_age=604800
    )
    return json_response


async def generate_session_id(email: str) -> str:
    """Generate a JWT session ID for the given email"""
    token = jwt.encode(
        {"sub": email, "iat": int(datetime.now(timezone.utc).timestamp())},
        os.getenv("JWT_SECRET", ""),
        algorithm="HS256",
    )
    return token
