"""Projects API routes"""

# import asyncio
# import asyncpg
# import orjson
from datetime import datetime
from typing import List, Optional

import sqlalchemy
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.auth import require_auth  # type: ignore
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
        """Pydantic config"""

        extra = "forbid"


class ProjectResponse(BaseModel):
    """Public representation of a project"""

    project_id: int
    project_name: str
    hackatime_projects: List[str]
    hackatime_total_hours: float
    last_updated: datetime

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_model(cls, project: UserProject) -> "ProjectResponse":
        """Create ProjectResponse from UserProject model instance"""
        return cls(
            project_id=project.id,
            project_name=project.name,
            hackatime_projects=list(project.hackatime_projects or []),
            hackatime_total_hours=project.hackatime_total_hours,
            last_updated=project.last_updated,
        )


router = APIRouter()

# @protect
# async def create_project(): ...


# @protect
@router.post("/api/projects/update")
@require_auth
async def update_project(
    request: Request,
    project_request: UpdateProjectRequest,
    session: AsyncSession = Depends(get_db),
):
    """Update project details"""

    user_email = request.state.user["sub"]

    project_raw = await session.execute(
        sqlalchemy.select(UserProject).where(
            UserProject.id == project_request.project_id,
            UserProject.user_email == user_email,
        )
    )

    project = project_raw.scalar_one_or_none()

    if project is None:
        raise HTTPException(status_code=404)  # if you get this good on you...?

    update_data = project_request.model_dump(exclude_unset=True, exclude={"project_id"})

    allowed_update_fields = {"project_name", "hackatime_projects"}
    for field, value in update_data.items():
        if field in allowed_update_fields:
            model_field = "name" if field == "project_name" else field
            setattr(project, model_field, value)

    try:
        await session.commit()
        await session.refresh(project)
        return JSONResponse(
            {
                "success": True,
                "project_info": ProjectResponse.from_model(project).model_dump(),
            }
        )
    except Exception:  # type: ignore # pylint: disable=broad-exception-caught
        await session.rollback()
        return Response(status_code=500)


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
    projects_ret = [ProjectResponse.from_model(project) for project in projects]
    return projects_ret


@router.get("/api/projects/{project_id}")
@require_auth
async def return_project_by_id(
    request: Request, project_id: int, session: AsyncSession = Depends(get_db)
):
    """Return a project by ID for a given user"""
    user_email = request.state.user["sub"]

    project_raw = await session.execute(
        sqlalchemy.select(UserProject).where(
            UserProject.id == project_id, UserProject.user_email == user_email
        )
    )

    project = project_raw.scalar_one_or_none()
    if project is None:
        return Response(status_code=404)
    return ProjectResponse.from_model(project)


@router.get("/api/projects/{project_id}/model-test")
@require_auth
async def model_test(
    request: Request, project_id: int, session: AsyncSession = Depends(get_db)
):
    """Return a project by ID for a given user"""
    user_email = request.state.user["sub"]

    project_raw = await session.execute(
        sqlalchemy.select(UserProject).where(
            UserProject.id == project_id, UserProject.user_email == user_email
        )
    )

    project = project_raw.scalar_one_or_none()
    if project is None:
        return Response(status_code=404)
    return project.update_hackatime()

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
        # last_updated=datetime.datetime.now(datetime.timezone.utc)
        # this should no longer need manual setting
    )

    try:
        session.add(new_project)
        await session.commit()
        await session.refresh(new_project)
        return JSONResponse(
            {
                "success": True,
                "project_info": ProjectResponse.from_model(new_project).model_dump(),
            }
        )
    except Exception as e:  # type: ignore # pylint: disable=broad-exception-caught
        await session.rollback()
        print(e)
        return Response(status_code=500)
