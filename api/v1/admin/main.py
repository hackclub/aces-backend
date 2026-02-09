"""Admin API routes"""

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.v1.auth import Permission, permission_dependency
from db import get_db
from models.main import Devlog, User, UserProject

router = APIRouter()


@router.get(
    "/analytics",
    dependencies=[Depends(permission_dependency(Permission.ADMIN))],
)
async def get_analytics(
    request: Request,  # noqa: ARG001
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return aggregate analytics for the admin dashboard."""

    # Users
    total_users = await session.scalar(select(func.count(User.id)))
    users_with_hackatime = await session.scalar(
        select(func.count(User.id)).where(User.hackatime_id.isnot(None))
    )
    total_cards_balance = await session.scalar(
        select(func.coalesce(func.sum(User.cards_balance), 0))
    )

    # Projects
    total_projects = await session.scalar(select(func.count(UserProject.id)))
    shipped_projects = await session.scalar(
        select(func.count(UserProject.id)).where(UserProject.shipped.is_(True))
    )
    total_hackatime_hours = await session.scalar(
        select(func.coalesce(func.sum(UserProject.hackatime_total_hours), 0))
    )

    # Devlogs
    total_devlogs = await session.scalar(select(func.count(Devlog.id)))
    pending_devlogs = await session.scalar(
        select(func.count(Devlog.id)).where(Devlog.state == "Pending")
    )
    approved_devlogs = await session.scalar(
        select(func.count(Devlog.id)).where(Devlog.state == "Approved")
    )
    rejected_devlogs = await session.scalar(
        select(func.count(Devlog.id)).where(Devlog.state == "Rejected")
    )
    total_cards_awarded = await session.scalar(
        select(func.coalesce(func.sum(Devlog.cards_awarded), 0))
    )

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
