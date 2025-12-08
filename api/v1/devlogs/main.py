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
from models.user import UserProject, Devlog

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
    user_email: str
    project_id: int
    content: str
    media_url: str
    created_at: datetime
    updated_at: Optional[datetime]
    hours_snapshot: float

    model_config = ConfigDict(from_attributes=True)

@router.get("/user/{user_email}")
async def get_devlogs_by_user(
    user_email: str,
    session: AsyncSession = Depends(get_db),
):
    """Get all devlogs by a user"""
    result = await session.execute(
        sqlalchemy.select(Devlog)
        .where(Devlog.user_email == user_email)
        .order_by(Devlog.created_at.desc())
    )
    devlogs = result.scalars().all()
    return [DevlogResponse.model_validate(d) for d in devlogs]

@router.get("/{devlog_id}")
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
        return Response(status_code = 404)
    
    return DevlogResponse.model_validate(devlog)

@router.post("/create")
@require_auth
async def create_devlog(
    request: Request,
    devlog_request: CreateDevlogRequest,
    session: AsyncSession = Depends(get_db),
):
    """Create a new devlog"""
    user_email = request.state.user["sub"]

    #check media is on CDN
    if devlog_request.media_url.host != CDN_HOST:
        raise HTTPException(status_code=400, detail="Media must be hosted on the Hack Club CDN")

    #get the project (and verify it belongs to user)
    result = await session.execute(
        sqlalchemy.select(UserProject).where(
            UserProject.id == devlog_request.project_id,
            UserProject.user_email == user_email,
        )
    )
    project = result.scalar_one_or_none()

    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    new_devlog = Devlog(
        user_email=user_email,
        project_id=project.id,
        content=devlog_request.content,
        media_url=str(devlog_request.media_url),
        hours_snapshot=project.hackatime_total_hours,
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

@router.post("/{devlog_id}/edit")
@require_auth
async def update_devlog(
    request: Request,
    devlog_id: int,
    devlog_request: UpdateDevlogRequest,
    session: AsyncSession = Depends(get_db),
):
    """update a devlog (only your own)"""
    user_email = request.state.user["sub"]

    result = await session.execute(
        sqlalchemy.select(Devlog).where(Devlog.id == devlog_id)
    )
    devlog = result.scalar_one_or_none()

    if devlog is None:
        raise HTTPException(status_code=404, detail="Devlog not found")

    if devlog.user_email != user_email:
        raise HTTPException(status_code=403, detail="Not your devlog")

    if devlog_request.content is not None:
        devlog.content = devlog_request.content

    if devlog_request.media_url is not None:
        if devlog_request.media_url.host != CDN_HOST:
            raise HTTPException(status_code=400, detail="Media must be hosted on the Hack Club CDN")
        devlog.media_url = str(devlog_request.media_url)

    try:
        await session.commit()
        await session.refresh(devlog)
        return DevlogResponse.model_validate(devlog)
    except Exception as e:
        await session.rollback()
        error("Error updating devlog:", exc_info=e)
        raise HTTPException(status_code=500, detail="Error updating devlog") from e