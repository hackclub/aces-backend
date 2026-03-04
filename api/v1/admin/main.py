"""Admin API routes"""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.v1.auth import Permission, permission_dependency
from db import get_db
from lib.responses import SimpleResponse
from models.main import Devlog, User, UserProject

router = APIRouter()


class AdminUserResponse(BaseModel):
    id: int
    email: str
    username: Optional[str]
    permissions: list[int]
    cards_balance: int
    marked_for_deletion: bool
    project_count: int


class PermissionsRequest(BaseModel):
    permissions: list[int]


class CardAdjustmentRequest(BaseModel):
    amount: int
    reason: str


@router.get(
    "/analytics",
    dependencies=[Depends(permission_dependency(Permission.ADMIN))],
)
async def get_analytics(
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return aggregate analytics for the admin dashboard."""

    user_row = (
        await session.execute(
            select(
                func.count(User.id),
                func.count(User.hackatime_id),
                func.coalesce(func.sum(User.cards_balance), 0),
            )
        )
    ).one()
    total_users, users_with_hackatime, total_cards_balance = user_row

    project_row = (
        await session.execute(
            select(
                func.count(UserProject.id),
                func.count(UserProject.id).filter(UserProject.shipped.is_(True)),
                func.coalesce(func.sum(UserProject.hackatime_total_hours), 0),
            )
        )
    ).one()
    total_projects, shipped_projects, total_hackatime_hours = project_row

    devlog_row = (
        await session.execute(
            select(
                func.count(Devlog.id),
                func.count(Devlog.id).filter(Devlog.state == "Pending"),
                func.count(Devlog.id).filter(Devlog.state == "Approved"),
                func.count(Devlog.id).filter(Devlog.state == "Rejected"),
                func.coalesce(func.sum(Devlog.cards_awarded), 0),
            )
        )
    ).one()
    (
        total_devlogs,
        pending_devlogs,
        approved_devlogs,
        rejected_devlogs,
        total_cards_awarded,
    ) = devlog_row

    return {
        "users": {
            "total": total_users or 0,
            "with_hackatime": users_with_hackatime or 0,
            "total_cards_balance": total_cards_balance or 0,
        },
        "projects": {
            "total": total_projects or 0,
            "shipped": shipped_projects or 0,
            "total_hackatime_hours": round(float(total_hackatime_hours or 0), 1),
        },
        "devlogs": {
            "total": total_devlogs or 0,
            "pending": pending_devlogs or 0,
            "approved": approved_devlogs or 0,
            "rejected": rejected_devlogs or 0,
            "total_cards_awarded": total_cards_awarded or 0,
        },
    }


@router.get(
    "/users",
    dependencies=[Depends(permission_dependency(Permission.ADMIN))],
)
async def list_users(
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_db),
) -> list[AdminUserResponse]:
    """List/search users with project counts."""
    limit = min(limit, 100)

    query = (
        select(
            User.id,
            User.email,
            User.username,
            User.permissions,
            User.cards_balance,
            User.marked_for_deletion,
            func.count(UserProject.id).label("project_count"),
        )
        .outerjoin(UserProject, User.email == UserProject.user_email)
        .group_by(User.id)
    )

    if q:
        pattern = f"%{q}%"
        query = query.where(User.email.ilike(pattern) | User.username.ilike(pattern))

    query = query.order_by(User.id).offset(offset).limit(limit)
    result = await session.execute(query)
    rows = result.all()

    return [
        AdminUserResponse(
            id=row.id,
            email=row.email,
            username=row.username,
            permissions=row.permissions or [],
            cards_balance=row.cards_balance,
            marked_for_deletion=row.marked_for_deletion,
            project_count=row.project_count,
        )
        for row in rows
    ]


@router.post(
    "/users/{user_id}/deactivate",
    dependencies=[Depends(permission_dependency(Permission.ADMIN))],
)
async def deactivate_user(
    user_id: int,
    session: AsyncSession = Depends(get_db),
) -> AdminUserResponse:
    """Deactivate a user (mark for deletion in 30 days)."""
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.marked_for_deletion:
        raise HTTPException(status_code=400, detail="User is already deactivated")

    user.marked_for_deletion = True
    user.date_for_deletion = datetime.now(timezone.utc) + timedelta(days=30)
    await session.commit()
    await session.refresh(user)

    project_count_result = await session.execute(
        select(func.count(UserProject.id)).where(UserProject.user_email == user.email)
    )
    project_count = project_count_result.scalar() or 0

    return AdminUserResponse(
        id=user.id,
        email=user.email,
        username=user.username,
        permissions=user.permissions or [],
        cards_balance=user.cards_balance,
        marked_for_deletion=user.marked_for_deletion,
        project_count=project_count,
    )


@router.post(
    "/users/{user_id}/reactivate",
    dependencies=[Depends(permission_dependency(Permission.ADMIN))],
)
async def reactivate_user(
    user_id: int,
    session: AsyncSession = Depends(get_db),
) -> AdminUserResponse:
    """Reactivate a deactivated user."""
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.marked_for_deletion:
        raise HTTPException(status_code=400, detail="User is not deactivated")

    user.marked_for_deletion = False
    user.date_for_deletion = None
    await session.commit()
    await session.refresh(user)

    project_count_result = await session.execute(
        select(func.count(UserProject.id)).where(UserProject.user_email == user.email)
    )
    project_count = project_count_result.scalar() or 0

    return AdminUserResponse(
        id=user.id,
        email=user.email,
        username=user.username,
        permissions=user.permissions or [],
        cards_balance=user.cards_balance,
        marked_for_deletion=user.marked_for_deletion,
        project_count=project_count,
    )


@router.put(
    "/users/{user_id}/permissions",
    dependencies=[Depends(permission_dependency(Permission.ADMIN))],
)
async def set_user_permissions(
    user_id: int,
    body: PermissionsRequest,
    session: AsyncSession = Depends(get_db),
) -> AdminUserResponse:
    """Set a user's permissions."""
    valid_values = {p.value for p in Permission}
    for perm in body.permissions:
        if perm not in valid_values:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid permission value: {perm}",
            )

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.permissions = body.permissions
    await session.commit()
    await session.refresh(user)

    project_count_result = await session.execute(
        select(func.count(UserProject.id)).where(UserProject.user_email == user.email)
    )
    project_count = project_count_result.scalar() or 0

    return AdminUserResponse(
        id=user.id,
        email=user.email,
        username=user.username,
        permissions=user.permissions or [],
        cards_balance=user.cards_balance,
        marked_for_deletion=user.marked_for_deletion,
        project_count=project_count,
    )


@router.post(
    "/users/{user_id}/cards",
    dependencies=[Depends(permission_dependency(Permission.ADMIN))],
)
async def adjust_card_balance(
    user_id: int,
    body: CardAdjustmentRequest,
    session: AsyncSession = Depends(get_db),
) -> SimpleResponse:
    """Adjust a user's card balance."""
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    new_balance = user.cards_balance + body.amount
    if new_balance < 0:
        raise HTTPException(
            status_code=400,
            detail="Adjustment would result in negative balance",
        )

    user.cards_balance = new_balance
    await session.commit()

    return SimpleResponse(success=True)
