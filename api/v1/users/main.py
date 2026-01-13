"""Users API routes"""

# import asyncio

# import asyncpg
# import orjson
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from enum import Enum
from json import JSONDecodeError
from typing import Any, Optional

import httpx
import redis.asyncio as redis
import sqlalchemy
from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
import re

from api.v1.auth import require_auth
from db import get_db
from lib.hackatime import get_account, get_projects
from lib.ratelimiting import limiter
from lib.responses import SimpleResponse
from models.main import User

logger = logging.getLogger(__name__)
USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_]+$")


@asynccontextmanager
async def lifespan(_app: Any):
    """Redis connection lifespan manager"""
    global r  # pylint: disable=W0601
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    r = redis.from_url(
        redis_url,
        password=os.getenv("REDIS_PASSWORD", ""),
        decode_responses=True,
    )
    yield
    await r.close()


router = APIRouter(lifespan=lifespan)


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
            case IDVStatusResponse.VERIFIED:
                return IDVStatus.ELIGIBLE
            case IDVStatusResponse.VERIFIED_BUT_OVER_18 | IDVStatusResponse.INELIGIBLE:
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
    cards: int


class UpdateUserRequest(BaseModel):
    """Update user request from client"""

    username: str


class DeleteUserResponse(BaseModel):
    """Delete user response to client"""

    deletion_date: datetime


# there'll be a second endpoint for admins to update
# @protect
@router.patch("/me")
@limiter.limit("10/hour")  # type: ignore
@require_auth
async def update_user(
    request: Request,
    update_request: UpdateUserRequest,
    session: AsyncSession = Depends(get_db),
) -> SimpleResponse:
    """Update user details"""

    user_email = request.state.user["sub"]

    user_raw = await session.execute(
        sqlalchemy.select(User).where(User.email == user_email)
    )

    user = user_raw.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=404)  # user doesn't exist

    new_username = update_request.username.strip()

    if not new_username or not (3 <= len(new_username) <= 32):
        raise HTTPException(
            status_code=400,
            detail="Username must be between 3 and 32 characters in length.",
        )

    if not USERNAME_PATTERN.match(new_username):
        raise HTTPException(
            status_code=400,
            detail="Username must contain only letters, numbers, and underscores",
        )

    try:
        new_user_raw = await session.execute(
            sqlalchemy.select(User).where(
                sqlalchemy.func.lower(User.username) == new_username.lower()
            )
        )

        new_user = new_user_raw.scalar_one_or_none()
        if new_user is not None and new_user.id != user.id:
            raise HTTPException(status_code=409, detail="Username already in use")

        user.username = new_username.lower()

        await session.commit()
        await session.refresh(user)

    except HTTPException:
        raise
    except Exception as e:  # type: ignore # pylint: disable=broad-exception-caught
        logger.exception("Failed to update username: %s", e)
        raise HTTPException(status_code=500, detail="Could not update username") from e

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
        cards=user.cards_balance,
    )


# @protect
@router.delete("/me")
@limiter.limit("10/hour")  # type: ignore
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
        logger.exception("Failed to mark user for deletion")
        raise HTTPException(
            status_code=500, detail="Failed to mark user for deletion"
        ) from e

    return DeleteUserResponse(deletion_date=date_for_deletion)


@router.post("/recalculate_time")
@limiter.limit("10/minute")  # type: ignore
@require_auth
async def recalculate_hackatime_time(
    request: Request,
    response: Response,  # pylint: disable=unused-argument
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
        logger.exception("Error fetching Hackatime projects")
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
        logger.exception("Error updating Hackatime data")
        raise HTTPException(
            status_code=500, detail="Error updating Hackatime data"
        ) from e


@router.post("/retry_hackatime_link")
@limiter.limit("20/minute")  # type: ignore
@require_auth
async def retry_hackatime_link(
    request: Request,
    response: Response,  # pylint: disable=unused-argument
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
        if not user.hackatime_id:
            raise HTTPException(
                status_code=400, detail="User does not have a linked Hackatime ID"
            )
        hackatime_data = await get_account(user.hackatime_id)
    except Exception as e:  # type: ignore # pylint: disable=broad-exception-caught
        logger.exception("Error fetching Hackatime account data")
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
        logger.exception("Error linking Hackatime account")
        raise HTTPException(
            status_code=500, detail="Error linking Hackatime account"
        ) from e


async def check_idv_status(
    user: User,
) -> IDVStatus:
    """Gets the IDV status for a user based on the email stored in their user info

    Args:
        user (User)

    Returns:
        IDVStatus (enum)
    """
    redis_response: Any | None = await r.get(f"idv-{user.id}")
    if redis_response is not None:
        if not isinstance(redis_response, str):
            logger.warning(
                "Unexpected Redis response type for IDV status, user_id=%d",
                user.id,
            )
            return IDVStatus.ERROR

        return IDVStatus(redis_response)

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://auth.hackclub.com/api/external/check",
                params={"email": user.email},
                timeout=10,
            )
            match response.status_code:
                case 200:
                    pass
                case 404:
                    logger.warning("HCA returned 404 for user_id=%d", user.id)
                    return IDVStatus.ERROR
                case 422:
                    logger.warning("HCA returned 422 for user_id=%d", user.id)
                    return IDVStatus.ERROR
                case _:
                    logger.error(
                        "HCA unexpected status=%d for user_id=%d",
                        response.status_code,
                        user.id,
                    )
                    return IDVStatus.ERROR
            data: dict[str, str] = response.json()
            if data.get("result") is None or data["result"] == "":
                logger.warning("HCA returned empty result for user_id=%d", user.id)
                return IDVStatus.ERROR
            idv_status = IDVStatusResponse(data["result"]).as_idv_status()
            await r.setex(f"idv-{user.id}", 900, idv_status.value)
            return idv_status
    except httpx.TimeoutException:
        logger.exception("HCA timeout for user_id=%d", user.id)
    except JSONDecodeError:
        logger.exception("HCA JSON decode error for user_id=%d", user.id)
    except Exception:  # type: ignore # pylint: disable=broad-exception-caught
        logger.exception("HCA error for user_id=%d", user.id)
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
