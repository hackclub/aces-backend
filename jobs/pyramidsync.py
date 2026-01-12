import asyncio
import logging
import os

from pyairtable import Api
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from db.main import get_session
from models.main import User

logger = logging.getLogger(__name__)


async def sync_users_to_airtable():
    """Push user data to Pyramid Scheme Airtable table"""
    table_id = os.getenv("AIRTABLE_PYRAMID_TABLE_ID")
    api_key = os.getenv("AIRTABLE_PYRAMID_API_KEY")
    base_id = os.getenv("AIRTABLE_BASE_ID")
    if not all([table_id, api_key, base_id]):
        logger.warning(
            "Missing Airtable config: table_id=%s, api_key=%s, base_id=%s",
            bool(table_id),
            bool(api_key),
            bool(base_id),
        )
        return

    api = Api(api_key)  # type: ignore
    table = api.table(base_id, table_id)  # type: ignore

    # Fetch all users with their projects
    async with get_session() as session:
        users = (
            (await session.execute(select(User).options(selectinload(User.projects))))
            .scalars()
            .all()
        )

        records: list[dict[str, dict[str, str | int | float]]] = []
        for user in users:
            records.append(
                {
                    "fields": {
                        "Email": user.email,
                        "Hours": round(
                            sum(p.hackatime_total_hours for p in user.projects), 2
                        ),
                        "Projects Shipped": sum(1 for p in user.projects if p.shipped),
                        "IDV Status": user.idv_status or "",
                        "Referral Code": user.referral_code_used or "",
                    }
                }
            )

        logger.info("Syncing %d users to Airtable", len(records))
        if not records:
            logger.warning("No records to sync")
            return

        try:
            await asyncio.to_thread(table.batch_upsert, records, key_fields=["Email"])
            logger.info("Airtable sync complete")
        except Exception:
            logger.exception("Airtable batch sync failed")
