import asyncio
import asyncpg
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
import sqlalchemy
from fastapi import APIRouter, Request, Depends, HTTPException
from api.auth import require_auth
from db import get_db, engine
from pydantic import BaseModel
from models.user import User, UserProject
import orjson
import datetime

class CreateProjectRequest(BaseModel):
    project_name: str

router = APIRouter()

#@limiter.limit("3/day")
async def create_user(): ... 

#@protect
async def update_user(): ... 

#@protect
async def get_user(): ...

#@protect
# async def create_project(): ...

#@protect
async def update_project(): ...

@router.get("/api/users/return_projects")
@require_auth
async def return_projects(request: Request, session: AsyncSession = Depends(get_db)): 
    user_email = request.state.user["sub"]
    user_raw = await session.execute(sqlalchemy.select(User).options(selectinload(User.projects)).where(User.email == user_email))
    user = user_raw.scalar_one_or_none()
    projects = user.projects if user else [] # this should never invoke the else unless something has gone very bad
    projects_ret = [project.__dict__ for project in projects]
    return projects_ret

@router.post("/api/users/create_project")
@require_auth
async def create_project(request: Request, project_create_request: CreateProjectRequest, session: AsyncSession = Depends(get_db)):
    user_email = request.state.user["sub"]
    user_raw = await session.execute(sqlalchemy.select(User).where(User.email == user_email))
    user = user_raw.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401) # if the user hasn't been created yet they shouldn't be authed
    
    new_project = UserProject(
        name=project_create_request.project_name,
        user_email=user_email,
        hackatime_projects=[],
        hackatime_total_hours=0.0,
        last_updated=datetime.datetime.now(datetime.timezone.utc)
    )

    session.add(new_project)
    await session.commit()
    await session.refresh(new_project)

    return {"success": True}



#@protect
async def delete_user(): ... # can only delete their own user!!! please don't let them delete other users!!!
# disabled for 30 days, no login -> delete

#@protect
async def is_pending_deletion(): ...

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