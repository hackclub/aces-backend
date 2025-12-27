from datetime import datetime, timezone
from sqlalchemy import select
from db.main import get_session
from models.main import User


async def cleanup_deleted_users() -> int:
    """Find users past their deletion date and permanently delete. Returns # of users deleted"""
    async with get_session() as session:
        now = datetime.now(timezone.utc)

        query = select(User).where(
            User.marked_for_deletion.is_(True), User.date_for_deletion <= now
        )

        result = await session.execute(query)
        users_to_delete = result.scalars().all()

        if not users_to_delete:
            return 0

        deleted_count = 0
        for user in users_to_delete:
            await session.delete(
                user
            )  # sqlalchemy automatically cascades so we js have to delete this record
            deleted_count += 1

        await session.commit()
        return deleted_count
