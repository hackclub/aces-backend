"""Projects API routes"""

# import asyncio
import datetime

# import asyncpg
# import orjson
import sqlalchemy
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.auth import require_auth # type: ignore
from db import get_db  # , engine
from models.user import User, UserProject


class CreateProjectRequest(BaseModel):
    """Create project request from client"""

    project_name: str

class UpdateProjectRequest(BaseModel):
    """Update project request from client"""
    
    project_id: int
    project_name: Optional[str] = None
    hackatime_projects: Optional[List[str]] = None

    class Config:
        extra = "forbid"  

router = APIRouter()

# @protect
# async def create_project(): ...


# @protect
@router.post("/api/projects/update")
@require_auth
async def update_project(
    request: Request,
    project_request: UpdateProjectRequest,
    session: AsyncSession = Depends(get_db)
):
    """Update project details"""

    user_email = request.state.user["sub"]

    project_raw = await session.execute(
        sqlalchemy.select(UserProject).where(
            UserProject.id == project_request.project_id,
            UserProject.user_email == user_email
        )
        
    )

    project = project_raw.scalar_one_or_none()

    if project is None:
        raise HTTPException(
            status_code=404
        ) # if you get this good on you...?
    
    update_data = project_request.model_dump(exclude_unset=True, exclude={"project_id"})

    ALLOWED_UPDATE_FIELDS = {"project_name", "hackatime_projects"}
    for field, value in update_data.items():
        if field in ALLOWED_UPDATE_FIELDS:
            setattr(project, field, value)

    await session.commit()
    await session.refresh(project)

    return {"success": True}

@router.get("/api/projects")
@require_auth
async def return_projects_for_user(
    request: Request, session: AsyncSession = Depends(get_db)
):
    """Return all projects for the authenticated user"""
    user_email = request.state.user["sub"]
    user_raw = await session.execute(
        sqlalchemy.select(User)
        .options(selectinload(User.projects))
        .where(User.email == user_email)
    )
    user = user_raw.scalar_one_or_none()
    projects = (
        user.projects if user else []
    )  # this should never invoke the else unless something has gone very bad
    projects_ret = [project.__dict__ for project in projects]
    return projects_ret


@router.post("/api/projects/create")
@require_auth
async def create_project(
    request: Request,
    project_create_request: CreateProjectRequest,
    session: AsyncSession = Depends(get_db),
):
    """Create a new project for the authenticated user"""
    user_email = request.state.user["sub"]
    user_raw = await session.execute(
        sqlalchemy.select(User).where(User.email == user_email)
    )
    user = user_raw.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=401
        )  # if the user hasn't been created yet they shouldn't be authed

    new_project = UserProject(
        name=project_create_request.project_name,
        user_email=user_email,
        hackatime_projects=[],
        hackatime_total_hours=0.0,
        # last_updated=datetime.datetime.now(datetime.timezone.utc), this should no longer need manual setting
    )

    session.add(new_project)
    await session.commit()
    await session.refresh(new_project)

    return {"success": True}
