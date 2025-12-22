"""Users API routes"""

# import asyncio

# import asyncpg
# import orjson
import json
import os
import traceback
from datetime import datetime, timedelta, timezone
from enum import Enum
from logging import error, warning
from typing import Any, Optional

import dotenv
import httpx
import redis.asyncio as redis
import sqlalchemy
import validators
from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.v1.auth.main import require_auth, send_otp_code  # type: ignore
from db import get_db
from lib.hackatime import get_account, get_projects
from lib.ratelimiting import limiter
from lib.responses import SimpleResponse
from models.main import User

router = APIRouter()

dotenv.load_dotenv()

HOST = "redis" if os.getenv("USING_DOCKER") == "true" else "localhost"
r = redis.Redis(password=os.getenv("REDIS_PASSWORD", ""), host=HOST)


class IDVStatusResponse(Enum):
    """IDV status responses from the API"""

    VERIFIED = "verified"
    VERIFIED_BUT_OVER_18 = "verified_but_over_18"
    PENDING = "pending"
    NEEDS_SUBMISSION = "needs_submission"
    INELIGIBLE = "ineligible"
    ERROR = "error"

    @classmethod
    def _missing_(cls, value: Any):
        return cls.ERROR

    def as_idv_status(self) -> "IDVStatus":
        """Convert to IDVStatus enum"""
        match self:
            case IDVStatusResponse.VERIFIED | IDVStatusResponse.VERIFIED_BUT_OVER_18:
                return IDVStatus.ELIGIBLE
            case IDVStatusResponse.INELIGIBLE:
                return IDVStatus.INELIGIBLE
            case IDVStatusResponse.PENDING | IDVStatusResponse.NEEDS_SUBMISSION:
                return IDVStatus.UNVERIFIED
            case _:
                return IDVStatus.ERROR


class IDVStatus(Enum):
    """Parsed IDV status responses"""

    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    UNVERIFIED = "unverified"
    ERROR = "error"

    @classmethod
    def _missing_(cls, value: Any):
        return cls.ERROR


class UserResponse(BaseModel):
    """Public representation of a user"""

    id: int
    email: str
    username: Optional[str] = None
    hackatime_id: Optional[int] = None
    permissions: list[int]
    marked_for_deletion: bool


class UpdateUserRequest(BaseModel):
    """Update user request from client"""

    email: str


class DeleteUserResponse(BaseModel):
    """Delete user response to client"""

    deletion_date: datetime


# there'll be a second endpoint for admins to update
# @protect
@router.patch("/me")
@require_auth
async def update_user(
    request: Request,
    update_request: UpdateUserRequest,
    session: AsyncSession = Depends(get_db),
) -> SimpleResponse:
    """Update user details"""

    user_email = request.state.user["sub"]

    if validators.email(update_request.email) is False:
        raise HTTPException(status_code=400, detail="Invalid email format")

    user_raw = await session.execute(
        sqlalchemy.select(User).where(User.email == user_email)
    )

    user = user_raw.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=404)  # user doesn't exist

    try:
        new_user_raw = await session.execute(
            sqlalchemy.select(User).where(User.email == update_request.email)
        )

        new_user = new_user_raw.scalar_one_or_none()
        if new_user is not None and new_user.id != user.id:
            raise HTTPException(status_code=409, detail="Email already in use")

        await send_otp_code(to_email=update_request.email, old_email=user_email)
    except HTTPException:
        raise
    except Exception as e:  # type: ignore # pylint: disable=broad-exception-caught
        error("Failed to send verification code:", exc_info=e)
        raise HTTPException(
            status_code=500, detail="Failed to send verification code"
        ) from e

    return SimpleResponse(success=True)


# @protect
@router.get("/me")
@require_auth
async def get_user(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Get user details"""

    user_email = request.state.user["sub"]

    user_raw = await session.execute(
        sqlalchemy.select(User).where(User.email == user_email)
    )

    user = user_raw.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=404)  # user doesn't exist

    return UserResponse(
        id=user.id,
        email=user.email,
        permissions=user.permissions,
        username=user.username,
        hackatime_id=user.hackatime_id,
        marked_for_deletion=user.marked_for_deletion,
    )


# @protect
@router.delete("/me")
@require_auth
async def delete_user(
    request: Request,
    # response: Response,
    session: AsyncSession = Depends(get_db),
) -> DeleteUserResponse:
    """Delete a user account"""
    # can only delete their own user!!! don't let them delete other users!!!
    # TODO: implement delete user functionality

    user_email = request.state.user["sub"]

    user_raw = await session.execute(
        sqlalchemy.select(User).where(User.email == user_email)
    )

    user = user_raw.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=404)  # user doesn't exist

    user.marked_for_deletion = True
    user.date_for_deletion = datetime.now(timezone.utc) + timedelta(days=30)

    date_for_deletion = user.date_for_deletion
    if not date_for_deletion:  # how does this even happen
        raise HTTPException(status_code=500, detail="Failed to set deletion date")

    try:
        await session.commit()
        await session.refresh(user)
    except Exception as e:  # type: ignore # pylint: disable=broad-exception-caught
        await session.rollback()
        error("Failed to mark user for deletion:", exc_info=e)
        raise HTTPException(
            status_code=500, detail="Failed to mark user for deletion"
        ) from e

    return DeleteUserResponse(deletion_date=date_for_deletion)


@router.post("/recalculate_time")
@limiter.limit("10/minute")  # type: ignore
@require_auth
async def recalculate_hackatime_time(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> SimpleResponse:
    """Recalculate Hackatime time for a user"""
    user_email = request.state.user["sub"]

    user_raw = await session.execute(
        sqlalchemy.select(User)
        .options(selectinload(User.projects))
        .where(User.email == user_email)
    )

    user = user_raw.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=404, detail="User not found"
        )  # user doesn't exist

    if not user.hackatime_id:
        raise HTTPException(
            status_code=400, detail="User does not have a linked Hackatime ID"
        )

    if not user.projects:
        raise HTTPException(status_code=400, detail="User has no linked projects")

    if datetime.now(timezone.utc) - user.hackatime_last_fetched < timedelta(minutes=5):
        raise HTTPException(
            status_code=429, detail="Please wait before trying to recalculate again."
        )

    all_hackatime_projects: "set[str]" = set()
    for project in user.projects:
        if project.hackatime_projects:
            all_hackatime_projects.update(project.hackatime_projects)

    try:
        user_projects = await get_projects(
            user.hackatime_id, list(all_hackatime_projects)
        )
    except Exception as e:  # type: ignore # pylint: disable=broad-exception-caught
        error("Error fetching Hackatime projects:", exc_info=e)
        raise HTTPException(
            status_code=500, detail="Error fetching Hackatime projects"
        ) from e

    for project in user.projects:
        # find matching project from hackatime data
        hackatime_projects = project.hackatime_projects
        projects = [
            (name, seconds)
            for name, seconds in user_projects.items()
            if name in hackatime_projects
        ]

        total_seconds = sum(float(seconds or 0) for _, seconds in projects)
        project.hackatime_total_hours = total_seconds / 3600.0

    user.hackatime_last_fetched = datetime.now(timezone.utc)

    try:
        await session.commit()
        await session.refresh(user)
        return SimpleResponse(success=True)
    except Exception as e:  # type: ignore # pylint: disable=broad-exception-caught
        await session.rollback()
        error("Error updating Hackatime data:", exc_info=e)
        raise HTTPException(
            status_code=500, detail="Error updating Hackatime data"
        ) from e


@router.post("/retry_hackatime_link")
@limiter.limit("20/minute")  # type: ignore
@require_auth
async def retry_hackatime_link(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> SimpleResponse:
    """Retry linking Hackatime account for a user"""
    user_email = request.state.user["sub"]

    user_raw = await session.execute(
        sqlalchemy.select(User).where(User.email == user_email)
    )

    user = user_raw.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=404, detail="User not found"
        )  # user doesn't exist

    if user.hackatime_id:
        raise HTTPException(
            status_code=400, detail="User already has a linked Hackatime ID"
        )

    hackatime_data = None
    try:
        hackatime_data = await get_account(user_email)
    except Exception as e:  # type: ignore # pylint: disable=broad-exception-caught
        error("Error fetching Hackatime account data:", exc_info=e)
        raise HTTPException(
            status_code=500, detail="Error fetching Hackatime account data"
        ) from e

    if not hackatime_data:
        raise HTTPException(status_code=404, detail="Hackatime account not found")

    user.hackatime_id = hackatime_data.id
    user.username = hackatime_data.username

    try:
        await session.commit()
        await session.refresh(user)
        return SimpleResponse(success=True)
    except Exception as e:  # type: ignore # pylint: disable=broad-exception-caught
        await session.rollback()
        error("Error linking Hackatime account:", exc_info=e)
        raise HTTPException(
            status_code=500, detail="Error linking Hackatime account"
        ) from e


async def check_idv_verification(
    user: User,
) -> IDVStatus:
    """Checks whether a user is IDV eligible based on the email stored in their user info."""
    redis_response: str | None = await r.get(f"{user.id}-idv-status")
    if redis_response is not None:
        return IDVStatus(redis_response)

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://auth.hackclub.com/api/external/check",
                params={"email": user.email},
                timeout=10,
            )
            data: dict[str, str] = response.json()
            match response.status_code:
                case 200:
                    pass
                case 404:
                    warning(
                        f"HCA returned a 404 when looking up the status for {user.email}"
                    )
                    return IDVStatus.ERROR
                case 422:
                    warning(
                        "HCA returned a 422 (invalid params) when looking up the"
                        f"status for {user.email}"
                    )
                    return IDVStatus.ERROR
                case _:
                    error(
                        "Received unexpected status code when checking auth "
                        f"status! Got: {response.status_code}"
                    )
                    return IDVStatus.ERROR
            if data.get("result") is None:
                warning(
                    f"Uncaught error from HCA, key result is empty! Raw HCA response: {data}"
                )
                return IDVStatus.ERROR
            idv_status = IDVStatusResponse(data["result"]).as_idv_status()
            await r.setex(f"{user.id}-idv-status", 900, idv_status.value)
            return idv_status
    except httpx.TimeoutException:
        error("Timeout while querying Hack Club Auth endpoint")
        traceback.print_exc()
    except json.JSONDecodeError:
        error("Error decoding JSON from Hack Club Auth API call!")
        traceback.print_exc()
    except Exception:  # type: ignore # pylint: disable=broad-exception-caught
        error("Other exception caught when querying Hack Club Auth endpoint!")
        traceback.print_exc()
    return IDVStatus.ERROR


# disabled for 30 days, no login -> delete
# @protect
async def is_pending_deletion():
    """Check if a user account is pending deletion"""
    # TODO: implement is pending deletion functionality
    # TODO: figure out how we want to decide if they're able to get deletion status


# async def run():
#     conn = await asyncpg.connect(user='user', password='password',
#                                  database='database', host='127.0.0.1')
#     values = await conn.fetch(
#         'SELECT * FROM mytable WHERE id = $1',
#         10,
#     )
#     await conn.close()

# asyncio.run(run())

# def foo():
#     return "abc"
