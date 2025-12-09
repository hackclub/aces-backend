from datetime import datetime
from logging import error
from typing import Optional

import sqlalchemy
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, HttpUrl
from sqlalchemy.ext.asyncio import AsyncSession

from api.v1.auth import require_auth
from db import get_db
from models.user import UserProject, Devlog, User

router = APIRouter()
CDN_HOST = "hc-cdn.hel1.your-objectstorage.com"


class CreateDevlogRequest(BaseModel):
    project_id: int
    content: str
    media_url: HttpUrl


class UpdateDevlogRequest(BaseModel):
    content: Optional[str] = None
    media_url: Optional[HttpUrl] = None


class DevlogResponse(BaseModel):
    id: int
    user_id: int
    project_id: int
    content: str
    media_url: str
    created_at: datetime
    updated_at: Optional[datetime]
    hours_snapshot: float
    cards_awarded: int
    model_config = ConfigDict(from_attributes=True)


@router.get("/user/{user_id}")
@require_auth
async def get_devlogs_by_user(
    user_id: int,
    session: AsyncSession = Depends(get_db),
):
    """Get all devlogs by a user"""
    result = await session.execute(
        sqlalchemy.select(Devlog)
        .where(Devlog.user_id == user_id)
        .order_by(Devlog.created_at.desc())
    )
    devlogs = result.scalars().all()
    return [DevlogResponse.model_validate(d) for d in devlogs]


@router.get("/{devlog_id}")
@require_auth
async def get_devlog_by_id(
    devlog_id: int,
    session: AsyncSession = Depends(get_db),
):
    """Get a single devlog"""
    result = await session.execute(
        sqlalchemy.select(Devlog).where(Devlog.id == devlog_id)
    )
    devlog = result.scalar_one_or_none()

    if devlog is None:
        raise HTTPException(status_code=404, detail="Devlog not found")

    return DevlogResponse.model_validate(devlog)


@router.post("/")
@require_auth
async def create_devlog(
    request: Request,
    devlog_request: CreateDevlogRequest,
    session: AsyncSession = Depends(get_db),
):
    """Create a new devlog"""
    user_email = request.state.user["sub"]

    # check media is on CDN
    if devlog_request.media_url.host != CDN_HOST:
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

    # get user to update cards balance
    user_result = await session.execute(
        sqlalchemy.select(User).where(User.email == user_email)
    )
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    last_devlog_result = await session.execute(
        sqlalchemy.select(Devlog)
        .where(Devlog.project_id == project.id)
        .order_by(Devlog.created_at.desc())
        .limit(1)
    )
    last_devlog = last_devlog_result.scalar_one_or_none()

    if last_devlog:
        hours_worked = project.hackatime_total_hours - last_devlog.hours_snapshot
    else:
        hours_worked = project.hackatime_total_hours

    # Validate hours worked
    if hours_worked < 0:
        raise HTTPException(
            status_code=400,
            detail="Invalid hours calculation - hours cannot be negative",
        )

    if hours_worked > 168:  # 168 hours = 1 week of continuous work
        raise HTTPException(
            status_code=400, detail="Hours worked exceeds maximum allowed (168 hours)"
        )

    cards_to_award = round(hours_worked * 8)

    new_devlog = Devlog(
        user_id=user.id,
        project_id=project.id,
        content=devlog_request.content,
        media_url=str(devlog_request.media_url),
        hours_snapshot=project.hackatime_total_hours,
        cards_awarded=cards_to_award,
    )

    user.cards_balance += cards_to_award

    try:
        session.add(new_devlog)
        await session.commit()
        await session.refresh(new_devlog)
        return DevlogResponse.model_validate(new_devlog)
    except Exception as e:
        await session.rollback()
        error("Error creating devlog:", exc_info=e)
        raise HTTPException(status_code=500, detail="Error creating devlog") from e


@router.patch("/{devlog_id}")
@require_auth
async def update_devlog(
    request: Request,
    devlog_id: int,
    devlog_request: UpdateDevlogRequest,
    session: AsyncSession = Depends(get_db),
):
    """update a devlog (only your own)"""
    user_email = request.state.user["sub"]

    user_result = await session.execute(
        sqlalchemy.select(User).where(User.email == user_email)
    )
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    result = await session.execute(
        sqlalchemy.select(Devlog).where(Devlog.id == devlog_id)
    )
    devlog = result.scalar_one_or_none()

    if devlog is None:
        raise HTTPException(status_code=404, detail="Devlog not found")

    if devlog.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your devlog")

    if devlog_request.content is not None:
        devlog.content = devlog_request.content

    if devlog_request.media_url is not None:
        if devlog_request.media_url.host != CDN_HOST:
            raise HTTPException(
                status_code=400, detail="Media must be hosted on the Hack Club CDN"
            )
        devlog.media_url = str(devlog_request.media_url)

    try:
        await session.commit()
        await session.refresh(devlog)
        return DevlogResponse.model_validate(devlog)
    except Exception as e:
        await session.rollback()
        error("Error updating devlog:", exc_info=e)
        raise HTTPException(status_code=500, detail="Error updating devlog") from e
