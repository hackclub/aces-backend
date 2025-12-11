"""Devlog API routes"""

from datetime import datetime
from enum import Enum
from logging import error
from typing import Optional
import os

import sqlalchemy
from fastapi import APIRouter, Depends, HTTPException, Request, Header
from pydantic import BaseModel, ConfigDict, Field, HttpUrl
from sqlalchemy.ext.asyncio import AsyncSession

from api.v1.auth import require_auth
from db import get_db
from lib.ratelimiting import limiter
from models.user import Devlog, User, UserProject

router = APIRouter()
CDN_HOST = "hc-cdn.hel1.your-objectstorage.com"
CARDS_PER_HOUR = 8


class DevlogState(Enum):
    """Devlog states"""

    PUBLISHED = 0
    ACCEPTED = 1
    REJECTED = 2
    OTHER = 3


class CreateDevlogRequest(BaseModel):
    """Devlog creation request from client"""

    project_id: int
    content: str = Field(min_length=1, max_length=1000)
    media_url: HttpUrl


class DevlogResponse(BaseModel):
    """Public representation of a devlog"""

    id: int
    user_id: int
    project_id: int
    content: str
    media_url: str
    created_at: datetime
    updated_at: Optional[datetime]
    hours_snapshot: float
    cards_awarded: int
    state: int
    model_config = ConfigDict(from_attributes=True)


class ReviewRequest(BaseModel):
    """Review decisions from airtable"""

    devlog_id: int
    status: int  # int cuz it goes off DevlogState


@router.get("/")
@require_auth
async def get_devlogs(
    request: Request,  # pylint: disable=unused-argument
    session: AsyncSession = Depends(get_db),
    devlog_id: Optional[int] = None,
    user_id: Optional[int] = None,
):
    """Get devlogs by id or user_id"""
    if devlog_id is not None:
        result = await session.execute(
            sqlalchemy.select(Devlog).where(Devlog.id == devlog_id)
        )
        devlog = result.scalar_one_or_none()
        if devlog is None:
            raise HTTPException(status_code=404, detail="Devlog not found")
        return DevlogResponse.model_validate(devlog)

    if user_id is not None:
        result = await session.execute(
            sqlalchemy.select(Devlog)
            .where(Devlog.user_id == user_id)
            .order_by(Devlog.created_at.desc())
        )
        devlogs = result.scalars().all()
        return [DevlogResponse.model_validate(d) for d in devlogs]

    raise HTTPException(
        status_code=400, detail="Must provide either devlog_id or user_id"
    )


@router.post("/")
@limiter.limit("10/minute")  # type: ignore
@require_auth
async def create_devlog(
    request: Request,
    devlog_request: CreateDevlogRequest,
    session: AsyncSession = Depends(get_db),
):
    """Create a new devlog"""
    user_email = request.state.user["sub"]

    # check media is on CDN
    if (
        devlog_request.media_url.host != CDN_HOST
        or devlog_request.media_url.scheme != "https"
    ):
        raise HTTPException(
            status_code=400, detail="Media must be hosted on the Hack Club CDN"
        )

    # get the project (and verify it belongs to user)
    result = await session.execute(
        sqlalchemy.select(UserProject).where(
            UserProject.id == devlog_request.project_id,
            UserProject.user_email == user_email,
        )
    )
    project = result.scalar_one_or_none()

    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    # check if project is shipped
    if project.shipped:
        raise HTTPException(
            status_code=400, detail="Cannot create devlog for shipped project"
        )

    # get user to update cards balance
    user_result = await session.execute(
        sqlalchemy.select(User).where(User.email == user_email)
    )
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    new_devlog = Devlog(
        user_id=user.id,
        project_id=project.id,
        content=devlog_request.content,
        media_url=str(devlog_request.media_url),
        hours_snapshot=project.hackatime_total_hours,
        cards_awarded=0,
        state=DevlogState.PUBLISHED.value,
    )

    try:
        session.add(new_devlog)
        await session.commit()
        await session.refresh(new_devlog)
        return DevlogResponse.model_validate(new_devlog)
    except Exception as e:
        await session.rollback()
        error("Error creating devlog:", exc_info=e)
        raise HTTPException(status_code=500, detail="Error creating devlog") from e


@router.post("/review")
@limiter.limit("10/minute")  # type: ignore
async def review_devlog(
    request: Request,
    review: ReviewRequest,
    session: AsyncSession = Depends(get_db),
    x_airtable_secret: str = Header(),
):
    """Handle reviews from airtable"""

    airtable_secret = os.getenv("AIRTABLE_REVIEW_KEY")
    if x_airtable_secret != airtable_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # grab the user's row
    result = await session.execute(
        sqlalchemy.select(Devlog).where(Devlog.id == review.devlog_id)
    )
    devlog = result.scalar_one_or_none()
    if devlog is None:
        raise HTTPException(status_code=404, detail="Devlog not found")

    if review.status == DevlogState.ACCEPTED.value:
        devlog.state = DevlogState.ACCEPTED.value

        # calc the cards to award
        cards = int(devlog.hours_snapshot * CARDS_PER_HOUR)
        devlog.cards_awarded = cards

        # add the awarded cards to the user's balance
        user_result = await session.execute(
            sqlalchemy.select(User).where(User.id == devlog.user_id)
        )
        user = user_result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User associated with devlog not found")
            
        user.cards_balance += cards

    elif review.status == DevlogState.REJECTED.value:
        devlog.state = DevlogState.REJECTED.value
    elif review.status == DevlogState.OTHER.value:
        devlog.state = DevlogState.OTHER.value
    else:
        raise HTTPException(status_code=400, detail="Invalid status code for devlog")

    try:
        await session.commit()
    except Exception as e:
        error("Error committing review decision:", exc_info=e)
        await session.rollback()
        raise HTTPException(status_code=500, detail="Error saving review decision") from e
    return {"success": True}
