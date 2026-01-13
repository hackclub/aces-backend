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
import httpx

from db import get_db
from lib.ratelimiting import limiter
from lib.responses import SimpleResponse
from models.main import User

logger = logging.getLogger(__name__)

TOKEN_EXPIRY_SECONDS = 604800  # 7 days

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
r = redis.from_url(
    REDIS_URL,
    password=os.getenv("REDIS_PASSWORD", ""),
    decode_responses=True,
)

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
    referral_code: Optional[str] = None

    @field_validator("otp")
    @classmethod
    def validate_otp(cls, v: int):
        """Validate that OTP is a 6-digit number"""
        if not 100000 <= v <= 999999:
            raise ValueError("OTP must be a 6-digit number")
        return v

    @field_validator("referral_code")
    @classmethod
    def validate_referral_code(cls, v: Optional[str]):
        """Validate if referral code is alphanumeric and up to 64 chars"""
        if v is None:
            return v
        if not v.isalnum() or len(v) > 64:
            raise ValueError("Referral code must be alphanumeric and at most 64 chars")

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
        if os.getenv("JWT_SECRET") is None:
            raise HTTPException(status_code=500)
        decoded_jwt = jwt.decode(
            curr_session_id, os.getenv("JWT_SECRET", ""), algorithms=["HS256"]
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
        key="sessionId",
        value=ret_jwt,
        httponly=True,
        secure=os.getenv("ENVIRONMENT", "").lower() == "production",
        samesite="lax",
        max_age=604800,
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


@router.get("/oauth")
@limiter.limit("3/minute")  # type: ignore
async def redirect_to_oauth(
    request: Request,  # pylint: disable=W0613
    response: Response,  # pylint: disable=W0613
) -> RedirectResponse:
    client_id = os.getenv("HCA_CLIENT_ID", None)
    if client_id is None:
        raise HTTPException(status_code=500, detail="Client ID not set")
    redirect_uri = (
        os.getenv("HCA_REDIRECT_URI", None)
        or "http://localhost:8000/api/v1/auth/callback"
    )
    scopes = os.getenv("SCOPES", "email")
    return RedirectResponse(
        f"https://auth.hackclub.com/oauth/authorize?client_id={client_id}&redirect_uri={redirect_uri}&response_type=code&scope={scopes}"
    )


@router.get("/callback")
@limiter.limit("3/minute")  # type: ignore
async def redirect_to_profile(
    request: Request,  # pylint: disable=W0613
    response: Response,  # pylint: disable=W0613
    code: Optional[str] = None,
    session: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    if code is None:
        raise HTTPException(status_code=400, detail="No authorization code provided")

    if os.getenv("HCA_CLIENT_ID") is None or os.getenv("HCA_CLIENT_SECRET") is None:
        raise HTTPException(
            status_code=500, detail="Client ID or Client Secret not set"
        )

    post_data: dict[str, str] = {
        "client_id": os.getenv("HCA_CLIENT_ID") or "",
        "client_secret": os.getenv("HCA_CLIENT_SECRET") or "",
        "redirect_uri": os.getenv("HCA_REDIRECT_URI", None)
        or "http://localhost:8000/api/v1/auth/callback",
        "code": code,
        "grant_type": "authorization_code",
    }

    async with httpx.AsyncClient() as client:
        hca_request = await client.post(
            "https://auth.hackclub.com/oauth/token", data=post_data
        )

        try:
            hca_request.raise_for_status()
        except httpx.HTTPStatusError:
            raise HTTPException(
                status_code=500, detail="Encountered error getting token"
            )

        hca_response = hca_request.json()

        access_token = hca_response.get("access_token")
        if access_token is None:
            raise HTTPException(
                status_code=500, detail="Did not receive an access token"
            )

        hca_info_request = await client.get(
            "https://auth.hackclub.com/api/v1/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        try:
            hca_info_request.raise_for_status()
        except httpx.HTTPStatusError:
            raise HTTPException(
                status_code=500, detail="Encountered error getting user info"
            )

        hca_info = hca_info_request.json().get("identity")

        if hca_info is None:
            raise HTTPException(
                status_code=500, detail="Received unexpected response from HCA"
            )
        email = hca_info.get("primary_email")

        result = await session.execute(
            sqlalchemy.select(User).where(User.email == email)
        )
        existing_user = result.scalar_one_or_none()

        if existing_user is None:
            new_user = User()
            new_user.email = hca_info.get("primary_email")
            new_user.slack_id = hca_info.get("slack_id")
            new_user.idv_status = hca_info.get("verification_status")
            new_user.ysws_eligible = hca_info.get("ysws_eligible")
            referral_code = request.cookies.get("referralCode", "")
            if referral_code and referral_code.isalnum() and len(referral_code) <= 100:
                new_user.referral_code_used = (
                    referral_code  # only set the referral code if its valid
                )

            hackatime_request = await client.get(
                f"https://hackatime.hackclub.com/api/v1/users/{new_user.slack_id}/stats"
            )

            try:
                hackatime_request.raise_for_status()
            except httpx.HTTPStatusError:
                raise HTTPException(status_code=500, detail="Could not reach Hackatime")

            hackatime_response = hackatime_request.json()

            data = hackatime_response.get("data")
            if not isinstance(data, dict):
                logger.error(
                    "Unexpected Hackatime response format: %s", hackatime_response
                )
                raise HTTPException(
                    status_code=502, detail="Received invalid data from Hackatime"
                )

            user_id = data.get("user_id")  # type: ignore
            if user_id is None:
                logger.error(
                    "Hackatime response missing 'user_id' field: %s",
                    hackatime_response,
                )
                raise HTTPException(
                    status_code=502,
                    detail="Received incomplete data from Hackatime service",
                )

            try:
                new_user.hackatime_id = int(user_id)
            except (TypeError, ValueError) as exc:
                logger.error(
                    "Invalid 'user_id' value in Hackatime response: %s",
                    hackatime_response,
                )
                raise HTTPException(
                    status_code=502,
                    detail="Received invalid user ID from Hackatime service",
                ) from exc

            try:
                session.add(new_user)
                await session.commit()
                await session.refresh(new_user)
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

        ret_jwt = await generate_session_id(email)
        redirect_response = RedirectResponse(
            url=os.getenv(
                "HCA_FINAL_URI", "https://aces.hackclub.com/dashboard/profile"
            )
        )
        redirect_response.set_cookie(
            key="sessionId",
            value=ret_jwt,
            httponly=True,
            secure=os.getenv("ENVIRONMENT", "").lower() == "production",
            max_age=604800,
            samesite="lax",
        )
        return redirect_response


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
