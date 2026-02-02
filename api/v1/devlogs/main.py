"""Devlog API routes"""

import asyncio
import logging
import os
from datetime import datetime
from enum import StrEnum
from typing import Optional

import sqlalchemy
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pyairtable import Api
from pydantic import BaseModel, ConfigDict, Field, HttpUrl
from sqlalchemy.ext.asyncio import AsyncSession

from api.v1.auth import require_auth
from db import get_db
from lib.hackatime import get_projects
from lib.ratelimiting import limiter
from models.main import Devlog, User, UserProject

logger = logging.getLogger(__name__)

router = APIRouter()
api = Api(os.environ["AIRTABLE_API_KEY"])
review_table = api.table(
    os.environ["AIRTABLE_BASE_ID"], os.environ["AIRTABLE_REVIEW_TABLE_ID"]
)
CDN_HOSTS = ["hc-cdn.hel1.your-objectstorage.com", "cdn.hackclub.com"]

CARDS_PER_HOUR = 8


class DevlogState(StrEnum):
    """Devlog states"""

    PUBLISHED = "Pending"
    ACCEPTED = "Approved"
    REJECTED = "Rejected"
    OTHER = "Other"


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
        devlog_request.media_url.host not in CDN_HOSTS
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

    if not user.hackatime_id:
        raise HTTPException(
            status_code=400, detail="User does not have a linked Hackatime ID"
        )

    all_hackatime_projects: "set[str]" = set()
    if project.hackatime_projects:
        all_hackatime_projects.update(project.hackatime_projects)

    if not all_hackatime_projects:
        raise HTTPException(
            status_code=400, detail="Project has no linked Hackatime projects"
        )

    try:
        hackatime_data = await get_projects(
            user.hackatime_id, list(all_hackatime_projects)
        )
    except Exception as e:
        logger.exception("Error fetching Hackatime projects")
        raise HTTPException(
            status_code=500, detail="Error fetching Hackatime projects"
        ) from e

    total_seconds = sum(float(seconds or 0) for _, seconds in hackatime_data.items())
    current_hours = total_seconds / 3600.0
    project.hackatime_total_hours = current_hours

    last_devlog_result = await session.execute(
        sqlalchemy.select(Devlog.hours_snapshot)
        .where(Devlog.project_id == project.id)
        .order_by(Devlog.id.desc())
        .limit(1)
    )
    last_hours_snapshot = (
        last_devlog_result.scalar_one_or_none() or 0
    )  # prevent the none case

    if current_hours <= last_hours_snapshot:
        raise HTTPException(
            status_code=400,
            detail="No new hours logged since your last devlog. Log more time before submitting.",
        )

    new_devlog = Devlog(
        user_id=user.id,
        project_id=project.id,
        content=devlog_request.content,
        media_url=str(devlog_request.media_url),
        hours_snapshot=current_hours,
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
            logger.exception("Error creating devlog review row in Airtable")
            raise HTTPException(
                status_code=500, detail="Error creating devlog review record"
            ) from e

        await session.commit()
        await session.refresh(new_devlog)
        return DevlogResponse.model_validate(new_devlog)
    except Exception as e:
        await session.rollback()
        logger.exception("Error creating devlog")
        raise HTTPException(status_code=500, detail="Error creating devlog") from e


# @router.post("/review")
# @limiter.limit("10/minute")  # type: ignore
# async def review_devlog(
#     request: Request,  # pylint: disable=unused-argument
#     review: ReviewRequest,
#     response: Response,  # pylint: disable=unused-argument
#     session: AsyncSession = Depends(get_db),
#     x_airtable_secret: str = Header(),
# ) -> ReviewResponse:
#     """Handle reviews from airtable"""

#     airtable_secret = os.getenv("AIRTABLE_REVIEW_KEY")
#     if not airtable_secret:
#         raise HTTPException(status_code=500, detail="Server misconfiguration")

#     # Normalize both secrets to bytes before constant-time comparison
#     provided_secret = x_airtable_secret.encode("utf-8")
#     expected_secret = airtable_secret.encode("utf-8")

#     if not hmac.compare_digest(provided_secret, expected_secret):
#         raise HTTPException(status_code=401, detail="Unauthorized")

#     status_value = review.status.value

#     try:
#         already_processed = False

#         async with session.begin():
#             result = await session.execute(
#                 sqlalchemy.select(Devlog)
#                 .where(Devlog.id == review.devlog_id)
#                 .with_for_update()
#             )
#             devlog = result.scalar_one_or_none()
#             if devlog is None:
#                 raise HTTPException(status_code=404, detail="Devlog not found")

#             if devlog.state == status_value:
#                 already_processed = True
#             else:
#                 # store old state before updating
#                 old_state = devlog.state

#                 if review.status == DevlogState.ACCEPTED:
#                     devlog.state = status_value

#                     # only award cards if transitioning TO accepted (prevent double-awarding)
#                     if old_state != DevlogState.ACCEPTED.value:
#                         # calc the cards to award based on hours difference from last snapshot
#                         prev_result = await session.execute(
#                             sqlalchemy.select(Devlog.hours_snapshot)
#                             .where(
#                                 Devlog.project_id == devlog.project_id,
#                                 Devlog.id < devlog.id,
#                             )
#                             .order_by(Devlog.id.desc())
#                             .limit(1)
#                         )
#                         prev_hours = prev_result.scalar() or 0
#                         cards = round(
#                             (devlog.hours_snapshot - prev_hours) * CARDS_PER_HOUR
#                         )
#                         devlog.cards_awarded = cards
#                         # add the awarded cards to the user's balance
#                         user_result = await session.execute(
#                             sqlalchemy.select(User)
#                             .where(User.id == devlog.user_id)
#                             .with_for_update()
#                         )
#                         user = user_result.scalar_one_or_none()
#                         if not user:
#                             raise HTTPException(
#                                 status_code=404,
#                                 detail="User associated with devlog not found",
#                             )

#                         user.cards_balance += cards

#                 elif review.status == DevlogState.REJECTED:
#                     devlog.state = status_value
#                 elif review.status == DevlogState.OTHER:
#                     devlog.state = status_value
#                 else:
#                     raise HTTPException(
#                         status_code=400, detail="Invalid status code for devlog"
#                     )

#         if already_processed:
#             return ReviewResponse(success=True, message="Already processed this devlog")

#         return ReviewResponse(
#             success=True, message="Devlog review processed successfully"
#         )
#     except HTTPException:  # pass through HTTPExceptions
#         raise
#     except Exception as e:
#         logger.exception("Error committing review decision")
#         raise HTTPException(
#             status_code=500, detail="Error saving review decision"
#         ) from e
