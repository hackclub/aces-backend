"""Devlog API routes"""

import asyncio
import os
from datetime import datetime
from enum import Enum
from logging import error
from typing import Optional

import sqlalchemy
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pyairtable import Api
from pydantic import BaseModel, ConfigDict, Field, HttpUrl
from sqlalchemy.ext.asyncio import AsyncSession

from api.v1.auth import require_auth
from db import get_db
from lib.ratelimiting import limiter
from models.main import Devlog, User, UserProject

router = APIRouter()
api = Api(os.environ["AIRTABLE_API_KEY"])
review_table = api.table(
    os.environ["AIRTABLE_BASE_ID"], os.environ["AIRTABLE_REVIEW_TABLE_ID"]
)
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
    state: DevlogState
    model_config = ConfigDict(from_attributes=True)


class DevlogsResponse(BaseModel):
    """Response containing multiple devlogs"""

    devlogs: list[DevlogResponse]


class ReviewRequest(BaseModel):
    """Review decisions from airtable"""

    devlog_id: int
    status: DevlogState


class ReviewResponse(BaseModel):
    """Response for review endpoint"""

    success: bool
    message: str = ""


@router.get("/")
@require_auth
async def get_devlogs(
    request: Request,  # pylint: disable=unused-argument
    session: AsyncSession = Depends(get_db),
    devlog_id: Optional[int] = None,
    user_id: Optional[int] = None,
) -> DevlogsResponse:
    """Get devlogs by id or user_id"""
    if devlog_id is not None:
        result = await session.execute(
            sqlalchemy.select(Devlog).where(Devlog.id == devlog_id)
        )
        devlog = result.scalar_one_or_none()
        if devlog is None:
            raise HTTPException(status_code=404, detail="Devlog not found")
        return DevlogsResponse(devlogs=[DevlogResponse.model_validate(devlog)])

    if user_id is not None:
        result = await session.execute(
            sqlalchemy.select(Devlog)
            .where(Devlog.user_id == user_id)
            .order_by(Devlog.created_at.desc())
        )
        devlogs = result.scalars().all()
        return DevlogsResponse(
            devlogs=[DevlogResponse.model_validate(d) for d in devlogs]
        )

    raise HTTPException(
        status_code=400, detail="Must provide either devlog_id or user_id"
    )


@router.post("/")
@limiter.limit("10/minute")  # type: ignore
@require_auth
async def create_devlog(
    request: Request,
    devlog_request: CreateDevlogRequest,
    response: Response,  # pylint: disable=unused-argument
    session: AsyncSession = Depends(get_db),
) -> DevlogResponse:
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
        await session.flush()  # Flush to DB to get the ID before Airtable

        try:
            await asyncio.to_thread(
                lambda: review_table.create(
                    {
                        "Devlog ID": new_devlog.id,
                        "User ID": user.id,
                        "Content": new_devlog.content,
                        "Git": project.repo,
                        "Media URL": new_devlog.media_url,
                        "Status": new_devlog.state,
                        "Hours Snapshot": new_devlog.hours_snapshot,
                        "Cards Awarded": new_devlog.cards_awarded,
                    }
                )
            )
        except Exception as e:
            await session.rollback()
            error("Error creating devlog review row in Airtable:", exc_info=e)
            raise HTTPException(
                status_code=500, detail="Error creating devlog review record"
            ) from e

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
    request: Request,  # pylint: disable=unused-argument
    review: ReviewRequest,
    session: AsyncSession = Depends(get_db),
    x_airtable_secret: str = Header(),
) -> ReviewResponse:
    """Handle reviews from airtable"""

    airtable_secret = os.getenv("AIRTABLE_REVIEW_KEY")
    if x_airtable_secret != airtable_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # grab the user's last 2 devlogs
    result = await session.execute(
        sqlalchemy.select(Devlog).where(Devlog.id == review.devlog_id)
    )
    devlog = result.scalar_one_or_none()
    if devlog is None:
        raise HTTPException(status_code=404, detail="Devlog not found")

    status_value = review.status.value

    if devlog.state == status_value:
        return ReviewResponse(success=True, message="Already processed this devlog")

    # store old state before updating
    old_state = devlog.state

    if review.status == DevlogState.ACCEPTED:
        devlog.state = status_value

        # calc the cards to award
        cards = int(devlog.hours_snapshot * CARDS_PER_HOUR)
        devlog.cards_awarded = cards

        # add the awarded cards to the user's balance
        user_result = await session.execute(
            sqlalchemy.select(User).where(User.id == devlog.user_id)
        )
        user = user_result.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=404, detail="User associated with devlog not found"
            )

        # only award cards if transitioning TO accepted (prevent double-awarding)
        if old_state != DevlogState.ACCEPTED.value:
            user.cards_balance += cards

    elif review.status == DevlogState.REJECTED:
        devlog.state = status_value
    elif review.status == DevlogState.OTHER:
        devlog.state = status_value
    else:
        raise HTTPException(status_code=400, detail="Invalid status code for devlog")

    try:
        await session.commit()
    except Exception as e:
        error("Error committing review decision:", exc_info=e)
        await session.rollback()
        raise HTTPException(
            status_code=500, detail="Error saving review decision"
        ) from e
    return ReviewResponse(success=True)
