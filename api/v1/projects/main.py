"""Projects API routes"""

# import asyncio
# import asyncpg
# import orjson
from datetime import datetime
from logging import error
from typing import List, Optional

import sqlalchemy
import validators
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, HttpUrl
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.v1.auth import require_auth  # type: ignore
from db import get_db  # , engine
from lib.hackatime import get_projects
from lib.ratelimiting import limiter
from models.main import User, UserProject

CDN_HOST = "hc-cdn.hel1.your-objectstorage.com"


class CreateProjectRequest(BaseModel):
    """Create project request from client"""

    project_name: str = Field(min_length=1, max_length=100)
    repo: Optional[HttpUrl] = None
    demo_url: Optional[HttpUrl] = None
    preview_image: Optional[HttpUrl] = None


class UpdateProjectRequest(BaseModel):
    """Update project request from client"""

    # project_id: int
    project_name: Optional[str] = Field(min_length=1, max_length=100)
    hackatime_projects: Optional[List[str]] = None
    repo: Optional[HttpUrl] = None
    demo_url: Optional[HttpUrl] = None
    preview_image: Optional[HttpUrl] = None

    class Config:
        """Pydantic config"""

        extra = "forbid"


class HackatimeProject(BaseModel):
    """Hackatime project linking request"""

    name: str = Field(min_length=1)


class ProjectResponse(BaseModel):
    """Public representation of a project"""

    project_id: int
    project_name: str
    hackatime_projects: List[str]
    hackatime_total_hours: float
    last_updated: datetime
    repo: Optional[str]
    demo_url: Optional[str]
    preview_image: Optional[str]
    shipped: bool

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
            repo=project.repo,
            demo_url=project.demo_url,
            preview_image=project.preview_image,
            shipped=project.shipped,
        )


router = APIRouter()
# @protect
# async def create_project(): ...


def validate_repo(repo: HttpUrl | None):
    """Validate repository URL against security criteria"""
    if not repo:
        raise HTTPException(status_code=400, detail="Repo url is missing")
    if not repo.host:
        raise HTTPException(status_code=400, detail="Repo url is missing host")
    if not validators.url(str(repo), private=False):
        raise HTTPException(
            status_code=400, detail="Repo url is not valid or is local/private"
        )
    if len(repo.host) > 256:
        raise HTTPException(
            status_code=400, detail="Repo url host exceeds the length limit"
        )
    return True


# @protect
@router.patch("/{project_id}")
@require_auth
async def update_project(
    request: Request,
    project_request: UpdateProjectRequest,
    project_id: int,
    session: AsyncSession = Depends(get_db),
):
    """Update project details"""

    user_email = request.state.user["sub"]

    project_raw = await session.execute(
        sqlalchemy.select(UserProject).where(
            UserProject.id == project_id,
            UserProject.user_email == user_email,
        )
    )

    project = project_raw.scalar_one_or_none()

    if project is None:
        raise HTTPException(status_code=404)  # if you get this good on you...?

    # Validate and update preview image if being updated
    if project_request.preview_image is not None:
        if (
            project_request.preview_image.host != CDN_HOST
            or project_request.preview_image.scheme != "https"
        ):
            raise HTTPException(
                status_code=400, detail="Image must be hosted on the Hack Club CDN"
            )
        project.preview_image = str(project_request.preview_image)

    # Validate and update demo URL if being updated
    if project_request.demo_url is not None:
        if not validators.url(str(project_request.demo_url), private=False):
            raise HTTPException(
                status_code=400, detail="Demo url is not valid or is local/private"
            )
        project.demo_url = str(project_request.demo_url)

    # Validate and update repo URL if being updated
    if project_request.repo is not None:
        validate_repo(project_request.repo)
        project.repo = str(project_request.repo)

    # Update project name
    if project_request.project_name is not None:
        project.name = project_request.project_name

    # Update hackatime projects
    if project_request.hackatime_projects is not None:
        project.hackatime_projects = project_request.hackatime_projects

    try:
        await session.commit()
        await session.refresh(project)
        return ProjectResponse.from_model(project)
    except Exception:  # type: ignore # pylint: disable=broad-exception-caught
        await session.rollback()
        return Response(status_code=500)


@router.get("/")
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


@router.get("/{project_id}")
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
        raise HTTPException(status_code=404, detail="Project not found")

    return ProjectResponse.from_model(project)


@router.post("/{project_id}/hackatime")
@limiter.limit("10/minute")  # type: ignore
@require_auth
async def link_hackatime_project(
    request: Request,
    project_id: int,
    hackatime_project: HackatimeProject,
    session: AsyncSession = Depends(get_db),
):
    """Link a Hackatime project to a user project"""
    user_email = request.state.user["sub"]

    project_raw = await session.execute(
        sqlalchemy.select(UserProject).where(
            UserProject.id == project_id, UserProject.user_email == user_email
        )
    )

    project = project_raw.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    if hackatime_project.name in project.hackatime_projects:
        raise HTTPException(
            status_code=400, detail="Hackatime project already linked to this project"
        )

    # Check if this Hackatime project is already linked to another ACES project for this user
    existing_link = await session.execute(
        sqlalchemy.select(UserProject).where(
            UserProject.user_email == user_email,
            UserProject.id != project_id,
            UserProject.hackatime_projects.contains([hackatime_project.name]),
        )
    )
    if existing_link.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=400,
            detail="This Hackatime project is already linked to another ACES project",
        )

    user_raw = await session.execute(
        sqlalchemy.select(User)
        .where(User.email == user_email)
        .options(selectinload(User.projects))
    )

    user = user_raw.scalar_one_or_none()
    if user is None or not user.hackatime_id:
        raise HTTPException(
            status_code=400, detail="User does not have a linked Hackatime ID"
        )

    try:
        user_projects = get_projects(
            user.hackatime_id, project.hackatime_projects + [hackatime_project.name]
        )
    except Exception as e:  # type: ignore # pylint: disable=broad-exception-caught
        error("Error fetching Hackatime projects:", exc_info=e)
        raise HTTPException(
            status_code=500, detail="Error fetching Hackatime projects"
        ) from e

    if user_projects == {}:
        raise HTTPException(status_code=400, detail="User has no Hackatime projects")

    if hackatime_project.name not in user_projects:
        raise HTTPException(
            status_code=400, detail="Hackatime project not found for this user"
        )

    project.hackatime_projects = project.hackatime_projects + [hackatime_project.name]

    values = user_projects.values()
    total_seconds = sum(v for v in values if v is not None)
    project.hackatime_total_hours = total_seconds / 3600.0  # convert to hours

    try:
        await session.commit()
        await session.refresh(project)
        return ProjectResponse.from_model(project)
    except Exception as e:  # type: ignore # pylint: disable=broad-exception-caught
        await session.rollback()
        error("Error linking Hackatime project:", exc_info=e)
        raise HTTPException(
            status_code=500, detail="Error linking Hackatime project"
        ) from e


@router.delete("/{project_id}/hackatime")
@limiter.limit("10/minute")  # type: ignore
@require_auth
async def unlink_hackatime_project(
    request: Request,
    project_id: int,
    hackatime_project: HackatimeProject,
    session: AsyncSession = Depends(get_db),
):
    """Unlink a Hackatime project from a user project"""
    user_email = request.state.user["sub"]

    project_raw = await session.execute(
        sqlalchemy.select(UserProject).where(
            UserProject.id == project_id, UserProject.user_email == user_email
        )
    )

    project = project_raw.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    if hackatime_project.name not in project.hackatime_projects:
        raise HTTPException(
            status_code=400, detail="Hackatime project not linked to this project"
        )

    user_raw = await session.execute(
        sqlalchemy.select(User)
        .options(selectinload(User.projects))
        .where(User.email == user_email)
    )

    user = user_raw.scalar_one_or_none()
    if user is None or not user.hackatime_id:
        raise HTTPException(
            status_code=400, detail="User does not have a linked Hackatime ID"
        )

    old_projects = project.hackatime_projects
    new_projects = [name for name in old_projects if name != hackatime_project.name]

    try:
        user_projects = get_projects(user.hackatime_id, new_projects)
    except Exception as e:  # type: ignore # pylint: disable=broad-exception-caught
        error("Error fetching Hackatime projects:", exc_info=e)
        raise HTTPException(
            status_code=500, detail="Error fetching Hackatime projects"
        ) from e

    values = user_projects.values()
    total_seconds = sum(v for v in values if v is not None)
    project.hackatime_total_hours = total_seconds / 3600.0  # convert to hours

    project.hackatime_projects = new_projects

    try:
        await session.commit()
        await session.refresh(project)
        return ProjectResponse.from_model(project)
    except Exception as e:  # type: ignore # pylint: disable=broad-exception-caught
        await session.rollback()
        error("Error unlinking Hackatime project:", exc_info=e)
        raise HTTPException(
            status_code=500, detail="Error unlinking Hackatime project"
        ) from e


@router.post("/")
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

    # Validate preview image
    if project_create_request.preview_image is not None:
        if (
            project_create_request.preview_image.host != CDN_HOST
            or project_create_request.preview_image.scheme != "https"
        ):
            raise HTTPException(
                status_code=400, detail="image must be hosted on the Hack Club CDN"
            )

    # Validate demo URL
    if project_create_request.demo_url is not None:
        if not validators.url(str(project_create_request.demo_url), private=False):
            raise HTTPException(
                status_code=400, detail="demo url is not valid or is local/private"
            )

    # Validate repo URL
    if project_create_request.repo is not None:
        validate_repo(project_create_request.repo)

    new_project = UserProject(
        name=project_create_request.project_name,
        user_email=user_email,
        hackatime_projects=[],
        hackatime_total_hours=0.0,
        repo=(
            str(project_create_request.repo)
            if project_create_request.repo is not None
            else None
        ),
        demo_url=(
            str(project_create_request.demo_url)
            if project_create_request.demo_url is not None
            else None
        ),
        preview_image=(
            str(project_create_request.preview_image)
            if project_create_request.preview_image is not None
            else None
        ),
        # last_updated=datetime.datetime.now(datetime.timezone.utc)
        # this should no longer need manual setting
    )

    try:
        session.add(new_project)
        await session.commit()
        await session.refresh(new_project)
        return ProjectResponse.from_model(new_project)
    except Exception as e:  # type: ignore # pylint: disable=broad-exception-caught
        await session.rollback()
        error("Error creating new project:", exc_info=e)
        raise HTTPException(status_code=500, detail="Error creating new project") from e


@router.post("/{project_id}/ship")
@limiter.limit("30/minute")  # type: ignore
@require_auth
async def ship_project(
    request: Request,
    project_id: int,
    session: AsyncSession = Depends(get_db),
):
    """Mark a project as shipped"""
    user_email = request.state.user["sub"]

    proj_raw = await session.execute(
        sqlalchemy.select(UserProject).where(
            UserProject.id == project_id,
            UserProject.user_email == user_email,
        )
    )

    proj = proj_raw.scalar_one_or_none()

    if proj is None:
        raise HTTPException(status_code=404, detail="Project not found")

    if proj.shipped:
        raise HTTPException(status_code=400, detail="Project already shipped")

    proj.shipped = True

    try:
        await session.commit()
        await session.refresh(proj)
        return ProjectResponse.from_model(proj)
    except Exception as e:
        await session.rollback()
        error("Error marking project as shipped:", exc_info=e)
        raise HTTPException(status_code=500, detail="Error shipping project") from e
